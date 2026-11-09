"""Pair catalog SKUs without turning provider quotes into confirmed product facts."""

from decimal import Decimal, InvalidOperation
from uuid import NAMESPACE_URL, UUID, uuid5

from .domain import event, fail, get_doc, get_item, write_document
from .product_facts import paired_contents
from .schemas import DocumentWrite
from .tmapi_collector import identify


def pair(c, user, item_id, revision, data):
    item = get_item(c, user, item_id, True)
    doc = get_doc(c, item_id, revision)
    if c.execute(
        "SELECT 1 FROM artwork_tasks WHERE document_id=%s AND state NOT IN ('accepted','withdrawn')", (doc["id"],)
    ).fetchone():
        fail("本批次资料已锁定；请先撤回未开工图需后再修改货源配对")
    if type(data.get("apply_to_facts", False)) is not bool:
        fail("带入资料参数须为布尔值", 422)
    try:
        snapshot_id = UUID(str(data.get("snapshot_id", "")))
    except ValueError:
        fail("请先读取货源规格", 422)
    snapshot = c.execute(
        "SELECT * FROM source_snapshots WHERE id=%s AND work_item_id=%s", (snapshot_id, item_id)
    ).fetchone()
    if not snapshot or snapshot["body"].get("collector") != "tmapi":
        fail("请选择本商品TMAPI读取到的货源规格", 422)
    if identify(snapshot["url"])[0] != "1688":
        fail("只支持1688货源规格配对", 422)
    sources = list(doc["body"].get("sources") or [])
    index = next(
        (
            i
            for i, s in enumerate(sources)
            if s.get("id") == data.get("source_id") and s.get("kind") == "supplier" and s.get("url") == snapshot["url"]
        ),
        None,
    )
    if index is None:
        fail("来源链接已变化，请重新读取后配对")
    raw = data.get("selected")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 100:
        fail("请选择1—100个供应商规格", 422)
    catalog = {v["sku_id"]: v for v in snapshot["body"].get("variants", [])}
    selected = []
    seen = set()
    for value in raw:
        if not isinstance(value, dict):
            fail("规格选择格式不正确", 422)
        sku_id = str(value.get("sku_id", ""))
        if sku_id in seen or sku_id not in catalog:
            fail("规格重复或不属于本次货源，请重新选择", 422)
        try:
            quantity = Decimal(str(value.get("quantity", 0)))
        except InvalidOperation:
            fail("每份用量须为有效数字", 422)
        if not quantity.is_finite() or not 0 < quantity <= 100000:
            fail("每份用量须大于0且不超过100000", 422)
        seen.add(sku_id)
        variant = catalog[sku_id]
        selected.append({**variant, "quantity": float(quantity), "unit": snapshot["body"].get("offer_unit") or "件"})
    source = {
        **sources[index],
        "paired_snapshot_id": str(snapshot["id"]),
        "paired_at": str(snapshot["created_at"]),
        "supplier_title": snapshot["body"]["title"],
        "selected_skus": selected,
    }
    sources[index] = source
    facts = None
    if data.get("apply_to_facts"):
        facts = dict(doc["body"].get("facts") or {})
        facts.setdefault("sales_name", item["name"])
        old = facts.get("components") or []
        # Legacy single products have no component rows. Preserve their original
        # specification when an additional supplier is being added.
        first_source = next((s for s in sources if s.get("kind") == "supplier" and s.get("url")), None)
        if not old and first_source and first_source["url"] != source["url"] and facts.get("specification"):
            old = [
                {
                    "id": str(uuid5(NAMESPACE_URL, str(item_id) + ":legacy")),
                    "name": (facts.get("sales_name") or item["name"])[:200],
                    "specification": facts.get("specification", ""),
                    "quantity": facts.get("quantity", ""),
                    "unit": facts.get("unit", ""),
                    "url": first_source["url"],
                    "source_id": first_source["id"],
                    "confirmed": False,
                }
            ]
        unpaired = [x for x in old if x.get("source_id") != source["id"] and x.get("url") != source["url"]]
        rows = [
            {
                "id": str(uuid5(NAMESPACE_URL, source["id"] + ":" + v["sku_id"])),
                "name": (source.get("supplier_title") or facts.get("sales_name") or "组成物料")[:200],
                "url": source["url"],
                "specification": v["specification"],
                "quantity": v["quantity"],
                "unit": v["unit"],
                "confirmed": False,
                "source_id": source["id"],
                "supplier_sku_id": v["sku_id"],
            }
            for v in selected
        ]
        if len(unpaired) + len(rows) > 100:
            fail("组成明细最多100项", 422)
        facts["components"] = unpaired + rows
        facts["included"] = paired_contents(facts.get("included", ""), old, facts["components"])
    result = write_document(
        c, user, item_id, DocumentWrite(revision=revision, section="sources", body=sources), allow_pairing=True
    )
    if facts is not None:
        result = write_document(
            c, user, item_id, DocumentWrite(revision=result["document"]["revision"], section="facts", body=facts)
        )
    event(
        c,
        item_id,
        user,
        "supplier.pair",
        {
            "source_id": source["id"],
            "snapshot_id": str(snapshot["id"]),
            "sku_ids": [x["sku_id"] for x in selected],
            "apply_to_facts": bool(data.get("apply_to_facts")),
        },
    )
    return result
