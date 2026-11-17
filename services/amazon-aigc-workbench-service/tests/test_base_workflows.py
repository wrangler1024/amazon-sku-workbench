import copy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from psycopg.types.json import Jsonb

from amazon_aigc.base_workflows import EVENT_TYPE, Coordinator
from amazon_aigc.native_rules import RuleError, Rules

FIELDS = [
    "审核状态",
    "提交校验状态",
    "插件提交ID",
    "提交运营",
    "最近提交审核时间",
    "首次审核通过时间",
    "ERP录入状态",
    "制图状态",
    "制图截止日期",
    "制图承接方",
    "美工",
    "提审公式核对",
    "评审路径",
    "运营小组",
    "撤回请求ID",
    "撤回处理ID",
]
AT = datetime(2026, 1, 2, 9, tzinfo=UTC)
RID = "recSynthetic01"
OWNER = [{"id": "on_syntheticoperator", "name": "Synthetic operator"}]


def ref(name):
    return {"value_type": "ref", "value": "$.tr.f" + str(FIELDS.index(name))}


def text(s):
    return {"value_type": "text", "value": s}


def cond(name, v):
    return {"field_name": name, "operator": "is", "value": [text(v)]}


def config(kind="route"):
    if kind == "route":
        trigger = {"condition_list": [{"conditions": [cond("提交校验状态", "通过"), cond("审核状态", "草稿")]}]}
        tr_type = "ChangeRecordTrigger"
        patch = [{"field_name": "审核状态", "value": [text("待审核")]}]
    elif kind == "formula":
        trigger = {"condition_list": [{"conditions": [cond("提交校验状态", "校验中"), cond("提审公式核对", "1")]}]}
        tr_type = "ChangeRecordTrigger"
        patch = [{"field_name": "提交校验状态", "value": [text("通过")]}]
    elif kind == "approval":
        trigger = {"field_watch_info": [cond("审核状态", "审核通过")]}
        tr_type = "SetRecordTrigger"
        patch = [{"field_name": "ERP录入状态", "value": [text("待录入")]}]
    elif kind == "withdraw":
        trigger = {"field_watch_info": [{"field_name": "撤回请求ID"}]}
        tr_type = "SetRecordTrigger"
        patch = [{"field_name": "审核状态", "value": [text("草稿")]}, {"field_name": "插件提交ID", "value": []}]
    elif kind == "draft":
        trigger = {"condition_list": [{"conditions": [
            {"field_name": "审核状态", "operator": "isEmpty", "value": []},
            cond("提交校验状态", "未提交"),
            {"field_name": "插件提交ID", "operator": "isEmpty", "value": []},
        ]}]}
        tr_type = "ChangeRecordTrigger"
        patch = [{"field_name": "审核状态", "value": [text("草稿")]}]
    else:
        trigger = {"field_name": "制图截止日期", "offset": -1, "hour": 9, "minute": 0}
        tr_type = "ReminderTrigger"
        patch = []
    trigger["table_name"] = "Records"
    steps = [{"id": "tr", "type": tr_type, "next": "write" if patch else "notice", "data": trigger}]
    if patch:
        steps.append(
            {
                "id": "write",
                "type": "SetRecordAction",
                "next": None if kind == "draft" else "notice",
                "data": {"table_name": "Records", "ref_info": {"step_id": "tr"}, "field_values": patch},
            }
        )
    steps.append(
        {
            "id": "notice",
            "type": "LarkMessageAction",
            "data": {
                "receiver": [ref("提交运营")],
                "title": [text("Synthetic notice")],
                "content": [text("Synthetic body")],
            },
        }
    )
    if kind == "draft":
        steps.pop()
    return {
        "base_id": "syntheticBase",
        "record_table": "Records",
        "base_origin": "https://example.invalid",
        "timezone": "UTC",
        "tables": [
            {
                "id": "syntheticTable",
                "name": "Records",
                "fields": [
                    {"id": "f" + str(i), "name": n, "type": "formula" if n == "提审公式核对" else "text"}
                    for i, n in enumerate(FIELDS)
                ],
            }
        ],
        "writable_fields": FIELDS,
        "workflows": [{"workflow_id": "syntheticWorkflow", "steps": steps}],
    }


