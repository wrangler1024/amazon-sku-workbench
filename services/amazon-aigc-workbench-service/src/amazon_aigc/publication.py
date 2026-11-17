"""Versioned publication registration; withdrawals never alter marketplace content."""

from uuid import uuid4

from .domain import detail, event, fail, get_doc, get_item, project


def current_publication(c, document_id):
    return c.execute(
        "SELECT p.* FROM publication_records p JOIN content_documents d ON d.id=p.document_id "
        "WHERE d.id=%s AND p.target_id=d.publication_target_id AND p.withdrawn_at IS NULL "
        "ORDER BY p.created_at DESC,p.id DESC LIMIT 1",
        (document_id,),
    ).fetchone()


def current_image_registration(c, document_id):
    return c.execute(
        "SELECT r.* FROM image_receipts r JOIN artwork_deliveries ad ON ad.id=r.delivery_id "
        "JOIN artwork_tasks at ON at.id=ad.task_id WHERE at.document_id=%s AND r.withdrawn_at IS NULL "
        "AND r.invalidated_at IS NULL ORDER BY r.created_at DESC,r.id DESC LIMIT 1",
        (document_id,),
    ).fetchone()


def confirmation_note(data, default):
    """New clients confirm directly; preserve notes from earlier clients."""
    value = data.get("receipt", "")
    if not isinstance(value, str) or len(value) > 4000:
        fail("刊登说明需为不超过4000字的文本", 422)
    return value.strip() or default


def publish_listing(c, user, item_id, revision, data):
    item = get_item(c, user, item_id, True)
    doc = get_doc(c, item_id, revision)
    market = _market(c, item, doc, data)
    listing_version = market["listing_version"] if market else doc["listing_version"]
    if not listing_version:
        fail("请先确认Listing文案")
    for key, maximum in [("msku", 200)]:
        if not isinstance(data.get(key), str) or not data[key].strip() or len(data[key]) > maximum:
            fail("请填写有效的MSKU", 422)
    store = data.get("store", "")
    if not isinstance(store, str) or len(store) > 300:
        fail("店铺选填，请填写不超过300字的店铺名称", 422)
    store, msku = store.strip(), data["msku"].strip()
    receipt = confirmation_note(data, "运营确认已在亚马逊后台完成文案保存或上架")
    asin = str(data.get("asin", "")).strip()[:100]
    target = c.execute(
        "INSERT INTO publication_targets(id,document_id,store,msku,asin,marketplace) VALUES(%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(document_id,store,msku,marketplace) DO UPDATE SET asin=EXCLUDED.asin RETURNING *",
        (uuid4(), doc["id"], store, msku, asin, market["site"] if market else ""),
    ).fetchone()
    previous = c.execute(
        "SELECT * FROM publication_records WHERE target_id=%s AND withdrawn_at IS NULL "
        "ORDER BY created_at DESC,id DESC LIMIT 1",
        (target["id"],),
    ).fetchone()
    if previous and previous["listing_version"] == listing_version:
        fail("当前确认版文案已登记，请勿重复确认")
    c.execute(
        "INSERT INTO publication_records(id,document_id,target_id,listing_version,store,msku,asin,receipt,created_by) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            uuid4(),
            doc["id"],
            target["id"],
            listing_version,
            store,
            msku,
            asin,
            receipt,
            user["id"],
        ),
    )
    if market:
        c.execute("UPDATE market_contents SET publication_target_id=%s WHERE document_id=%s AND site=%s", (target["id"], doc["id"], market["site"]))
        c.execute("UPDATE content_documents SET revision=revision+1 WHERE id=%s", (doc["id"],))
    else:
        c.execute("UPDATE content_documents SET publication_target_id=%s,revision=revision+1 WHERE id=%s", (target["id"], doc["id"]))
    event(
        c, item_id, user, "publication.register", {"listing_version": listing_version, "target_id": target["id"]}
    )
    project(c, item_id)
    return detail(c, user, item_id)


