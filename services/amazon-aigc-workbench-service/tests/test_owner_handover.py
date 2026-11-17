from types import SimpleNamespace

import pytest

from amazon_aigc.sync import import_records


@pytest.fixture(autouse=True)
def clean_handover(db):
    with db.connection() as c:
        c.execute("DELETE FROM owner_handover_logs")
        c.execute("DELETE FROM source_owner_state")
    yield
    with db.connection() as c:
        c.execute("DELETE FROM owner_handover_logs")
        c.execute("DELETE FROM source_owner_state")


class Gateway:
    def __init__(self, settings, rows, personnel):
        self.s = settings
        self.rows = rows
        self.personnel = personnel
        self.logs = {}
        self.updated = {}

    def records(self, table_id, fields=None):
        if table_id == self.s.sku_table_id:
            return self.rows
        if table_id == self.s.personnel_table_id:
            return self.personnel
        if table_id == self.s.handover_log_table_id:
            return list(self.logs.values())
        raise AssertionError(table_id)

    def update(self, record_id, fields):
        self.updated[record_id] = fields
        row = next(r for r in self.rows if r["record_id"] == record_id)
        row.update(fields)

    def record(self, record_id):
        return next(r for r in self.rows if r["record_id"] == record_id)

    def create_handover_log(self, fields):
        record_id = "log-" + str(len(self.logs) + 1)
        self.logs[record_id] = {"record_id": record_id, **fields}
        return record_id

    def update_handover_log(self, record_id, fields):
        self.logs[record_id].update(fields)


def settings(tmp_path):
    return SimpleNamespace(
        tenant="test",
        environment="test",
        base_id="test-base",
        sku_table_id="tblSKU",
        personnel_table_id="tblPeople",
        handover_log_table_id="tblLogs",
        feishu_mode="openapi",
        storage_root=tmp_path,
    )


def add_identity(c, user, open_id, union_id):
    c.execute(
        "INSERT INTO external_identities(provider,tenant,subject,user_id) VALUES('feishu','test',%s,%s)",
        (open_id, user["id"]),
    )
    c.execute(
        "INSERT INTO external_identities(provider,tenant,subject,user_id) VALUES('feishu_union','test',%s,%s)",
        (union_id, user["id"]),
    )


def row(record_id, owner, submitter_id, creator=None, status="审核通过"):
    return {
        "record_id": record_id,
        "选品SKU": "OWNER-TEST-1",
        "中文品名": "合成负责人交接测试",
        "目标站点": ["US"],
        "审核状态": [status],
        "提交运营": [{"id": owner, "name": "测试运营"}],
        "提交运营ID": submitter_id,
        "创建人": [{"id": creator or owner, "name": "测试运营"}],
        "运营小组": ["一部一组"],
        "审批小组": ["一部一组"],
    }


def personnel(subject, role="一部二组运营", enabled=True):
    return [
        {
            "成员": [{"id": subject, "name": "新运营"}],
            "角色": [role],
            "启用状态": enabled,
            "生效日期": "2026-01-01T00:00:00+08:00",
        }
    ]


def prepare(db, setup):
    users, item, _ = setup
    suffix = item.hex[:12]
    record_id = "rec-" + suffix
    old_open, old_union = "ou_old_" + suffix, "on_old_" + suffix
    new_open, new_union = "ou_new_" + suffix, "on_new_" + suffix
    with db.connection() as c:
        c.execute("DELETE FROM owner_handover_logs")
        c.execute("DELETE FROM source_owner_state")
        add_identity(c, users["operator"], old_open, old_union)
        add_identity(c, users["other"], new_open, new_union)
        c.execute(
            "INSERT INTO external_bindings(provider,tenant,environment,base_id,table_id,record_id,work_item_id) "
            "VALUES('feishu','test','test','test-base','tblSKU',%s,%s)",
            (record_id, item),
        )
        c.execute(
            "UPDATE users SET groups='{\"一部二组\"}', roles='{operator}', active=true, internal=true WHERE id=%s",
            (users["other"]["id"],),
        )
    return users, item, record_id, old_open, old_union, new_open, new_union


def test_owner_change_fills_base_fields_and_logs_success(db, setup, tmp_path):
    users, item, record_id, old_open, old_union, new_open, new_union = prepare(db, setup)
    gateway = Gateway(
        settings(tmp_path),
        [
            row(record_id, old_union, old_open),
            row("rec-map-" + item.hex[:12], new_union, new_open, creator=new_union, status="草稿"),
        ],
        personnel(new_union),
    )
    first = import_records(db, gateway)
    assert first["owner_handovers"]["processed"] == 0

    gateway.rows[0].update({"提交运营": [{"id": new_union, "name": "新运营"}]})
    result = import_records(db, gateway)
    assert result["owner_handovers"] == {"processed": 1, "succeeded": 1, "failed": 0}
    assert gateway.updated[record_id] == {
        "提交运营ID": new_open,
        "运营小组": "一部二组",
        "审批小组": "一部二组",
    }
    with db.connection() as c:
        work = c.execute("SELECT owner_id,group_name FROM work_items WHERE id=%s", (item,)).fetchone()
        log = c.execute("SELECT * FROM owner_handover_logs WHERE record_id=%s", (record_id,)).fetchone()
    assert work["owner_id"] == users["other"]["id"]
    assert work["group_name"] == "一部二组"
    assert log["state"] == "succeeded"
    assert log["base_state"] == "succeeded"
    assert log["workbench_state"] == "succeeded"
    assert gateway.logs["log-1"]["交接状态"] == "成功"

    assert import_records(db, gateway)["owner_handovers"]["processed"] == 0
    with db.connection() as c:
        assert c.execute("SELECT count(*) n FROM owner_handover_logs").fetchone()["n"] == 1


