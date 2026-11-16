from test_market_editor import cmd, confirmed
from test_notifications import notices  # noqa: F401
from test_unified_artwork import image

from amazon_aigc.notifications import enqueue_event, run_notification, still_relevant


def test_market_notifications_use_child_delivery_and_deduplicate(db, notices):  # noqa: F811
    users, item, _ = notices
    d = confirmed(db, notices)
    t = d["tasks"][0]["id"]
    cmd(db, notices, t, "claim")
    cmd(db, notices, t, "start")
    ids = [image(db, item, users["artist"], n) for n in range(2)]
    cmd(db, notices, t, "deliver", "IT", asset_ids=ids)
    cmd(db, notices, t, "deliver", "FR", asset_ids=ids)
    with db.connection() as c:
        out = c.execute("SELECT * FROM notification_outbox WHERE action='artwork.deliver' AND work_item_id=%s ORDER BY created_at", (item,)).fetchall()
        assert len(out) == 2
        assert {r["payload"]["marketplace"] for r in out} == {"IT", "FR"}
        assert all(still_relevant(c, row) for row in out)
        audit = c.execute("SELECT * FROM audit_events WHERE id=%s", (out[0]["event_id"],)).fetchone()
        enqueue_event(c, audit["id"], item, users["artist"], audit["action"], audit["detail"])
        assert c.execute("SELECT count(*) n FROM notification_outbox WHERE event_id=%s", (audit["id"],)).fetchone()["n"] == 1
    sent = []
    while run_notification(db, None, lambda g,s,b,k: sent.append((s,b,k)) or "om_synthetic"):
        pass
    assert len([b for _,b,_ in sent if "已交付" in b["zh_cn"]["title"]]) == 2
    cmd(db, notices, t, "review", "FR", actor="operator", result="accepted")
    with db.connection() as c:
        row = c.execute("SELECT * FROM notification_outbox WHERE action='artwork.review' AND work_item_id=%s", (item,)).fetchone()
        assert row["payload"]["site"] == "FR" and still_relevant(c, row)
    while run_notification(db, None, lambda g,s,b,k: sent.append((s,b,k)) or "om_synthetic"):
        pass
    with db.connection() as c:
        assert c.execute("SELECT count(DISTINCT event_id) n FROM notification_outbox WHERE action='artwork.publish' AND work_item_id=%s", (item,)).fetchone()["n"] == 1
        assert c.execute("SELECT count(*) n FROM notification_outbox WHERE action='artwork.review' AND state='sent' AND work_item_id=%s", (item,)).fetchone()["n"] == 1
