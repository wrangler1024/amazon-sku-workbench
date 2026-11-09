"""One-time audited cutover of native artwork. Never write selection data."""
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from .config import PRODUCTION_BASE, PRODUCTION_TABLE
from .feishu import value
from .sync import reference_urls, supplier_urls

STATES = {"制图中": "working", "待验收": "pending", "待返修": "revision"}


def fingerprint(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def cohort(row):
    if value(row.get("审核状态")) != "审核通过":
        return "unapproved"
    state = value(row.get("制图状态"))
    if state in ("待制图", "待分配"):
        if row.get("首次开工时间") or row.get("成品图") or row.get("成品交付日期"):
            raise ValueError("Unstarted record contains delivery/start evidence: " + row["record_id"])
        return "reset"
    if state in STATES:
        if not row.get("首次开工时间") or not row.get("美工"):
            raise ValueError("Active record missing start/maker: " + row["record_id"])
        if state != "制图中" and not row.get("成品交付日期"):
            raise ValueError("Delivery time missing: " + row["record_id"])
        return "active"
    if state == "已验收":
        return "history"
    raise ValueError("Unknown legacy state: " + row["record_id"])


def stamp(v):
    if not v:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000, UTC)
    return datetime.fromisoformat(v)


def brief(row):
    raw = value(row.get("制图需求说明"))
    sections = [("产品与销售组合", "product"), ("使用说明", "instructions"), ("卖点总结", "selling_points"),
                ("画面重点", "visual_focus"), ("特殊要求与核实项", "requirements")]
    b = {key: "" for _, key in sections}
    for title, key in sections:
        m = re.search(r"【" + title + r"】(.*?)(?=【|$)", raw, re.DOTALL)
        if m:
            b[key] = m[1].strip()
    if not any(b.values()):
        b["product"] = raw
    b.update(image_count=int(row.get("计划制图数量") or 7), plans=[], references=[])
    return b


def identity(c, members, required=False, tenant="example"):
    subject = members[0].get("id") if isinstance(members, list) and members else ""
    person = c.execute("SELECT u.* FROM users u JOIN external_identities e ON e.user_id=u.id WHERE e.tenant=%s AND e.subject=%s", (tenant, subject)).fetchone() if subject else None
    if required and not person:
        raise ValueError("Unmapped legacy person: " + subject)
    return person