def test_owner_change_failure_is_recorded_and_keeps_workbench_owner(db, setup, tmp_path):
    users, item, record_id, old_open, old_union, _new_open, new_union = prepare(db, setup)
    gateway = Gateway(
        settings(tmp_path),
        [
            row(record_id, old_union, old_open),
            row("rec-map-" + item.hex[:12], new_union, _new_open, creator=new_union, status="草稿"),
        ],
        personnel(new_union),
    )
    import_records(db, gateway)
    gateway.rows[0].update({"提交运营": [{"id": "on_missing_" + item.hex[:12], "name": "未配置运营"}]})

    result = import_records(db, gateway)
    assert result["owner_handovers"] == {"processed": 1, "succeeded": 0, "failed": 1}
    with db.connection() as c:
        work = c.execute("SELECT owner_id FROM work_items WHERE id=%s", (item,)).fetchone()
        log = c.execute("SELECT * FROM owner_handover_logs WHERE record_id=%s", (record_id,)).fetchone()
    assert work["owner_id"] == users["operator"]["id"]
    assert log["state"] == "failed"
    assert log["workbench_state"] == "failed"
    assert "未同步" in log["error"]
    assert gateway.logs["log-1"]["交接状态"] == "失败"


def test_owner_change_with_active_unconfigured_user_keeps_workbench_owner(db, setup, tmp_path):
    users, item, record_id, old_open, old_union, _new_open, new_union = prepare(db, setup)
    gateway = Gateway(
        settings(tmp_path),
        [
            row(record_id, old_union, old_open),
            row("rec-map-" + item.hex[:12], new_union, _new_open, creator=new_union, status="草稿"),
        ],
        [],
    )
    import_records(db, gateway)
    gateway.rows[0].update({"提交运营": [{"id": new_union, "name": "新运营"}]})

    result = import_records(db, gateway)
    assert result["owner_handovers"] == {"processed": 1, "succeeded": 0, "failed": 1}
    with db.connection() as c:
        work = c.execute("SELECT owner_id FROM work_items WHERE id=%s", (item,)).fetchone()
        log = c.execute("SELECT * FROM owner_handover_logs WHERE record_id=%s", (record_id,)).fetchone()
    assert work["owner_id"] == users["operator"]["id"]
    assert log["state"] == "failed"
    assert "插件人员权限配置" in log["error"]
    assert gateway.logs["log-1"]["交接状态"] == "失败"


def test_owner_change_base_write_failure_keeps_workbench_owner(db, setup, tmp_path):
    users, item, record_id, old_open, old_union, _new_open, new_union = prepare(db, setup)
    gateway = Gateway(
        settings(tmp_path),
        [
            row(record_id, old_union, old_open),
            row("rec-map-" + item.hex[:12], new_union, _new_open, creator=new_union, status="草稿"),
        ],
        personnel(new_union),
    )
    import_records(db, gateway)
    gateway.rows[0].update({"提交运营": [{"id": new_union, "name": "新运营"}]})

    def fail_update(record_id, fields):
        raise RuntimeError("synthetic Base write failure")

    gateway.update = fail_update
    result = import_records(db, gateway)
    assert result["owner_handovers"] == {"processed": 1, "succeeded": 0, "failed": 1}
    with db.connection() as c:
        work = c.execute("SELECT owner_id FROM work_items WHERE id=%s", (item,)).fetchone()
        log = c.execute("SELECT * FROM owner_handover_logs WHERE record_id=%s", (record_id,)).fetchone()
    assert work["owner_id"] == users["operator"]["id"]
    assert log["state"] == "failed"
    assert log["base_state"] == "failed"
    assert "synthetic Base write failure" in log["error"]


def test_owner_change_without_base_app_mapping_fails_closed(db, setup, tmp_path):
    users, item, record_id, old_open, old_union, _new_open, new_union = prepare(db, setup)
    gateway = Gateway(
        settings(tmp_path),
        [row(record_id, old_union, old_open)],
        personnel(new_union),
    )
    import_records(db, gateway)
    gateway.rows[0].update({"提交运营": [{"id": new_union, "name": "新运营"}]})

    result = import_records(db, gateway)
    assert result["owner_handovers"] == {"processed": 1, "succeeded": 0, "failed": 1}
    with db.connection() as c:
        work = c.execute("SELECT owner_id FROM work_items WHERE id=%s", (item,)).fetchone()
        log = c.execute("SELECT * FROM owner_handover_logs WHERE record_id=%s", (record_id,)).fetchone()
    assert work["owner_id"] == users["operator"]["id"]
    assert "Base应用身份映射" in log["error"]