class Gateway:
    def __init__(self, row):
        self.row = copy.deepcopy(row)
        self.s = SimpleNamespace(
            base_id="syntheticBase", sku_table_id="syntheticTable", feishu_app_id="syntheticApp", tenant="synthetic"
        )
        self.writes, self.sent, self.fail_notices = [], [], 0
        self.after_write_error = False

    def records(self, table, fields=None):
        return [copy.deepcopy(self.row)] if self.row else []

    def record(self, rid):
        return copy.deepcopy(self.row)

    def is_workflow_actor(self, identity):
        return identity.get("open_id") == "ou_syntheticbot"

    def workflow_update(self, rid, patch):
        self.writes.append(copy.deepcopy(patch))
        self.row.update(patch)
        if self.after_write_error:
            self.after_write_error = False
            raise ConnectionError("Synthetic timeout after external write")

    def send_notice(self, recipient, payload, message_uuid):
        self.sent.append((recipient, payload, message_uuid))
        if self.fail_notices:
            self.fail_notices -= 1
            raise ConnectionError("Synthetic delivery failure")
        return "syntheticMessage"


@pytest.fixture
def coordinator(db):
    with db.connection() as c:
        c.execute(
            "TRUNCATE base_workflow_notices,base_workflow_operations,base_workflow_runs,base_workflow_events,base_workflow_records"
        )
        c.execute("UPDATE base_workflow_control SET enabled=false,seeded_at=NULL,config_hash=NULL")
    row = {
        "record_id": RID,
        "审核状态": "草稿",
        "提交校验状态": "通过",
        "插件提交ID": "selection-v1-recSynthetic01-" + str(int(AT.timestamp() * 1000)),
        "提交运营": OWNER,
    }
    g = Gateway(row)
    return Coordinator(db, g, config(), now=lambda: AT + timedelta(minutes=10))


def drain(q):
    for _ in range(50):
        if not q.run_one():
            return
    raise AssertionError("Unbounded cascade")


def enqueue(q, changed=None, actor=OWNER):
    with q.db.connection() as c:
        q.dispatch(c, q.gateway.row, {}, actor, AT, "synthetic-event", changed or {"审核状态"})


def event(q, revision=1, identifier="synthetic-event-0001"):
    return {
        "header": {"event_type": EVENT_TYPE, "app_id": q.gateway.s.feishu_app_id, "event_id": identifier},
        "event": {
            "file_token": q.gateway.s.base_id,
            "table_id": q.gateway.s.sku_table_id,
            "update_time": int(AT.timestamp()),
            "revision": revision,
            "operator_id": {"union_id": OWNER[0]["id"]},
            "action_list": [
                {
                    "record_id": RID,
                    "action": "record_edited",
                    "after_value": [{"field_id": "f1", "field_value": '"通过"'}],
                }
            ],
        },
    }


def test_numeric_formula_operand_and_identifiers():
    r = Rules(config())
    assert r.compare(1, "is", "1")
    assert r.compare(0, "isNot", "1")
    assert not r.compare("001", "is", "1")


def test_reject_unreviewed_graph():
    cfg = config()
    cfg["workflows"][0]["steps"][1]["type"] = "HTTPClientAction"
    with pytest.raises(RuleError):
        Rules(cfg)


def test_duplicate_event_and_wrong_target(coordinator):
    q = coordinator
    bad = event(q)
    bad["header"]["app_id"] = "differentApp"
    assert not q.ingest(bad)
    assert q.ingest(event(q)) and q.ingest(event(q))
    with q.db.connection() as c:
        assert c.execute("SELECT count(*) AS n FROM base_workflow_events").fetchone()["n"] == 1


def test_route_once_and_preserve_original_time(coordinator):
    q = coordinator
    enqueue(q)
    drain(q)
    assert q.gateway.row["审核状态"] == "待审核"
    assert q.gateway.row["最近提交审核时间"] == int(AT.timestamp() * 1000)
    enqueue(q)
    drain(q)
    assert len(q.gateway.writes) == 1
    assert q.notify_one()
    assert not q.notify_one()
    assert len(q.gateway.sent) == 1


