import pytest
from fastapi import HTTPException
from test_workflow import BRIEF, FACTS, prepare

from amazon_aigc.domain import detail, publish_brief, remove_supplier, write_document
from amazon_aigc.schemas import DocumentWrite


def test_remove_supplier_keeps_other_sources_manual_notes_and_history(db, setup):
    users, item, _ = setup
    user = users["operator"]
    sources = [
        {"id": "s1", "kind": "supplier", "url": "https://detail.1688.com/offer/1.html"},
        {"id": "s2", "kind": "supplier", "url": "https://detail.1688.com/offer/2.html"},
        {"id": "c1", "kind": "competitor", "url": "https://www.amazon.com/dp/B0TEST0005"},
    ]
    rows = [
        {
            "id": "one",
            "name": "物料1",
            "source_id": "s1",
            "url": sources[0]["url"],
            "specification": "黑色",
            "quantity": 2,
            "unit": "个",
            "confirmed": True,
        },
        {
            "id": "two",
            "name": "物料2",
            "source_id": "s2",
            "url": sources[1]["url"],
            "specification": "白色",
            "quantity": 3,
            "unit": "个",
            "confirmed": True,
        },
    ]
    facts = {**FACTS, "components": rows, "included": "物料1、物料2", "source_text": "人工补充保留"}
    with db.connection() as c:
        write_document(c, user, item, DocumentWrite(revision=0, section="sources", body=sources))
    confirmed = prepare(db, user, item, "facts", facts)
    old_version = confirmed["document"]["facts_version"]
    rev = confirmed["document"]["revision"]
    with db.connection() as c, pytest.raises(HTTPException):
        remove_supplier(c, users["other"], item, rev, {"source_id": "s1"})
    with db.connection() as c, pytest.raises(HTTPException):
        remove_supplier(c, user, item, rev - 1, {"source_id": "s1"})
    with db.connection() as c:
        result = remove_supplier(c, user, item, rev, {"source_id": "s1", "included": "物料2 × 3个"})
        body = result["document"]["body"]
        assert [s["id"] for s in body["sources"]] == ["s2", "c1"]
        assert [r["id"] for r in body["facts"]["components"]] == ["two"]
        assert body["facts"]["quantity"] == "3"
        assert body["facts"]["source_text"] == "人工补充保留"
        assert result["document"]["facts_version"] is None
        assert len(next(v for v in result["versions"] if v["id"] == old_version)["body"]["components"]) == 2
        last = remove_supplier(c, user, item, result["document"]["revision"], {"source_id": "s2", "included": ""})
        assert last["document"]["body"]["facts"]["components"] == []
        assert last["document"]["body"]["facts"]["quantity"] == ""
        assert [s["id"] for s in last["document"]["body"]["sources"]] == ["c1"]


def test_supplier_removal_refuses_locked_batches_without_partial_write(db, setup):
    users, item, _ = setup
    user = users["operator"]
    with db.connection() as c:
        write_document(
            c,
            user,
            item,
            DocumentWrite(
                revision=0,
                section="sources",
                body=[{"id": "s1", "kind": "supplier", "url": "https://detail.1688.com/offer/1.html"}],
            ),
        )
    prepare(db, user, item, "facts", FACTS)
    confirmed = prepare(db, user, item, "brief", BRIEF)
    with db.connection() as c:
        before = publish_brief(c, user, item, confirmed["document"]["revision"], {})
    with db.connection() as c, pytest.raises(HTTPException, match="锁定"):
        remove_supplier(c, user, item, before["document"]["revision"], {"source_id": "s1"})
    with db.connection() as c:
        after = detail(c, user, item)
        assert after["document"] == before["document"]
        assert after["tasks"] == before["tasks"]
