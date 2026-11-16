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
                "next": "notice",
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
    assert q.decode_fields([{"field_id": "f1", "field_value": None}]) == {"提交校验状态": None}


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