@pytest.mark.parametrize("offset_ms", [16829, 1000, -1000])
def test_route_corrects_submission_time_in_either_direction(coordinator, offset_ms):
    q = coordinator
    q.gateway.row["最近提交审核时间"] = int(AT.timestamp() * 1000) + offset_ms
    enqueue(q)
    drain(q)
    assert q.gateway.row["最近提交审核时间"] == int(AT.timestamp() * 1000)
    assert q.gateway.row["审核状态"] == "待审核"
    assert q.notify_one() and not q.notify_one()
    enqueue(q)
    drain(q)
    assert len(q.gateway.writes) == len(q.gateway.sent) == 1


@pytest.mark.parametrize("future_version", [False, True])
def test_submission_start_time_uses_committed_version_not_delivery_time(coordinator, future_version):
    cfg = config("formula")
    steps = cfg["workflows"][0]["steps"]
    steps[0]["type"] = "SetRecordTrigger"
    steps[0]["data"] = {"table_name": "Records", "field_watch_info": [cond("提交校验状态", "校验中")]}
    steps[1]["data"]["field_values"] = [
        {"field_name": "最近提交审核时间", "value": [{"value_type": "date", "value": "now"}]},
    ]
    steps[1]["next"] = None
    steps.pop()
    q = Coordinator(coordinator.db, coordinator.gateway, cfg, now=coordinator.now)
    q.gateway.row["提交校验状态"] = "校验中"
    delivery = AT + timedelta(seconds=17)
    if future_version:
        q.gateway.row["插件提交ID"] = "selection-v1-recSynthetic01-" + str(int((delivery + timedelta(seconds=10)).timestamp() * 1000))
    with q.db.connection() as c:
        q.dispatch(c, q.gateway.row, {}, OWNER, delivery, "synthetic-delayed-event", {"提交校验状态"})
    drain(q)
    if future_version:
        assert not q.gateway.writes
        with q.db.connection() as c:
            assert "future" in c.execute("SELECT error FROM base_workflow_runs").fetchone()["error"]
    else:
        assert q.gateway.row["最近提交审核时间"] == int(AT.timestamp() * 1000)
        assert q.gateway.row["审核状态"] == "草稿"
    assert not q.notify_one()


def test_stale_version_has_no_write_or_notice(coordinator):
    q = coordinator
    enqueue(q)
    q.gateway.row["插件提交ID"] = "newer-version"
    drain(q)
    assert not q.gateway.writes
    assert not q.notify_one()


def test_newer_decision_stops_route_and_notice(coordinator):
    q = coordinator
    enqueue(q)
    q.gateway.row["审核状态"] = "拒绝"
    drain(q)
    assert not q.gateway.writes
    assert not q.notify_one()


def test_notice_survives_unrelated_erp_change(coordinator):
    q = coordinator
    enqueue(q)
    drain(q)
    q.gateway.row["ERP录入状态"] = "已录入"
    assert q.notify_one()
    assert len(q.gateway.sent) == 1


def test_notice_retry_uses_stable_uuid(coordinator):
    q = coordinator
    enqueue(q)
    drain(q)
    q.gateway.fail_notices = 1
    assert q.notify_one()
    with q.db.connection() as c:
        c.execute("UPDATE base_workflow_notices SET next_attempt_at=now()")
    assert q.notify_one()
    assert q.gateway.sent[0][2] == q.gateway.sent[1][2]
    assert not q.notify_one()


def test_notice_suppressed_after_withdrawal(coordinator):
    q = coordinator
    enqueue(q)
    drain(q)
    q.gateway.row["审核状态"] = "草稿"
    assert q.notify_one()
    assert not q.gateway.sent


def test_write_timeout_resumes_without_rewriting(coordinator):
    q = coordinator
    enqueue(q)
    q.gateway.after_write_error = True
    drain(q)
    with q.db.connection() as c:
        c.execute("UPDATE base_workflow_runs SET next_attempt_at=now()")
    drain(q)
    assert len(q.gateway.writes) == 1
    assert q.notify_one()