def image_receipt(c, user, item_id, revision, data):
    item = get_item(c, user, item_id, True)
    doc = get_doc(c, item_id, revision)
    market = _market(c, item, doc, data)
    t = c.execute(
        "SELECT * FROM artwork_tasks WHERE document_id=%s ORDER BY batch DESC LIMIT 1", (doc["id"],)
    ).fetchone()
    d = c.execute("SELECT * FROM artwork_deliveries WHERE id=%s", (t["current_delivery_id"],)).fetchone() if t else None
    if market and t and t["market_sites"]:
        child = c.execute("SELECT * FROM artwork_task_markets WHERE task_id=%s AND site=%s", (t["id"], market["site"])).fetchone()
        d = c.execute("SELECT * FROM artwork_deliveries WHERE id=%s", (child["current_delivery_id"],)).fetchone() if child else None
    if (
        not d
        or (not market and bool(t["market_sites"]))
        or (not market and t["state"] != "accepted")
        or d["result"] != "accepted"
        or str(data.get("delivery_id")) != str(d["id"])
    ):
        fail("请选择最新已验收图片")
    receipt = confirmation_note(data, "运营确认本版图片已刊登")
    if c.execute(
        "SELECT 1 FROM image_receipts WHERE delivery_id=%s "
        "AND invalidated_at IS NULL AND withdrawn_at IS NULL",
        (d["id"],),
    ).fetchone():
        fail("本版图片已确认刊登")
    c.execute(
        "INSERT INTO image_receipts(id,delivery_id,receipt,created_by) "
        "VALUES(%s,%s,%s,%s)",
        (uuid4(), d["id"], receipt, user["id"]),
    )
    c.execute("UPDATE content_documents SET revision=revision+1 WHERE id=%s", (doc["id"],))
    event(c, item_id, user, "publication.images", {"delivery_id": d["id"]})
    project(c, item_id)
    return detail(c, user, item_id)


def withdraw_registration(c, user, item_id, revision, data):
    item = get_item(c, user, item_id, True)
    doc = get_doc(c, item_id, revision)
    market = _market(c, item, doc, data)
    kind, reason = data.get("kind"), data.get("reason", "")
    if kind not in ("copy", "images") or not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        fail("请选择有效的登记类型与撤回原因", 422)
    current = (
        current_publication(c, doc["id"])
        if kind == "copy"
        else current_image_registration(c, doc["id"])
    )
    if market:
        if kind == "copy":
            current = c.execute("SELECT * FROM publication_records WHERE target_id=%s AND withdrawn_at IS NULL ORDER BY created_at DESC,id DESC LIMIT 1", (market["publication_target_id"],)).fetchone()
        else:
            current = c.execute("SELECT r.* FROM image_receipts r JOIN artwork_deliveries d ON d.id=r.delivery_id JOIN artwork_tasks t ON t.id=d.task_id WHERE t.document_id=%s AND d.marketplace=%s AND r.withdrawn_at IS NULL AND r.invalidated_at IS NULL ORDER BY r.created_at DESC,r.id DESC LIMIT 1", (doc["id"], market["site"])).fetchone()
    if not current or str(current["id"]) != str(data.get("registration_id")):
        fail("当前有效登记已变化，请刷新后重试")
    table = "publication_records" if kind == "copy" else "image_receipts"
    c.execute(
        f"UPDATE {table} SET withdrawn_at=now(),withdrawn_by=%s,withdrawal_reason=%s WHERE id=%s",
        (user["id"], reason.strip(), current["id"]),
    )
    c.execute("UPDATE content_documents SET revision=revision+1 WHERE id=%s", (doc["id"],))
    event(
        c,
        item_id,
        user,
        "publication.withdraw",
        {"kind": kind, "registration_id": current["id"], "reason": reason.strip()},
    )
    project(c, item_id)
    return detail(c, user, item_id)


def _market(c, item, doc, data):
    if item["editor_mode"] != "pan_eu":
        if data.get("site"):
            fail("普通商品只支持评审来源站点", 422)
        return None
    site = data.get("site")
    if site not in item["editor_targets"]:
        fail("请选择当前计划的刊登站点", 422)
    return c.execute("SELECT * FROM market_contents WHERE document_id=%s AND site=%s", (doc["id"], site)).fetchone()
