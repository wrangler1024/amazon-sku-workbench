
import pytest
from fastapi import HTTPException
from test_unified_artwork import image
from test_workflow import FACTS, LISTING, pool, prepare

from amazon_aigc.domain import detail, publish_listing, task_command
from amazon_aigc.publication import image_receipt, withdraw_registration
from amazon_aigc.workbench_query import workbench_page
from amazon_aigc.workbench_views import item_rows


@pytest.mark.parametrize("store", ["测试店", "", "   ", None])
def test_independent_withdrawals_and_reregistration(db, setup, store):
    users, item, _ = setup
    t = pool(db, setup)
    prepare(db, users["operator"], item, "listing", LISTING)

    def action(fn, body):
        with db.connection() as c:
            rev = detail(c, users["operator"], item)["document"]["revision"]
            return fn(c, users["operator"], item, rev, body)

    store_data = {} if store is None else {"store": store}
    r = action(publish_listing, {**store_data, "msku": "TEST"})
    target_id = r["publication_target"]["id"]
    assert r["publication_target"]["store"] == (store or "").strip()
    first = r["publications"][0]["id"]
    assert r["publications"][0]["receipt"] == "运营确认已在亚马逊后台完成文案保存或上架"
    with db.connection() as c:
        task_command(c, users["artist"], t["id"], "claim", 0, {})
        task_command(c, users["artist"], t["id"], "start", 1, {})
    aid = image(db, item, users["artist"], 1)
    with db.connection() as c:
        r = task_command(c, users["artist"], t["id"], "deliver", 2, {"asset_ids": [aid]})
        r = task_command(c, users["operator"], t["id"], "review", 3, {"result": "accepted"})
    did = r["tasks"][0]["current_delivery_id"]
    r = action(image_receipt, {"delivery_id": did})
    receipt = r["image_receipts"][0]["id"]
    with db.connection() as c:
        row = next(x for x in workbench_page(c, users["operator"], tab="imagePublication")["items"] if x["id"] == str(item))
        assert row["copy_published_at"] and row["image_published_at"]
    assert r["image_receipts"][0]["receipt"] == "运营确认本版图片已刊登"
    r = action(withdraw_registration, {"kind": "copy", "registration_id": first, "reason": "误点"})
    assert r["publication_target"] and not r["image_receipts"][0]["withdrawn_at"]
    with db.connection() as c:
        row = next(r for r in item_rows(c, users["operator"]) if r["id"] == str(item))
        assert row["images_uploaded"] and row["publication_count"] == 0
    r = action(withdraw_registration, {"kind": "images", "registration_id": receipt, "reason": "误点"})
    assert r["tasks"][0]["state"] == "accepted"
    with db.connection() as c:
        row = next(x for x in workbench_page(c, users["operator"], tab="imagePublication")["items"] if x["id"] == str(item))
        assert row["copy_published_at"] is None and row["image_published_at"] is None
    assert r["publications"][0]["withdrawn_at"] and r["image_receipts"][0]["withdrawn_at"]
    # Target remains usable after copy withdrawal; image registration has its own history.
    r = action(image_receipt, {"delivery_id": did, "receipt": "重新登记图片"})
    assert len(r["image_receipts"]) == 2 and not r["image_receipts"][0]["withdrawn_at"]
    r = action(publish_listing, {**store_data, "msku": "TEST", "receipt": "重新登记文案"})
    assert r["publication_target"]["id"] == target_id
    assert len(r["publications"]) == 2
    with pytest.raises(HTTPException):
        action(withdraw_registration, {"kind": "copy", "registration_id": first, "reason": "过期登记"})
    with pytest.raises(HTTPException), db.connection() as c:
        withdraw_registration(
            c,
            users["other"],
            item,
            r["document"]["revision"],
            {"kind": "copy", "registration_id": r["publications"][0]["id"], "reason": "越权"},
        )
    # Copy target changes do not alter image publication.
    r = action(publish_listing, {"store": "其他店", "msku": "OTHER", "receipt": "其他刊登目标"})
    with db.connection() as c:
        row = next(r for r in item_rows(c, users["operator"]) if r["id"] == str(item))
        assert row["images_uploaded"]
    with pytest.raises(HTTPException) as duplicate:
        action(image_receipt, {"delivery_id": did})
    assert "本版图片已确认刊登" in duplicate.value.detail