def test_formula_poll_handles_event_already_showing_formula(coordinator):
    q = coordinator
    q = Coordinator(q.db, q.gateway, config("formula"), now=q.now)
    q.gateway.row.update({"提交校验状态": "校验中", "提审公式核对": 1})
    with q.db.connection() as c:
        c.execute("INSERT INTO base_workflow_records(record_id,snapshot) VALUES(%s,%s)", (RID, Jsonb(q.gateway.row)))
    q.poll()
    drain(q)
    assert q.gateway.row["提交校验状态"] == "通过"


def test_newer_watched_state_prevents_approval_side_effect(coordinator):
    q = coordinator
    q = Coordinator(q.db, q.gateway, config("approval"), now=q.now)
    q.gateway.row["审核状态"] = "审核通过"
    enqueue(q)
    q.gateway.row["审核状态"] = "打回修改"
    drain(q)
    assert not q.gateway.writes
    assert not q.notify_one()


def test_clear_text_field_uses_null(coordinator):
    q = coordinator
    q = Coordinator(q.db, q.gateway, config("withdraw"), now=q.now)
    q.gateway.row["撤回请求ID"] = "synthetic-request"
    enqueue(q, {"撤回请求ID"})
    drain(q)
    assert q.gateway.row["插件提交ID"] is None
    assert q.gateway.row["审核状态"] == "草稿"


def test_seed_never_replays_historical_records(coordinator):
    q = coordinator
    q.gateway.row["审核状态"] = "审核通过"
    assert q.seed()["seeded_records"] == 1
    assert not q.run_one()
    assert not q.gateway.sent
    with pytest.raises(ValueError):
        q.seed()


@pytest.mark.parametrize("creation_event", [True, False])
def test_new_unsubmitted_draft_survives_refresh_before_event(coordinator, creation_event):
    import json

    q = Coordinator(coordinator.db, coordinator.gateway, config("draft"), now=coordinator.now)
    q.gateway.row.update({"审核状态": None, "提交校验状态": "未提交", "插件提交ID": None})
    q.seed()
    assert not q.routing(q.rules.workflows["syntheticWorkflow"])
    if creation_event:
        payload = event(q)
        action = payload["event"]["action_list"][0]
        action["action"] = "record_added"
        action["after_value"] = [{"field_id": "f1", "field_value": json.dumps("未提交")}]
        assert q.ingest(payload) and q.process_event()
    else:
        q.reconcile()
    drain(q)
    q.reconcile()
    drain(q)
    assert q.gateway.row["审核状态"] == "草稿"
    assert q.gateway.row["插件提交ID"] is None
    assert q.gateway.row.get("最近提交审核时间") is None
    assert q.gateway.writes == [{"审核状态": "草稿"}]
    assert not q.notify_one()


def test_reminder_dedup_and_fresh_deadline_guard(coordinator):
    q = coordinator
    q = Coordinator(q.db, q.gateway, config("reminder"), now=lambda: AT)
    q.gateway.row["制图截止日期"] = int((AT + timedelta(days=1)).timestamp() * 1000)
    with q.db.connection() as c:
        c.execute("INSERT INTO base_workflow_records(record_id,snapshot) VALUES(%s,%s)", (RID, Jsonb(q.gateway.row)))
    q.reminders()
    q.reminders()
    drain(q)
    q.gateway.row["制图截止日期"] += 86400000
    assert q.notify_one()
    assert not q.gateway.sent
    with q.db.connection() as c:
        assert c.execute("SELECT count(*) AS n FROM base_workflow_runs").fetchone()["n"] == 1


def test_event_option_ids_are_normalized(coordinator):
    q = coordinator
    q.rules.tables["Records"]["fields"][1]["type"] = "select"
    q.rules.option_names["syntheticOption"] = "校验中"
    decoded = q.decode_fields([{"field_id": "f1", "field_value": '"syntheticOption"'}])
    assert decoded == {"提交校验状态": "校验中"}
    assert q.decode_fields([{"field_id": "f1", "field_value": None, "field_identity_value": None}]) == {
        "提交校验状态": None
    }


