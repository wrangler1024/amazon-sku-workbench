from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from test_supplier_pairing import pairing  # noqa: F401

from amazon_aigc.api import create_app
from amazon_aigc.auth import new_session
from amazon_aigc.config import Settings
from amazon_aigc.domain import detail
from amazon_aigc.product_facts import paired_contents, summarize_contents
from amazon_aigc.supplier_pairing import pair


@pytest.mark.parametrize("included", ["", "   "])
def test_partial_facts_save_then_confirm_has_actionable_error(db, setup, tmp_path, included):
    users, item, _ = setup
    client = TestClient(create_app(Settings(database_url="postgresql://unused/test", storage_root=tmp_path), db))
    with db.connection() as c:
        token = new_session(c, users["operator"]["id"])
    headers = {"Authorization": "Bearer " + token}
    path = f"/api/items/{item}/document"
    facts = {"sales_name": "雪纺披肩", "quantity": "1件", "included": included, "uses": "晚宴搭配"}

    def save(revision, value, confirm=False):
        return client.put(
            path,
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={
                "revision": revision,
                "section": "facts",
                "body": value,
                "confirm": confirm,
            },
        )

    saved = save(0, facts)
    assert saved.status_code == 200
    document = saved.json()["document"]
    assert document["body"]["facts"] == facts and document["facts_version"] is None
    response = save(document["revision"], facts, True)
    assert response.status_code == 422
    message = response.json()["detail"]
    assert "实际包含物" in message and "按明细一键汇总" in message and "保存草稿" in message
    assert all(s not in message for s in ["pydantic", "validation error", "input_value", "晚宴搭配"])
    with db.connection() as c:
        assert detail(c, users["operator"], item)["document"] == document
    confirmed = save(document["revision"], {**facts, "included": "粉色披肩1件"}, True)
    assert confirmed.status_code == 200 and confirmed.json()["document"]["facts_version"]


def test_pairing_prefills_contents_without_confirming_and_refreshes_selected_quantity(db, pairing):  # noqa: F811
    users, item, before, payload = pairing
    with db.connection() as c:
        c.execute(
            "UPDATE content_documents SET body=jsonb_set(body,'{facts,included}',%s) WHERE work_item_id=%s",
            (Jsonb(""), item),
        )
        first = pair(c, users["operator"], item, before["document"]["revision"], {**payload, "apply_to_facts": True})
        facts = first["document"]["body"]["facts"]
        assert facts["included"] == "供应商商品：黑色薄款；裸装 × 2个"
        assert first["document"]["facts_version"] is None
        assert not facts["components"][0]["confirmed"]
        payload["selected"].append({"sku_id": "pink", "quantity": 3})
        second = pair(c, users["operator"], item, first["document"]["revision"], {**payload, "apply_to_facts": True})
        assert (
            second["document"]["body"]["facts"]["included"] == "供应商商品：黑色薄款；裸装 × 2个；粉色薄款；裸装 × 3个"
        )


def test_contents_preserve_manual_accessories_and_multiple_suppliers():
    a = {"id": "a", "name": "披肩", "specification": "粉色", "quantity": 1, "unit": "件"}
    b = {"id": "b", "name": "收纳袋", "specification": "白色", "quantity": 1, "unit": "个"}
    summary = summarize_contents([a])
    assert paired_contents(summary, [a], [a, b]) == "披肩：粉色 × 1件\n收纳袋：白色 × 1个"
    assert paired_contents(summary + "\n另含礼盒", [a], [a, b]).endswith("\n另含礼盒")
    assert paired_contents("实物仅含披肩一件，无礼盒", [a], [a, b]) == "实物仅含披肩一件，无礼盒"
    assert paired_contents("", [], [a, {**b, "quantity": 0}]) == ""
    assert paired_contents("", [], [{**a, "quantity": True}]) == ""
    many = [{**a, "id": str(i), "specification": "长规格" * 100} for i in range(100)]
    assert len(summarize_contents(many)) <= 4000
