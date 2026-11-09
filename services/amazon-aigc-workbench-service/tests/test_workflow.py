from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import HTTPException

from amazon_aigc.domain import (
    assign_batch,
    detail,
    idempotent,
    publish_brief,
    publish_listing,
    task_command,
    write_document,
)
from amazon_aigc.schemas import DocumentWrite

FACTS = {"product_type": "ordinary", "sales_name": "四件收纳盘", "quantity": "4件", "included": "4个托盘"}
BRIEF = {
    "product": "四件收纳盘",
    "selling_points": "透明可视",
    "visual_focus": "完整件数",
    "plans": [{"purpose": "主图", "composition": "4个托盘白底"}],
}
LISTING = {
    "title": "Clear Trays, 4 Pack",
    "highlights": "For Everyday Organization",
    "bullets": [
        "Clear material",
        "Four separate trays",
        "Check dimensions",
        "Organize small items",
        "No props included",
    ],
    "description": "Product Overview\nFour trays for organizing.",
    "terms": "drawer storage",
}


def prepare(db, user, item, section, body):
    with db.connection() as c:
        r = detail(c, user, item)["document"]["revision"]
        return write_document(c, user, item, DocumentWrite(revision=r, section=section, body=body, confirm=True))


def pool(db, setup):
    users, item, _doc = setup
    prepare(db, users["operator"], item, "facts", FACTS)
    data = prepare(db, users["operator"], item, "brief", BRIEF)
    with db.connection() as c:
        data = publish_brief(c, users["operator"], item, data["document"]["revision"], {})
    return data["tasks"][0]


def test_listing_publishes_without_artwork(db, setup):
    users, item, _ = setup
    prepare(db, users["operator"], item, "facts", FACTS)
    data = prepare(db, users["operator"], item, "listing", LISTING)
    with db.connection() as c:
        result = publish_listing(
            c,
            users["operator"],
            item,
            data["document"]["revision"],
            {"store": "test", "msku": "TEST-001", "receipt": "合成回执"},
        )
        assert result["publications"] and not result["tasks"]
        old = result["publications"][0]["listing_version"]
        result = write_document(
            c,
            users["operator"],
            item,
            DocumentWrite(
                revision=result["document"]["revision"],
                section="listing",
                body={**LISTING, "title": "Updated Four Trays"},
                confirm=True,
            ),
        )
        assert result["document"]["listing_version"] != old
        assert result["publications"][0]["listing_version"] == old


def test_cross_group_and_stale_revision(db, setup):
    users, item, _ = setup
    with db.connection() as c:
        with pytest.raises(HTTPException) as error:
            detail(c, users["other"], item)
        assert error.value.status_code == 404
    prepare(db, users["operator"], item, "facts", FACTS)
    with db.connection() as c:
        with pytest.raises(HTTPException) as error:
            write_document(c, users["operator"], item, DocumentWrite(revision=0, section="facts", body=FACTS))
        assert error.value.status_code == 409


def test_concurrent_claim_only_one_wins(db, setup):
    users, _item, _ = setup
    t = pool(db, setup)

    def claim(u):
        try:
            with db.connection() as c:
                task_command(c, u, t["id"], "claim", 0, {})
            return True
        except HTTPException:
            return False

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(claim, [users["artist"], users["art_lead"]]))
    assert sum(results) == 1


def test_unstarted_limit_start_release(db, setup):
    users, _item, _ = setup
    t = pool(db, setup)
    with db.connection() as c:
        task_command(c, users["artist"], t["id"], "claim", 0, {})
    # Independent second item, same artist: the limit is cross-task.
    second, doc = uuid4(), uuid4()
    with db.connection() as c:
        c.execute(
            "INSERT INTO work_items(id,environment,site,code,name,owner_id,group_name,review_status) VALUES(%s,'test','US','TEST-2','test',%s,'一部一组','审核通过')",
            (second, users["operator"]["id"]),
        )
        c.execute("INSERT INTO content_documents(id,work_item_id) VALUES(%s,%s)", (doc, second))
    second_task = pool(db, (users, second, doc))
    with db.connection() as c:
        task_command(c, users["artist"], second_task["id"], "claim", 0, {})
    with db.connection() as c:
        task_command(c, users["artist"], t["id"], "start", 1, {})
    with db.connection() as c:
        result = task_command(c, users["artist"], second_task["id"], "return", 1, {"reason": "合成退池测试"})
        assert result["tasks"][0]["state"] == "pool"