@pytest.mark.parametrize("real_decision", [False, True])
def test_full_snapshot_only_authorizes_changed_review_decisions(coordinator, real_decision):
    import json

    q = Coordinator(coordinator.db, coordinator.gateway, config("approval"), now=coordinator.now)
    q.gateway.row["审核状态"] = "审核通过"
    q.seed()
    payload = event(q)
    action = payload["event"]["action_list"][0]
    action["before_value"] = [
        {"field_id": "f0", "field_value": json.dumps("草稿" if real_decision else "审核通过")},
        {"field_id": "f7", "field_value": json.dumps("制图中")},
    ]
    action["after_value"] = [
        {"field_id": "f0", "field_value": json.dumps("审核通过")},
        {"field_id": "f7", "field_value": json.dumps("待验收")},
    ]
    q.gateway.row["制图状态"] = "待验收"
    authorized = []
    q.authorize_decision = lambda row, actor: authorized.append(actor)
    assert q.ingest(payload) and q.process_event()
    assert authorized == ([OWNER[0]["id"]] if real_decision else [])
    drain(q)
    assert q.gateway.row.get("ERP录入状态") == ("待录入" if real_decision else None)
    assert q.notify_one() == real_decision


def test_null_event_serial_does_not_block_current_submission(coordinator):
    cfg = config()
    cfg["tables"][0]["fields"].append({"id": "synthetic_serial", "name": "记录序号", "type": "auto_number"})
    cfg["workflows"][0]["steps"][1]["data"]["filter_info"] = {
        "conditions": [{"field_name": "记录序号", "operator": "is", "value": [
            {"value_type": "ref", "value": "$.tr.synthetic_serial"},
        ]}],
    }
    q = Coordinator(coordinator.db, coordinator.gateway, cfg, now=coordinator.now)
    q.gateway.row["记录序号"] = "SYN-001"
    payload = event(q)
    payload["event"]["action_list"][0]["after_value"].append(
        {"field_id": "synthetic_serial", "field_value": "null"}
    )
    assert q.ingest(payload) and q.process_event()
    drain(q)
    assert q.gateway.row["审核状态"] == "待审核"
    assert q.notify_one()


@pytest.mark.parametrize("count", [0, 1])
def test_formula_cell_wrapper_is_unwrapped_for_numeric_write(coordinator, count):
    cfg = config()
    cfg["tables"][0]["fields"].extend([
        {"id": "synthetic_formula", "name": "Next count", "type": "formula"},
        {"id": "synthetic_count", "name": "Count", "type": "number"},
    ])
    cfg["writable_fields"].append("Count")
    cfg["workflows"][0]["steps"][1]["data"]["field_values"] = [
        {"field_name": "Count", "value": [{"value_type": "ref", "value": "$.tr.synthetic_formula"}]},
    ]
    q = Coordinator(coordinator.db, coordinator.gateway, cfg, now=coordinator.now)
    q.gateway.row["Next count"] = {"type": 2, "value": [count]}
    enqueue(q)
    drain(q)
    assert q.gateway.row["Count"] == count
    assert q.gateway.writes == [{"Count": count}]


@pytest.mark.parametrize("own_bot", [True, False])
def test_only_the_application_actor_can_echo_an_applied_approval(coordinator, own_bot):
    import json

    q = Coordinator(coordinator.db, coordinator.gateway, config("approval"), now=coordinator.now)
    q.gateway.row["审核状态"] = "审核通过"
    q.seed()
    with q.db.connection() as c:
        key = q.queue(c, q.rules.workflows["syntheticWorkflow"], q.gateway.row, [], AT, "synthetic-operation", finished=True)
        c.execute(
            "INSERT INTO base_workflow_operations(operation_key,run_key,record_id,patch,before_snapshot,state) "
            "VALUES('synthetic-echo-operation',%s,%s,%s,%s,'applied')",
            (key, RID, Jsonb({"审核状态": "审核通过"}), Jsonb({})),
        )
    payload = event(q)
    payload["event"]["operator_id"] = {
        "open_id": "ou_syntheticbot" if own_bot else "ou_synthetichuman",
        "union_id": "on_syntheticbot" if own_bot else OWNER[0]["id"],
    }
    action = payload["event"]["action_list"][0]
    action["before_value"] = [{"field_id": "f0", "field_value": json.dumps("草稿")}]
    action["after_value"] = [{"field_id": "f0", "field_value": json.dumps("审核通过")}]
    assert q.ingest(payload)
    assert q.process_event() == own_bot
    with q.db.connection() as c:
        result = c.execute("SELECT state,error FROM base_workflow_events").fetchone()
    assert result["state"] == ("succeeded" if own_bot else "queued")
    assert (result["error"] or "").startswith("PermissionError") == (not own_bot)
    assert not q.run_one() and not q.notify_one()


