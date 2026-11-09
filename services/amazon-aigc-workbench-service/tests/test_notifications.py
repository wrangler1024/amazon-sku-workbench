import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_workflow import pool

from amazon_aigc.config import TEST_BASE, TEST_TABLE
from amazon_aigc.domain import assign_batch, task_command
from amazon_aigc.notifications import enqueue_event, run_notification, send
from amazon_aigc.notifications import test_notice as queue_test


@pytest.fixture
def notices(db, setup):
    users, item, doc = setup
    with db.connection() as c:
        c.execute("DELETE FROM notification_outbox")
        c.execute("UPDATE notification_config SET enabled=true")
        c.execute(
            "INSERT INTO external_bindings VALUES('feishu','example','integration',%s,%s,%s,%s)",
            (TEST_BASE, TEST_TABLE, str(item), item),
        )
        for u in users.values():
            c.execute(
                "INSERT INTO external_identities VALUES('feishu_union','example',%s,%s)",
                ("test-" + str(u["id"]), u["id"]),
            )
    yield users, item, doc
    with db.connection() as c:
        c.execute("UPDATE notification_config SET enabled=false")
        c.execute("DELETE FROM notification_outbox")


def test_transactional_routing_dedup_and_status(db, notices):
    users, item, _ = notices
    t = pool(db, notices)
    with db.connection() as c:
        event = c.execute(
            "SELECT * FROM audit_events WHERE work_item_id=%s AND action='artwork.publish'", (item,)
        ).fetchone()
        before = c.execute("SELECT count(*) n FROM notification_outbox WHERE event_id=%s", (event["id"],)).fetchone()[
            "n"
        ]
        enqueue_event(c, event["id"], item, users["operator"], "artwork.publish", {})
        assert (
            c.execute("SELECT count(*) n FROM notification_outbox WHERE event_id=%s", (event["id"],)).fetchone()["n"]
            == before
        )
        assert c.execute(
            "SELECT 1 FROM notification_outbox WHERE event_id=%s AND recipient_id=%s",
            (event["id"], users["art_lead"]["id"]),
        ).fetchone()
    # Rollback business mutation also removes its queued notification.
    with pytest.raises(RuntimeError), db.connection() as c:
        task_command(c, users["artist"], t["id"], "claim", 0, {})
        raise RuntimeError("rollback")
    with db.connection() as c:
        assert not c.execute("SELECT 1 FROM notification_outbox WHERE action='artwork.claim'").fetchone()
        assign_batch(
            c, users["art_lead"], {"assignee_id": str(users["artist"]["id"]), "tasks": [{"id": t["id"], "revision": 0}]}
        )
        recipients = {
            r["recipient_id"]
            for r in c.execute("SELECT recipient_id FROM notification_outbox WHERE action='artwork.assign'")
        }
        assert recipients == {users["operator"]["id"], users["artist"]["id"]}
        task_command(c, users["artist"], t["id"], "return", 1, {"reason": "测试退回"})
        recipients = {
            r["recipient_id"]
            for r in c.execute("SELECT recipient_id FROM notification_outbox WHERE action='artwork.return'")
        }
        assert (
            users["operator"]["id"] in recipients
            and users["art_lead"]["id"] in recipients
            and users["artist"]["id"] not in recipients
        )


def test_notification_retry_stable_uuid_and_permission_recheck(db, notices):
    users, _item, _ = notices
    seen = []

    def sender(g, s, b, id):
        seen.append((s, b, id))
        if len(seen) == 1:
            raise TimeoutError("upstream secret text must not be saved")
        return "om_test"

    with db.connection() as c:
        queue_test(c, users["operator"])
    assert run_notification(db, None, sender)
    with db.connection() as c:
        row = c.execute("SELECT * FROM notification_outbox").fetchone()
        assert row["state"] == "failed" and row["error"] == "TimeoutError"
        c.execute("UPDATE notification_outbox SET run_after=now()")
    assert run_notification(db, None, sender)
    assert seen[0][2] == seen[1][2]
    assert not run_notification(db, None, sender)
    with db.connection() as c:
        assert c.execute("SELECT state FROM notification_outbox").fetchone()["state"] == "sent"
        queue_test(c, users["artist"])
        c.execute("UPDATE users SET active=false WHERE id=%s", (users["artist"]["id"],))
    assert run_notification(db, None, sender) and len(seen) == 2
    with db.connection() as c:
        assert (
            c.execute(
                "SELECT state FROM notification_outbox WHERE recipient_id=%s", (users["artist"]["id"],)
            ).fetchone()["state"]
            == "skipped"
        )


def test_transport_app_identity_and_fixed_test_boundary(monkeypatch):
    captured = {}

    def post(url, **kw):
        captured.update(url=url, **kw)
        return SimpleNamespace(
            raise_for_status=lambda: None, json=lambda: {"code": 0, "data": {"message_id": "om_test"}}
        )

    monkeypatch.setattr("amazon_aigc.notifications.httpx.post", post)
    gateway = SimpleNamespace(
        s=SimpleNamespace(environment="integration", base_id=TEST_BASE, sku_table_id=TEST_TABLE, feishu_mode="openapi"),
        access_token=lambda: "test-token",
    )
    uid = uuid4()
    assert send(gateway, "union_test", {"zh_cn": {"title": "test"}}, uid) == "om_test"
    assert captured["params"] == {"receive_id_type": "union_id"} and captured["json"]["uuid"] == str(uid)
    assert json.loads(captured["json"]["content"])["zh_cn"]["title"] == "test"
    gateway.s.base_id = "forbidden"
    with pytest.raises(RuntimeError):
        send(gateway, "union_test", {}, uid)


def test_admin_notice_settings_and_diagnostic_idempotency(db, notices, tmp_path):
    from fastapi.testclient import TestClient

    from amazon_aigc.api import create_app
    from amazon_aigc.auth import new_session
    from amazon_aigc.config import Settings

    users, _, _ = notices
    s = Settings(database_url="postgresql://unused/test", storage_root=tmp_path, sync_enabled=False)
    with db.connection() as c:
        token = new_session(c, users["operator"]["id"])
    client = TestClient(create_app(s, db))
    headers = {"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid4())}
    assert client.get("/api/admin/notifications", headers=headers).status_code == 403
    with db.connection() as c:
        c.execute("UPDATE users SET roles=ARRAY['admin'] WHERE id=%s", (users["operator"]["id"],))
    assert client.get("/api/admin/notifications", headers=headers).status_code == 200
    first = client.post("/api/admin/notifications/test", headers=headers)
    again = client.post("/api/admin/notifications/test", headers=headers)
    assert first.status_code == 200 and first.json() == again.json()
    assert first.json()["recipient"] == "operator"
    assert client.put("/api/admin/notifications", headers=headers, json={"enabled": "yes"}).status_code == 422
    assert client.put("/api/admin/notifications", headers=headers, json={"enabled": False}).json()["enabled"] is False
