from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from test_workflow import FACTS, LISTING, pool, prepare

from amazon_aigc.domain import detail, write_document
from amazon_aigc.schemas import DocumentWrite
from amazon_aigc.source_pipeline import input_stamp
from amazon_aigc.supplier_pairing import pair

URL = "https://detail.1688.com/offer/900000000002.html"


@pytest.fixture
def pairing(db, setup):
    users, item, _ = setup
    user = users["operator"]
    prepare(db, user, item, "facts", FACTS)
    prepare(db, user, item, "listing", LISTING)
    sid = uuid4()
    with db.connection() as c:
        d = detail(c, user, item)
        d = write_document(
            c,
            user,
            item,
            DocumentWrite(
                revision=d["document"]["revision"],
                section="sources",
                body=[
                    {"id": "source-1", "kind": "supplier", "url": URL, "manual_text": "保留人工补充"},
                ],
            ),
        )
        c.execute(
            "INSERT INTO source_snapshots(id,work_item_id,source_id,url,document_revision,method,parser_version,status,body) VALUES(%s,%s,'source-1',%s,3,'tmapi','tmapi-1688-amazon-2',%s,%s)",
            (
                sid,
                item,
                URL,
                "complete",
                Jsonb(
                    {
                        "collector": "tmapi",
                        "title": "供应商商品",
                        "offer_unit": "个",
                        "variants": [
                            {
                                "sku_id": "black",
                                "specification": "黑色薄款；裸装",
                                "reference_price": "0.45",
                                "currency": "CNY",
                                "stock": 99,
                                "images": [],
                            },
                            {
                                "sku_id": "pink",
                                "specification": "粉色薄款；裸装",
                                "reference_price": "0.50",
                                "currency": "CNY",
                                "stock": 88,
                                "images": [],
                            },
                        ],
                    }
                ),
            ),
        )
    payload = {"source_id": "source-1", "snapshot_id": str(sid), "selected": [{"sku_id": "black", "quantity": 2}]}
    return users, item, d, payload


def test_quote_from_snapshot_and_reference_only_preserves_confirmations(db, pairing):
    users, item, before, payload = pairing
    payload["selected"][0]["reference_price"] = "0.01"
    with db.connection() as c:
        old = c.execute("SELECT * FROM content_documents WHERE work_item_id=%s", (item,)).fetchone()
        after = pair(c, users["operator"], item, before["document"]["revision"], payload)
        new = c.execute("SELECT * FROM content_documents WHERE work_item_id=%s", (item,)).fetchone()
    source = after["document"]["body"]["sources"][0]
    assert source["selected_skus"][0]["reference_price"] == "0.45"
    assert source["manual_text"] == "保留人工补充"
    assert after["document"]["facts_version"] == before["document"]["facts_version"]
    assert after["document"]["listing_version"] == before["document"]["listing_version"]
    assert after["document"]["body"]["facts"] == before["document"]["body"]["facts"]
    assert input_stamp(old) != input_stamp(new)
    # The generic sources editor cannot forge provider metadata; changing URL clears it.
    forged = deepcopy(after["document"]["body"]["sources"])
    forged[0]["selected_skus"][0]["reference_price"] = "0.01"
    with db.connection() as c:
        result = write_document(
            c, users["operator"], item, DocumentWrite(revision=new["revision"], section="sources", body=forged)
        )
        assert result["document"]["body"]["sources"][0]["selected_skus"][0]["reference_price"] == "0.45"
        forged[0]["url"] = "https://detail.1688.com/offer/900000000001.html"
        result = write_document(
            c,
            users["operator"],
            item,
            DocumentWrite(revision=result["document"]["revision"], section="sources", body=forged),
        )
        assert "selected_skus" not in result["document"]["body"]["sources"][0]


@pytest.mark.parametrize(
    "selected",
    [
        None,
        [],
        [None],
        [{"sku_id": "missing", "quantity": 1}],
        [{"sku_id": "black", "quantity": 1}] * 2,
        *[[{"sku_id": "black", "quantity": q}] for q in [-1, 0, "NaN", "Infinity", "abc", True, 100001]],
    ],
)
def test_invalid_selection_is_rejected_without_writes(db, pairing, selected):
    users, item, before, payload = pairing
    with pytest.raises(HTTPException), db.connection() as c:
        pair(c, users["operator"], item, before["document"]["revision"], {**payload, "selected": selected})
    with db.connection() as c:
        assert detail(c, users["operator"], item)["document"] == before["document"]


@pytest.mark.parametrize("change", [{"snapshot_id": str(uuid4())}, {"source_id": "other"}, {"apply_to_facts": "false"}])
def test_pairing_identity_and_flags_are_checked(db, pairing, change):
    users, item, before, payload = pairing
    with pytest.raises(HTTPException), db.connection() as c:
        pair(c, users["operator"], item, before["document"]["revision"], {**payload, **change})
    with pytest.raises(HTTPException), db.connection() as c:
        pair(c, users["other"], item, before["document"]["revision"], payload)


def test_single_apply_invalidates_current_confirmations_but_keeps_history(db, pairing):
    users, item, before, payload = pairing
    with db.connection() as c:
        result = pair(c, users["operator"], item, before["document"]["revision"], {**payload, "apply_to_facts": True})
    doc = result["document"]
    assert doc["facts_version"] is None and doc["listing_version"] is None
    assert doc["body"]["facts"]["specification"] == "黑色薄款；裸装"
    assert doc["body"]["facts"]["quantity"] == "2"
    assert doc["body"]["facts"]["included"] == FACTS["included"]
    assert result["versions"] == before["versions"]