def test_human_self_review_and_unverified_actor_are_rejected(coordinator):
    with pytest.raises(PermissionError):
        coordinator.authorize_decision(coordinator.gateway.row, OWNER[0]["id"])
    with pytest.raises(PermissionError):
        coordinator.authorize_decision(coordinator.gateway.row, None)


def test_verified_supervisor_group_and_self_review(coordinator):
    q = coordinator
    from uuid import uuid4

    uid, subject = uuid4(), "on_" + uuid4().hex
    with q.db.connection() as c:
        c.execute(
            "INSERT INTO users(id,name,roles,groups,active,internal,verified_at) VALUES(%s,'Synthetic supervisor',%s,%s,true,true,now())",
            (uid, ["supervisor"], ["Synthetic group"]),
        )
        c.execute(
            "INSERT INTO external_identities(provider,tenant,subject,user_id) VALUES('feishu_union','synthetic',%s,%s)",
            (subject, uid),
        )
    q.gateway.row["运营小组"] = "Synthetic group"
    q.authorize_decision(q.gateway.row, subject)
    q.gateway.row["运营小组"] = "Other group"
    with pytest.raises(PermissionError):
        q.authorize_decision(q.gateway.row, subject)
    q.gateway.row["提交运营"] = [{"id": subject}]
    with pytest.raises(PermissionError):
        q.authorize_decision(q.gateway.row, subject)


def test_timeout_recovery_preserves_cascade_event(coordinator):
    q = coordinator
    cfg = config()
    downstream = config("approval")["workflows"][0]
    downstream["workflow_id"] = "syntheticDownstream"
    downstream["steps"][0]["data"]["field_watch_info"] = [cond("审核状态", "待审核")]
    cfg["workflows"].append(downstream)
    q = Coordinator(q.db, q.gateway, cfg, now=q.now)
    enqueue(q)
    q.gateway.after_write_error = True
    drain(q)
    with q.db.connection() as c:
        c.execute("UPDATE base_workflow_runs SET next_attempt_at=now()")
    drain(q)
    assert q.gateway.row["ERP录入状态"] == "待录入"
    assert len(q.gateway.writes) == 2


def test_handover_invalidates_old_pending_work(coordinator):
    q = coordinator
    enqueue(q)
    q.gateway.row["提交运营"] = [{"id": "on_syntheticnewowner"}]
    drain(q)
    assert not q.gateway.writes
    assert not q.notify_one()


def test_paused_provider_skips_reminders_but_keeps_selection(coordinator):
    q = coordinator
    cfg = config("reminder")
    cfg["paused_artwork_providers"] = ["Synthetic paused provider"]
    q = Coordinator(q.db, q.gateway, cfg, now=lambda: AT)
    q.gateway.row["制图承接方"] = "Synthetic paused provider"
    q.gateway.row["制图截止日期"] = int((AT + timedelta(days=1)).timestamp() * 1000)
    with q.db.connection() as c:
        c.execute("INSERT INTO base_workflow_records(record_id,snapshot) VALUES(%s,%s)", (RID, Jsonb(q.gateway.row)))
    q.reminders()
    assert not q.run_one()
    selection = Coordinator(
        q.db, q.gateway, {**config(), "paused_artwork_providers": ["Synthetic paused provider"]}, now=q.now
    )
    enqueue(selection)
    drain(selection)
    assert q.gateway.row["审核状态"] == "待审核"