def test_batch_assignment_is_atomic_and_lead_works(db, setup):
    users, item, _ = setup
    t = pool(db, setup)
    with pytest.raises(HTTPException), db.connection() as c:
        assign_batch(
            c,
            users["art_lead"],
            {
                "assignee_id": str(users["art_lead"]["id"]),
                "tasks": [{"id": t["id"], "revision": 0}, {"id": str(uuid4()), "revision": 0}],
            },
        )
    with db.connection() as c:
        assert detail(c, users["operator"], item)["tasks"][0]["state"] == "pool"
        assign_batch(
            c,
            users["art_lead"],
            {"assignee_id": str(users["art_lead"]["id"]), "tasks": [{"id": t["id"], "revision": 0}]},
        )
        result = task_command(c, users["art_lead"], t["id"], "start", 1, {})
        assert result["tasks"][0]["state"] == "working"


def test_idempotency_and_changed_payload(db, setup):
    users, _item, _ = setup
    key = str(uuid4())
    count = []

    def op():
        count.append(1)
        return {"ok": True}

    with db.connection() as c:
        idempotent(c, users["operator"], key, {"x": 1}, op)
        idempotent(c, users["operator"], key, {"x": 1}, op)
        assert len(count) == 1
    with pytest.raises(HTTPException), db.connection() as c:
        idempotent(c, users["operator"], key, {"x": 2}, op)


def test_facts_locked_during_artwork(db, setup):
    users, item, _ = setup
    pool(db, setup)
    with pytest.raises(HTTPException), db.connection() as c:
        data = detail(c, users["operator"], item)
        write_document(
            c,
            users["operator"],
            item,
            DocumentWrite(revision=data["document"]["revision"], section="facts", body={**FACTS, "quantity": "5件"}),
        )


def test_reconfirm_same_facts_keeps_version_during_artwork(db, setup):
    users, item, _ = setup
    task = pool(db, setup)
    before = prepare(db, users["operator"], item, "listing", LISTING)
    for step in ("pool", "working"):
        if step == "working":
            with db.connection() as c:
                task_command(c, users["artist"], task["id"], "claim", 0, {})
                task_command(c, users["artist"], task["id"], "start", 1, {})
        with db.connection() as c:
            result = write_document(
                c,
                users["operator"],
                item,
                DocumentWrite(revision=before["document"]["revision"], section="facts", body=FACTS, confirm=True),
            )
            assert result["document"]["facts_version"] == before["document"]["facts_version"]
            assert result["document"]["listing_version"] == before["document"]["listing_version"]
            assert result["document"]["revision"] == before["document"]["revision"]
            assert len(result["versions"]) == len(before["versions"])


def test_withdraw_unlocks_facts_and_preserves_old_brief(db, setup):
    users, item, _ = setup
    task = pool(db, setup)
    with db.connection() as c:
        old = detail(c, users["operator"], item)
        result = task_command(c, users["operator"], task["id"], "withdraw", 0, {})
        result = write_document(
            c,
            users["operator"],
            item,
            DocumentWrite(
                revision=result["document"]["revision"],
                section="facts",
                body={**FACTS, "quantity": "5件"},
                confirm=True,
            ),
        )
        assert result["tasks"][0]["state"] == "withdrawn"
        assert result["tasks"][0]["brief_version"] == old["tasks"][0]["brief_version"]
        assert result["document"]["brief_version"] is None
        assert result["document"]["facts_version"] != old["document"]["facts_version"]


def test_source_removal_persists_without_changing_confirmed_facts(db, setup):
    users, item, _ = setup
    before = prepare(db, users["operator"], item, "facts", FACTS)
    with db.connection() as c:
        saved = write_document(
            c,
            users["operator"],
            item,
            DocumentWrite(
                revision=before["document"]["revision"],
                section="sources",
                body=[{"id": "s1", "kind": "supplier", "url": "https://detail.1688.com/offer/1.html"}],
            ),
        )
        result = write_document(
            c,
            users["operator"],
            item,
            DocumentWrite(revision=saved["document"]["revision"], section="sources", body=[]),
        )
        assert detail(c, users["operator"], item)["document"]["body"]["sources"] == []
        assert result["document"]["facts_version"] == before["document"]["facts_version"]