def test_multi_apply_infers_bundle_and_keeps_other_components(db, pairing):
    users, item, before, payload = pairing
    payload["selected"].append({"sku_id": "pink", "quantity": 3})
    payload["apply_to_facts"] = True
    with db.connection() as c:
        applied = pair(c, users["operator"], item, before["document"]["revision"], payload)
        assert applied["document"]["body"]["facts"]["product_type"] == "bundle"
        assert applied["document"]["body"]["facts"]["quantity"] == "5"
        before = applied
    with db.connection() as c:
        other = {
            "id": "other",
            "name": "其他物料",
            "url": "https://detail.1688.com/offer/123.html",
            "specification": "白色",
            "quantity": 1,
            "unit": "件",
            "confirmed": True,
        }
        facts = {**FACTS, "product_type": "bundle", "components": [other, {**other, "id": "replace", "url": URL}]}
        d = write_document(
            c,
            users["operator"],
            item,
            DocumentWrite(revision=before["document"]["revision"], section="facts", body=facts),
        )
        result = pair(c, users["operator"], item, d["document"]["revision"], payload)
    rows = result["document"]["body"]["facts"]["components"]
    assert rows[0] == other and len(rows) == 3
    assert [r["quantity"] for r in rows[1:]] == [2, 3]
    assert all(r["confirmed"] is False for r in rows[1:])
    assert all("reference_price" not in r for r in rows)


def test_active_artwork_locks_pairing(db, setup):
    task = pool(db, setup)
    users, item, _ = setup
    with pytest.raises(HTTPException, match="锁定"), db.connection() as c:
        d = detail(c, users["operator"], item)
        pair(c, users["operator"], item, d["document"]["revision"], {})
    assert task["state"] == "pool"


def test_link_first_works_without_preselecting_product_type(db, pairing):
    users, item, before, payload = pairing
    with db.connection() as c:
        d = write_document(
            c, users["operator"], item, DocumentWrite(revision=before["document"]["revision"], section="facts", body={})
        )
        result = pair(c, users["operator"], item, d["document"]["revision"], {**payload, "apply_to_facts": True})
    facts = result["document"]["body"]["facts"]
    assert facts["product_type"] == "ordinary" and facts["quantity"] == "2"
    assert len(facts["components"]) == 1 and facts["sales_name"]
    assert facts["included"] == "供应商商品：黑色薄款；裸装 × 2个"
    assert not facts.get("uses")  # Usage is not inferred from supplier marketing copy.


def test_add_second_supplier_preserves_first_and_remove_returns_to_single(db, pairing):
    users, item, before, payload = pairing
    user = users["operator"]
    with db.connection() as c:
        first = pair(c, user, item, before["document"]["revision"], {**payload, "apply_to_facts": True})
        second_url = "https://detail.1688.com/offer/123.html"
        sources = first["document"]["body"]["sources"] + [{"id": "source-2", "url": second_url, "kind": "supplier"}]
        d = write_document(
            c, user, item, DocumentWrite(revision=first["document"]["revision"], section="sources", body=sources)
        )
        snap = uuid4()
        c.execute(
            "INSERT INTO source_snapshots SELECT %s,work_item_id,'source-2',%s,document_revision,method,status,body,object_key,parser_version,now() FROM source_snapshots WHERE id=%s",
            (snap, second_url, payload["snapshot_id"]),
        )
        result = pair(
            c,
            user,
            item,
            d["document"]["revision"],
            {**payload, "source_id": "source-2", "snapshot_id": str(snap), "apply_to_facts": True},
        )
        facts = result["document"]["body"]["facts"]
        assert facts["product_type"] == "bundle" and len(facts["components"]) == 2
        assert facts["components"][0] == first["document"]["body"]["facts"]["components"][0]
        assert facts["included"] == FACTS["included"]
        # The backend derives both type and totals, even if a stale UI sends old summaries.
        facts["components"] = facts["components"][:1]
        changed = write_document(
            c,
            user,
            item,
            DocumentWrite(revision=result["document"]["revision"], section="facts", body=facts, confirm=True),
        )
        assert changed["document"]["body"]["facts"]["product_type"] == "ordinary"
        assert changed["document"]["body"]["facts"]["quantity"] == "2"
        assert changed["document"]["facts_version"]


def test_mixed_units_are_not_summed_as_equal_items():
    from amazon_aigc.product_facts import normalize_components

    f = normalize_components(
        {
            "included": "保留运营填写",
            "uses": "保留用途",
            "components": [
                {"specification": "绑带", "quantity": 2, "unit": "条"},
                {"specification": "盒", "quantity": 1, "unit": "个"},
            ],
        }
    )
    assert f["quantity"] == "2条 + 1个" and f["unit"] == ""
    assert f["included"] == "保留运营填写" and f["uses"] == "保留用途"


def test_material_conflict_and_product_dimension_provenance():
    from amazon_aigc.product_facts import source_fact_hints

    hints = source_fact_hints(
        {"title": "纯棉开衫", "specifications": ["面料成分: 棉", "面料成分含量: 65%棉+35%涤纶", "包装尺寸: 20x10x5cm"]}
    )
    assert hints["material"] == "65%棉+35%涤纶" and hints["dimensions"] == ""
    assert len(hints["warnings"]) == 1 and "100%" in hints["warnings"][0]