@pytest.mark.parametrize("failure", [503, 1254607])
def test_bulk_gateway_skips_formulas_and_retries_each_page(monkeypatch, failure):
    import httpx

    from amazon_aigc.base_workflows import WorkflowGateway
    from amazon_aigc.config import TEST_BASE, TEST_TABLE

    cfg = config()
    cfg["base_id"] = TEST_BASE
    cfg["tables"][0]["id"] = TEST_TABLE
    calls = []

    def handle(request):
        import json

        calls.append(request)
        assert request.method == "POST"
        assert request.url.path.endswith("/records/search")
        body = json.loads(request.content)
        fields = body["field_names"]
        assert body["automatic_fields"] is False
        assert "提审公式核对" not in fields
        assert "审核状态" in fields
        page = request.url.params.get("page_token")
        if len(calls) == 2:
            assert page == "synthetic-next-page"
            return httpx.Response(503) if failure == 503 else httpx.Response(400, json={"code": 1254607})
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "items": [{"record_id": RID + ("2" if page else ""), "fields": {"审核状态": "草稿"}}],
                    "has_more": not page,
                    "page_token": "synthetic-next-page" if not page else None,
                },
            },
        )

    s = SimpleNamespace(environment="integration", base_id=TEST_BASE, sku_table_id=TEST_TABLE, feishu_mode="openapi")
    g = WorkflowGateway(s, cfg, client=httpx.Client(transport=httpx.MockTransport(handle)))
    g.token = "synthetic-token"
    g.expires = 9999999999
    monkeypatch.setattr("amazon_aigc.base_workflows.time.sleep", lambda _: None)
    assert g.records(TEST_TABLE) == [
        {"record_id": RID, "审核状态": "草稿"},
        {"record_id": RID + "2", "审核状态": "草稿"},
    ]
    assert len(calls) == 3


@pytest.mark.parametrize("repeated_records", [True, False])
def test_bulk_gateway_rejects_nonadvancing_pages(repeated_records):
    from amazon_aigc.base_workflows import WorkflowGateway
    from amazon_aigc.config import TEST_BASE, TEST_TABLE

    cfg = config()
    cfg["base_id"] = TEST_BASE
    cfg["tables"][0]["id"] = TEST_TABLE
    s = SimpleNamespace(environment="integration", base_id=TEST_BASE, sku_table_id=TEST_TABLE, feishu_mode="openapi")
    g = WorkflowGateway(s, cfg)
    calls = []

    def request(*args, **kwargs):
        calls.append(kwargs)
        return {
            "items": [{"record_id": RID if repeated_records else RID + str(len(calls)), "fields": {}}],
            "has_more": True,
            "page_token": "synthetic-repeated-token",
        }

    g.request = request
    with pytest.raises(RuntimeError, match="pagination"):
        g.records(TEST_TABLE)
    assert len(calls) == 2


@pytest.mark.parametrize("result", ["present", "absent", "forbidden", "wrong_record"])
def test_single_record_batch_read_checks_identity_and_access(result):
    import json

    import httpx

    from amazon_aigc.base_workflows import WorkflowGateway
    from amazon_aigc.config import TEST_BASE, TEST_TABLE

    cfg = config()
    cfg["base_id"] = TEST_BASE
    cfg["tables"][0]["id"] = TEST_TABLE
    fields = {"插件提交ID": [{"text": "synthetic-version", "type": "text"}], "审核状态": "草稿"}

    def handle(request):
        assert request.method == "POST" and request.url.path.endswith("/records/batch_get")
        assert json.loads(request.content) == {
            "record_ids": [RID], "user_id_type": "union_id", "automatic_fields": False,
        }
        data = {"records": [{"record_id": RID, "fields": fields}]}
        if result in ("absent", "forbidden"):
            data = {"records": [], result + "_record_ids": [RID]}
        elif result == "wrong_record":
            data["records"][0]["record_id"] = RID + "other"
        return httpx.Response(200, json={"code": 0, "data": data})

    s = SimpleNamespace(environment="integration", base_id=TEST_BASE, sku_table_id=TEST_TABLE, feishu_mode="openapi")
    g = WorkflowGateway(s, cfg, client=httpx.Client(transport=httpx.MockTransport(handle)))
    g.token, g.expires = "synthetic-token", 9999999999
    if result in ("forbidden", "wrong_record"):
        with pytest.raises(PermissionError if result == "forbidden" else RuntimeError):
            g.record(RID)
    else:
        assert g.record(RID) == ({"record_id": RID, **fields} if result == "present" else None)
