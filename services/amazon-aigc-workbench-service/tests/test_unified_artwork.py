from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from test_workflow import pool

from amazon_aigc.domain import claim_batch, detail, task_command
from amazon_aigc.workbench_views import artist_rows, item_rows


def extra_task(db, setup, n):
    users, _, _ = setup
    item, doc = uuid4(), uuid4()
    with db.connection() as c:
        c.execute(
            "INSERT INTO work_items(id,environment,site,code,name,owner_id,group_name,review_status) VALUES(%s,'test','US',%s,'test',%s,'一部一组','审核通过')",
            (item, f"EXTRA-{n}", users["operator"]["id"]),
        )
        c.execute("INSERT INTO content_documents(id,work_item_id) VALUES(%s,%s)", (doc, item))
    return pool(db, (users, item, doc))


def image(db, item, artist, n):
    aid = uuid4()
    with db.connection() as c:
        c.execute(
            "INSERT INTO assets(id,work_item_id,uploaded_by,object_key,name,sha256,size_bytes,mime,width,height) VALUES(%s,%s,%s,%s,%s,%s,100,'image/png',20,20)",
            (aid, item, artist["id"], str(aid), f"image-{n}.png", str(aid)),
        )
    return str(aid)


def test_two_claim_batch_atomic_and_competing_limit(db, setup):
    users, _, _ = setup
    ts = [pool(db, setup), extra_task(db, setup, 2), extra_task(db, setup, 3)]
    with pytest.raises(HTTPException), db.connection() as c:
        claim_batch(
            c, users["artist"], {"tasks": [{"id": ts[0]["id"], "revision": 0}, {"id": ts[1]["id"], "revision": 99}]}
        )
    with db.connection() as c:
        assert c.execute("SELECT state FROM artwork_tasks WHERE id=%s", (ts[0]["id"],)).fetchone()["state"] == "pool"

    def run(t):
        try:
            with db.connection() as c:
                task_command(c, users["artist"], t["id"], "claim", 0, {})
            return True
        except HTTPException:
            return False

    with ThreadPoolExecutor(3) as ex:
        assert sum(ex.map(run, ts)) == 2
    with db.connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM artwork_tasks WHERE assignee_id=%s AND state='waiting'",
                (users["artist"]["id"],),
            ).fetchone()["n"]
            == 2
        )


def test_variable_delivery_image_comments_cancel_and_receipts(db, setup):
    users, item, _ = setup
    t = pool(db, setup)
    revision = 0

    def cmd(action, user="artist", data=None):
        nonlocal revision
        with db.connection() as c:
            r = task_command(c, users[user], t["id"], action, revision, data or {})
        revision = r["tasks"][0]["revision"]
        return r

    cmd("claim")
    cmd("start")
    ids = [image(db, item, users["artist"], n) for n in range(8)]
    with pytest.raises(HTTPException):
        cmd("deliver", data={"asset_ids": []})
    r = cmd("deliver", data={"asset_ids": ids})
    did = r["tasks"][0]["current_delivery_id"]
    with pytest.raises(HTTPException):
        cmd("save-review", "other", {"image_notes": {ids[0]: "bad"}})
    with pytest.raises(HTTPException):
        cmd("save-review", "operator", {"image_notes": {str(uuid4()): "foreign image"}})
    r = cmd("save-review", "operator", {"image_notes": {ids[0]: "数量核对"}, "note": ""})
    assert r["deliveries"][0]["image_notes"][ids[0]] == "数量核对"
    r = cmd("review", "operator", {"result": "accepted"})
    assert r["tasks"][0]["first_review_result"] == "accepted"
    # A real existing supplement receipt must become invalid after cancellation.
    with db.connection() as c:
        doc = c.execute("SELECT id,listing_version FROM content_documents WHERE work_item_id=%s", (item,)).fetchone()
        lv = uuid4()
        pid = uuid4()
        target = uuid4()
        c.execute("INSERT INTO publication_targets(id,document_id,store,msku) VALUES(%s,%s,'test','TEST')", (target, doc['id']))
        c.execute("UPDATE content_documents SET publication_target_id=%s WHERE id=%s", (target, doc['id']))
        c.execute(
            "INSERT INTO content_versions(id,document_id,kind,body,created_by) VALUES(%s,%s,'listing',%s,%s)",
            (lv, doc["id"], Jsonb({"title": "test"}), users["operator"]["id"]),
        )
        c.execute(
            "INSERT INTO publication_records(id,document_id,target_id,listing_version,store,msku,receipt,created_by) VALUES(%s,%s,%s,%s,'test','TEST','test',%s)",
            (pid, doc["id"], target, lv, users["operator"]["id"]),
        )
        c.execute(
            "INSERT INTO image_receipts(id,publication_id,target_id,delivery_id,receipt,created_by) VALUES(%s,%s,%s,%s,'test',%s)",
            (uuid4(), pid, target, did, users["operator"]["id"]),
        )
    r = cmd("cancel-review", "operator", {"delivery_id": did})
    assert r["tasks"][0]["state"] == "pending" and r["tasks"][0]["first_review_result"] is None
    assert len(r["deliveries"][0]["canceled_reviews"]) == 1 and r["deliveries"][0]["asset_ids"] == ids
    assert r["image_receipts"][0]["invalidated_at"]
    r = cmd("review", "operator", {"result": "rejected"})
    assert r["tasks"][0]["first_review_result"] == "rejected"
    r = cmd("deliver", data={"asset_ids": [ids[0]]})
    r = cmd("review", "operator", {"result": "accepted"})
    assert r["tasks"][0]["first_review_result"] == "rejected" and len(r["deliveries"]) == 2
    with db.connection() as c:
        rows = item_rows(c, users["operator"])
        row = next(x for x in rows if x["id"] == str(item))
        assert not row["images_uploaded"]
        people = artist_rows(c)
        artist = next(p for p in people if p["id"] == str(users["artist"]["id"]))
        assert artist["drawing_days"] == 1 and artist["accepted"] >= 1
        assert detail(c, users["artist"], item)["deliveries"][1]["image_notes"][ids[0]] == "数量核对"


def test_cancel_old_batch_and_stale_revision_rejected(db, setup):
    users, item, doc = setup
    t = pool(db, setup)
    with db.connection() as c:
        task_command(c, users["artist"], t["id"], "claim", 0, {})
        task_command(c, users["artist"], t["id"], "start", 1, {})
    aid = image(db, item, users["artist"], 1)
    with db.connection() as c:
        task_command(c, users["artist"], t["id"], "deliver", 2, {"asset_ids": [aid]})
        task_command(c, users["operator"], t["id"], "review", 3, {"result": "accepted"})
    with pytest.raises(HTTPException), db.connection() as c:
        task_command(c, users["operator"], t["id"], "cancel-review", 3, {})
    with db.connection() as c:
        c.execute(
            "INSERT INTO artwork_tasks(id,document_id,brief_version,batch,state) VALUES(%s,%s,%s,2,'pool')",
            (uuid4(), doc, t["brief_version"]),
        )
    with pytest.raises(HTTPException), db.connection() as c:
        task_command(c, users["operator"], t["id"], "cancel-review", 4, {})
