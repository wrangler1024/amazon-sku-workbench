"""Human-readable archive names derived from the selected delivery."""

import re
import unicodedata

from .domain import fail, get_item
from .feishu import value


def safe_segment(text, limit=24, fallback="商品"):
    text = unicodedata.normalize("NFC", str(text or ""))
    text = re.sub(r'[\\/:*?"<>|_\x00-\x1f\x7f\s]+', "-", text).strip(". -")
    return text[:limit].rstrip(". -") or fallback


def suggested_short_name(name):
    name = str(name or "").strip()
    # Deterministic suggestions only; never call a model or invent a product type.
    name = re.sub(r"^【(?:生成测试|联调合成|测试)[^】]*】\s*", "", name)
    name = re.sub(r"^(?:(?:亚马逊|跨境|外贸|热销|爆款|新款|现货|厂家直销|厂家|批发|时尚|性感)\s*)+", "", name)
    name = re.split(r"[，,；;\n]", name, maxsplit=1)[0]
    return safe_segment(name, limit=16)


def archive_filename(name):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "-", str(name or "")).strip(". ") or "原图"
    # Keep extensions when limiting very long Unicode filenames.
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    suffix = "." + ext[:12] if ext else ""
    budget = 180 - len(suffix.encode("utf-8"))
    stem = stem.encode("utf-8")[:budget].decode("utf-8", errors="ignore").rstrip(". -") or "原图"
    return stem + suffix


def delivery_download_context(c, user, delivery_id, short_name=None):
    delivery = c.execute(
        "SELECT x.*,d.work_item_id,t.batch,t.brief_version,u.name artist_name "
        "FROM artwork_deliveries x JOIN artwork_tasks t ON t.id=x.task_id "
        "JOIN content_documents d ON d.id=t.document_id LEFT JOIN users u ON u.id=x.delivered_by "
        "WHERE x.id=%s", (delivery_id,),
    ).fetchone()
    if not delivery:
        fail("图片版本不存在", 404)
    item = get_item(c, user, delivery["work_item_id"])
    if delivery["result"] != "accepted":
        fail("该版本尚未验收通过")
    facts = c.execute(
        "SELECT f.body FROM content_versions b JOIN content_versions f ON f.id=b.facts_version WHERE b.id=%s",
        (delivery["brief_version"],),
    ).fetchone()
    product_name = (facts["body"].get("sales_name") if facts else None) or item["name"]
    suggested = suggested_short_name(product_name)
    chosen = safe_segment(short_name) if short_name is not None else suggested
    task_number = value((item.get("review_snapshot") or {}).get("记录序号")).strip()
    number = safe_segment(task_number, 32, "未编号-" + str(item["id"])[:8])
    site = safe_segment(item["site"], 8, "未设站点")
    artist = safe_segment(delivery["artist_name"], 12, "未记录美工")
    prefix = f"{number}_{site}_{artist}_"
    suffix = f"_{delivery['batch']}_{delivery['version']}"
    info = {"product_name": product_name, "suggested_short_name": suggested, "short_name": chosen,
            "prefix": prefix, "suffix": suffix, "folder": prefix + chosen + suffix}
    return delivery, item, info