@pytest.mark.parametrize("bad_data", [
    {"store": 123}, {"store": "x" * 301}, {"msku": ""}, {"receipt": 123}, {"receipt": "x" * 4001}
])
def test_optional_store_keeps_validation_boundaries(db, setup, bad_data):
    users, item, _ = setup
    prepare(db, users["operator"], item, "facts", FACTS)
    prepare(db, users["operator"], item, "listing", LISTING)
    with pytest.raises(HTTPException) as error, db.connection() as c:
        rev = detail(c, users["operator"], item)["document"]["revision"]
        publish_listing(c, users["operator"], item, rev,
                        {"msku": "TEST", "receipt": "已在后台保存", **bad_data})
    assert error.value.status_code == 422


def test_images_publish_before_any_copy_and_stay_independent(db, setup):
    users, item, _ = setup
    t = pool(db, setup)
    with db.connection() as c:
        task_command(c, users["artist"], t["id"], "claim", 0, {})
        task_command(c, users["artist"], t["id"], "start", 1, {})
    aid = image(db, item, users["artist"], 1)
    with db.connection() as c:
        r = task_command(c, users["artist"], t["id"], "deliver", 2, {"asset_ids": [aid]})
        r = task_command(c, users["operator"], t["id"], "review", 3, {"result": "accepted"})
    delivery_id = r["tasks"][0]["current_delivery_id"]

    def command(fn, data):
        with db.connection() as c:
            revision = detail(c, users["operator"], item)["document"]["revision"]
            return fn(c, users["operator"], item, revision, data)

    with db.connection() as c:
        row = workbench_page(c, users["operator"], tab="imagePublication")["items"][0]
        assert row["image_state"] == "ready" and row["publication_target"] is None
    r = command(image_receipt, {"delivery_id": delivery_id})
    assert not r["publications"] and r["publication_target"] is None
    receipt = r["image_receipts"][0]
    assert receipt["publication_id"] is None and receipt["target_id"] is None
    assert receipt["delivery_id"] == delivery_id
    with db.connection() as c:
        row = workbench_page(c, users["operator"], tab="imagePublication")["items"][0]
        assert row["image_state"] == "uploaded" and row["image_published_at"]
        assert row["copy_published_at"] is None
    prepare(db, users["operator"], item, "listing", LISTING)
    r = command(publish_listing, {"msku": "INDEPENDENT"})
    pub_id = r["publications"][0]["id"]
    r = command(withdraw_registration, {"kind": "images", "registration_id": receipt["id"], "reason": "误点"})
    assert r["publications"][0]["id"] == pub_id and not r["publications"][0]["withdrawn_at"]
    r = command(image_receipt, {"delivery_id": delivery_id})
    r = command(withdraw_registration, {"kind": "copy", "registration_id": pub_id, "reason": "误点"})
    assert not r["image_receipts"][0]["withdrawn_at"]
    with db.connection() as c:
        r = task_command(c, users["operator"], t["id"], "cancel-review", 4, {})
    assert r["image_receipts"][0]["invalidated_at"]
    with db.connection() as c:
        row = workbench_page(c, users["operator"], tab="imagePublication")["items"][0]
        assert row["image_state"] == "blocked" and row["image_published_at"] is None
    with db.connection() as c:
        task_command(c, users["operator"], t["id"], "review", 5, {"result": "accepted"})
    r = command(image_receipt, {"delivery_id": delivery_id})
    assert not r["image_receipts"][0]["invalidated_at"]
    assert len(r["image_receipts"]) == 3