def migrate(c, settings, rows, *, commit=False):
    if (settings.environment, settings.base_id, settings.sku_table_id) != ("production", PRODUCTION_BASE, PRODUCTION_TABLE):
        raise ValueError("Production cutover target required")
    c.execute("SELECT pg_advisory_xact_lock(58017003)")
    result = {k: 0 for k in ("reset", "active", "history", "unapproved")}
    for row in rows:
        kind = cohort(row); result[kind] += 1
        rid = row["record_id"]
        old = c.execute("SELECT * FROM legacy_migration_records WHERE base_id=%s AND table_id=%s AND record_id=%s", (settings.base_id, settings.sku_table_id, rid)).fetchone()
        if old:
            if old["fingerprint"] != fingerprint(row):
                raise ValueError("Source changed after migration: " + rid)
            continue
        owner = identity(c, row.get("提交运营"), kind in ("reset", "active"), settings.tenant)
        maker = identity(c, row.get("美工"), kind == "active", settings.tenant)
        if kind == "active" and not (maker["active"] and maker["internal"] and ("artist" in maker["roles"] or "art_lead" in maker["roles"])):
            raise ValueError("Active maker is not eligible: " + rid)
        images = [a for a in row.get("成品图", []) if Path(a["name"]).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
        if kind == "active" and value(row.get("制图状态")) != "制图中" and not images:
            raise ValueError("No individual originals: " + rid)
        if not commit:
            continue
        item_id = doc_id = None
        uid = lambda suffix, rid=rid: uuid5(NAMESPACE_URL, settings.base_id + "/" + rid + "/" + suffix)
        if kind in ("reset", "active"):
            item_id, doc_id = uid("item"), uid("document")
            sources = [{"id": f"supplier-{n+1}", "kind": "supplier", "url": u} for n, u in enumerate(supplier_urls(row))]
            for n, url in enumerate(reference_urls(row)):
                sources.append({"id": f"reference-{n+1}", "kind": "competitor", "url": url})
            body = {"facts": {"sales_name": value(row.get("中文品名"))}, "sources": sources, "brief": brief(row)}
            c.execute("INSERT INTO work_items(id,environment,site,code,name,owner_id,group_name,review_status,review_snapshot,source_revision) VALUES(%s,'production',%s,%s,%s,%s,%s,'审核通过',%s,%s)",
                      (item_id, value(row.get("目标站点")), value(row.get("选品SKU")), value(row.get("中文品名")), owner["id"], value(row.get("运营小组")), Jsonb(row), fingerprint(row)))
            c.execute("INSERT INTO external_bindings VALUES('feishu',%s,'production',%s,%s,%s,%s)", (settings.tenant, settings.base_id, settings.sku_table_id, rid, item_id))
            c.execute("INSERT INTO content_documents(id,work_item_id,body) VALUES(%s,%s,%s)", (doc_id, item_id, Jsonb(body)))
            if kind == "active":
                version, task = uid("brief"), uid("task")
                c.execute("INSERT INTO content_versions(id,document_id,kind,body,created_by) VALUES(%s,%s,'brief',%s,%s)", (version, doc_id, Jsonb(body["brief"]), owner["id"]))
                c.execute("UPDATE content_documents SET brief_version=%s WHERE id=%s", (version, doc_id))
                c.execute("INSERT INTO artwork_tasks(id,document_id,brief_version,batch,state,assignee_id,assigned_at,started_at,due_date,created_at,legacy_takeover) VALUES(%s,%s,%s,1,%s,%s,%s,%s,%s,%s,true)",
                          (task, doc_id, version, STATES[value(row["制图状态"])], maker["id"], stamp(row.get("制图派单时间")), stamp(row.get("首次开工时间")), stamp(row["制图截止日期"]).astimezone(ZoneInfo("Asia/Shanghai")).date() if row.get("制图截止日期") else None, stamp(row.get("需求提交时间") or row.get("制图派单时间") or row["首次开工时间"])))
                asset_ids = []
                for a in row.get("成品图", []):
                    aid = uid("asset/" + a["file_token"])
                    ext = Path(a["name"]).suffix.lower()
                    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".zip": "application/zip", ".rar": "application/vnd.rar"}.get(ext, "application/octet-stream")
                    c.execute("INSERT INTO assets(id,work_item_id,uploaded_by,object_key,name,sha256,size_bytes,mime,source_url) VALUES(%s,%s,%s,%s,%s,'pending',%s,%s,%s)",
                              (aid, item_id, maker["id"], "legacy/" + str(aid), a["name"], max(1, a.get("size", 1)), mime, "feishu-legacy:" + a["file_token"]))
                    c.execute("INSERT INTO external_asset_bindings(asset_id,provider,base_id,file_token) VALUES(%s,'feishu',%s,%s)", (aid, settings.base_id, a["file_token"]))
                    c.execute("INSERT INTO legacy_attachment_links VALUES(%s,%s,%s,'成品图',%s)", (aid, Jsonb(a), rid, not mime.startswith("image/")))
                    if mime.startswith("image/"):
                        asset_ids.append(aid)
                if asset_ids:
                    did = uid("delivery")
                    state = value(row["制图状态"])
                    result_value = "rejected" if state == "待返修" else None
                    note = "原流程交付，切换时保留原图与原时间。" if state != "制图中" else "原流程历史附件；当前任务仍为制图中。"
                    c.execute("INSERT INTO artwork_deliveries(id,task_id,version,asset_ids,note,delivered_by,delivered_at,result,review_note) VALUES(%s,%s,1,%s,%s,%s,%s,%s,%s)",
                              (did, task, asset_ids, note, maker["id"], stamp(row.get("成品交付日期") or row["首次开工时间"]), result_value, value(row.get("验收意见"))))
                    if state != "制图中":
                        c.execute("UPDATE artwork_tasks SET current_delivery_id=%s WHERE id=%s", (did, task))
            c.execute("INSERT INTO audit_events(work_item_id,action,detail) VALUES(%s,'migration.takeover',%s)", (item_id, Jsonb({"cohort": kind, "record_id": rid, "source_state": value(row.get("制图状态"))})))
        c.execute("INSERT INTO legacy_migration_records(base_id,table_id,record_id,cohort,fingerprint,snapshot,work_item_id,artist_id,first_started_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (settings.base_id, settings.sku_table_id, rid, kind, fingerprint(row), Jsonb(row), item_id, maker["id"] if maker else None, stamp(row.get("首次开工时间"))))
    return result


def ensure_asset(c, settings, asset, gateway=None):
    """Cache a legacy original through the pinned Feishu attachment endpoint."""
    path = settings.storage_root / asset["object_key"]
    if path.is_file() and asset["sha256"] != "pending":
        return asset
    link = c.execute("SELECT * FROM legacy_attachment_links WHERE asset_id=%s", (asset["id"],)).fetchone()
    if not link:
        raise ValueError("Original file missing")
    from .feishu import FeishuGateway
    gateway = gateway or FeishuGateway(settings)
    row = gateway.record(link["record_id"])
    a = next((a for a in (row or {}).get(link["field_name"], []) if a.get("file_token") == link["attachment"]["file_token"]), link["attachment"])
    data = gateway.product_image(a, max_bytes=300_000_000)
    expected = link["attachment"].get("size")
    if expected and len(data) != expected:
        raise ValueError("Legacy original byte size mismatch")
    digest = hashlib.sha256(data).hexdigest()
    if asset["sha256"] != "pending" and asset["sha256"] != digest:
        raise ValueError("Legacy original checksum mismatch")
    path.parent.mkdir(parents=True, exist_ok=True)
    from uuid import uuid4
    temp = path.with_suffix('.' + uuid4().hex + '.tmp')
    try:
        temp.write_bytes(data)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    c.execute("UPDATE assets SET sha256=%s,size_bytes=%s WHERE id=%s", (digest, len(data), asset["id"]))
    return {**asset, "sha256": digest, "size_bytes": len(data)}
