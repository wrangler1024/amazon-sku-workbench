import hashlib
import json
import re
from datetime import UTC
from uuid import uuid4

import httpx
from psycopg.types.json import Jsonb

from .feishu import value
from .product_images import cache_product_images

FIELDS = [
    "选品SKU",
    "中文品名",
    "产品图片",
    "英文品名",
    "目标站点",
    "提交运营",
    "提交运营ID",
    "运营小组",
    "审核状态",
    "1688链接",
    "参考链接",
    "参考竞品ASIN",
    "制图需求说明",
    "编辑资料",
    "刊登状态",
    "制图加急",
]


def supplier_urls(row):
    found = []

    def visit(value):
        if isinstance(value, str):
            for url in re.findall(
                r"https?://(?:[a-zA-Z0-9-]+\.)*1688\.com/offer/\d+\.html(?:\?[^\s<>\"，；\[\]()]*)?", value
            ):
                url = re.sub(r"^http:", "https:", url)
                if url not in found:
                    found.append(url)
        elif isinstance(value, list):
            for entry in value:
                visit(entry)
        elif isinstance(value, dict):
            for key in ("link", "url", "text"):
                visit(value.get(key))

    for field in ("1688货源", "1688链接"):
        visit(row.get(field))
    return found[:20]


def reference_urls(row):
    # CLI snapshots can contain [URL](URL), while OpenAPI returns rich text.
    return list(dict.fromkeys(re.findall(r"https?://[^\s<>\"\[\]()，；]+", value(row.get("参考链接")))))[:20]


def import_records(db, gateway):
    rows = gateway.records(gateway.s.sku_table_id)
    count = 0
    images = []
    with db.connection() as c:
        c.execute("SELECT pg_advisory_xact_lock(58017003)")
        for row in rows:
            legacy = c.execute("SELECT cohort FROM legacy_migration_records WHERE base_id=%s AND table_id=%s AND record_id=%s", (gateway.s.base_id, gateway.s.sku_table_id, row["record_id"])).fetchone()
            if legacy and legacy["cohort"] == "history":
                continue
            members = row.get("提交运营") or []
            subject = (
                members[0].get("id", "") if isinstance(members, list) and members else value(row.get("提交运营ID"))
            )
            ident = c.execute(
                "SELECT u.* FROM users u JOIN external_identities e ON e.user_id=u.id WHERE e.provider IN ('feishu','feishu_union') AND e.tenant=%s AND e.subject=%s",
                (gateway.s.tenant, subject),
            ).fetchone()
            # Only the explicitly provisioned test roster can create linked work.
            if not ident or not ident["active"]:
                continue
            b = c.execute(
                "SELECT work_item_id FROM external_bindings WHERE provider=%s AND tenant=%s AND environment=%s AND base_id=%s AND table_id=%s AND record_id=%s",
                (
                    "feishu",
                    gateway.s.tenant,
                    gateway.s.environment,
                    gateway.s.base_id,
                    gateway.s.sku_table_id,
                    row["record_id"],
                ),
            ).fetchone()
            status = value(row.get("审核状态"))
            name = value(row.get("中文品名"))
            site = value(row.get("目标站点"))
            if not name or not site:
                continue
            if not b and status != "审核通过":
                continue
            item_id = b["work_item_id"] if b else uuid4()
            fingerprint = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            group = value(row.get("运营小组")) or (ident["groups"][0] if ident["groups"] else "")
            if not b:
                c.execute(
                    "INSERT INTO work_items(id,environment,site,code,name,owner_id,group_name,review_status,review_snapshot,source_revision) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        item_id,
                        gateway.s.environment,
                        site,
                        value(row.get("选品SKU")),
                        name,
                        ident["id"],
                        group,
                        status,
                        Jsonb(row),
                        fingerprint,
                    ),
                )
                c.execute(
                    "INSERT INTO external_bindings(provider,tenant,environment,base_id,table_id,record_id,work_item_id) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                    (
                        "feishu",
                        gateway.s.tenant,
                        gateway.s.environment,
                        gateway.s.base_id,
                        gateway.s.sku_table_id,
                        row["record_id"],
                        item_id,
                    ),
                )
                body = {
                    "facts": {
                        "product_type": "ordinary",
                        "sales_name": name,
                        "quantity": "",
                        "material": "",
                        "dimensions": "",
                        "included": "",
                        "excluded": "",
                        "uses": "",
                        "components": [],
                    },
                    "sources": [
                        *[
                            {"id": f"supplier-{i + 1}", "kind": "supplier", "url": url}
                            for i, url in enumerate(supplier_urls(row))
                        ],
                        *[
                            {"id": f"competitor-{i + 1}", "kind": "competitor", "url": url}
                            for i, url in enumerate(reference_urls(row))
                        ],
                    ],
                    "keywords": "",
                    "selling_points": "",
                }
                c.execute(
                    "INSERT INTO content_documents(id,work_item_id,body) VALUES(%s,%s,%s)",
                    (uuid4(), item_id, Jsonb(body)),
                )
            else:
                c.execute(
                    "UPDATE work_items SET name=%s,code=%s,site=%s,owner_id=%s,group_name=%s,review_status=%s,source_revision=%s,review_snapshot=%s,updated_at=now() WHERE id=%s",
                    (
                        name,
                        value(row.get("选品SKU")),
                        site,
                        ident["id"],
                        group,
                        status,
                        fingerprint,
                        Jsonb(row),
                        item_id,
                    ),
                )
            # Keep immutable review/source evidence; never overwrite confirmed product facts.
            c.execute(
                "INSERT INTO review_snapshots(id,work_item_id,fingerprint,body) VALUES(%s,%s,%s,%s) ON CONFLICT(work_item_id,fingerprint) DO NOTHING",
                (uuid4(), item_id, fingerprint, Jsonb(row)),
            )
            count += 1
            images.append((item_id, ident["id"], row.get("产品图片")))
    thumbnails = cache_product_images(db, gateway, images)
    return {"read": len(rows), "linked": count, "thumbnails": thumbnails}


def projection(c, item_id, gateway=None):
    doc = c.execute("SELECT * FROM content_documents WHERE work_item_id=%s", (item_id,)).fetchone()
    from .publication import current_publication

    pub = current_publication(c, doc["id"])
    task = c.execute(
        "SELECT * FROM artwork_tasks WHERE document_id=%s ORDER BY batch DESC LIMIT 1", (doc["id"],)
    ).fetchone()
    ref = {
        "id": str(doc["id"]),
        "s": doc["schema_version"],
        "r": doc["revision"],
        "l": str(doc["listing_version"] or ""),
        "p": str(pub["listing_version"]) if pub else "",
    }
    fields = {"编辑资料": json.dumps(ref, separators=(",", ":")), "刊登状态": ["已刊登" if pub else "未登记"]}
    legacy_reset = c.execute("SELECT cohort FROM legacy_migration_records WHERE work_item_id=%s", (item_id,)).fetchone()
    if not task and legacy_reset and legacy_reset["cohort"] == "reset":
        fields.update({"制图状态": ["待分配"], "制图进度留存": ["待分配"], "美工": None,
                       "美工留存": None, "制图派单时间": None, "制图截止日期": None,
                       "需求提交时间": None, "制图加急": False})
    if task:
        fields["制图加急"] = task["urgent"]
        fields["制图截止日期"] = task["due_date"].isoformat() if task["due_date"] else None
        # Enum mapping is intentionally centralized and schema-verified by doctor.
        states = {
            "pool": "待分配",
            "waiting": "待制图",
            "working": "制图中",
            "pending": "待验收",
            "revision": "待返修",
            "accepted": "已验收",
            "withdrawn": "待分配",
        }
        fields["制图状态"] = [states[task["state"]]]
        if task["assignee_id"]:
            ident = c.execute(
                "SELECT subject FROM external_identities WHERE provider=%s AND user_id=%s",
                ("feishu_union" if gateway and gateway.s.feishu_mode == "openapi" else "feishu", task["assignee_id"]),
            ).fetchone()
            fields["美工"] = [{"id": ident["subject"]}] if ident else None
        else:
            fields["美工"] = None
    if task:
        fields["制图进度留存"] = fields["制图状态"]
        fields["制图承接方"] = ["内部美工"]
        fields["承接方留存"] = ["内部美工"]
        fields["美工留存"] = fields["美工"]

        def when(v):
            if not v:
                return None
            return int(v.timestamp() * 1000) if gateway and gateway.s.feishu_mode == "openapi" else v.isoformat()

        if not task.get("legacy_takeover"):
            fields["需求提交时间"] = when(task["created_at"]) if task["state"] != "withdrawn" else None
        fields["制图派单时间"] = when(task["assigned_at"])
        fields["首次开工时间"] = when(task["started_at"])
        if gateway and gateway.s.feishu_mode == "openapi" and task["due_date"]:
            from datetime import datetime

            fields["制图截止日期"] = int(
                datetime.combine(task["due_date"], datetime.min.time(), tzinfo=UTC).timestamp() * 1000
            )
        brief = c.execute("SELECT body FROM content_versions WHERE id=%s", (task["brief_version"],)).fetchone()["body"]
        fields["计划制图数量"] = brief.get("image_count") or len(brief.get("plans") or []) or 7
        fields["制图需求说明"] = "\n\n".join(
            str(brief.get(k, "")) for k in ["product", "instructions", "selling_points", "visual_focus", "requirements"]
        )
        if task.get("legacy_takeover"):
            legacy_source = c.execute(
                "SELECT snapshot FROM legacy_migration_records WHERE work_item_id=%s", (item_id,)
            ).fetchone()["snapshot"]
            # The migrated brief is immutable; preserve the original formatting and date instant.
            fields["制图需求说明"] = value(legacy_source.get("制图需求说明"))
            fields["计划制图数量"] = legacy_source.get("计划制图数量")
            from .legacy import stamp

            fields["制图截止日期"] = when(stamp(legacy_source.get("制图截止日期")))
        if task["current_delivery_id"]:
            delivery = c.execute(
                "SELECT * FROM artwork_deliveries WHERE id=%s", (task["current_delivery_id"],)
            ).fetchone()
            fields["成品交付日期"] = when(delivery["delivered_at"])
            fields["验收结果"] = [{"accepted": "通过", "rejected": "需修改"}.get(delivery["result"], "未验收")]
            image_notes = delivery.get("image_notes") or {}
            names = {
                str(a["id"]): a["name"]
                for a in c.execute("SELECT id,name FROM assets WHERE id=ANY(%s)", (delivery["asset_ids"],)).fetchall()
            }
            fields["验收意见"] = "\n".join(
                [delivery["review_note"] or "", *[names.get(k, k) + "：" + v for k, v in image_notes.items() if v]]
            ).strip()
            if gateway and gateway.s.feishu_mode == "openapi":
                from .feishu import upload_asset

                tokens = []
                for aid in delivery["asset_ids"]:
                    binding = c.execute(
                        "SELECT file_token FROM external_asset_bindings WHERE asset_id=%s AND provider='feishu' AND base_id=%s",
                        (aid, gateway.s.base_id),
                    ).fetchone()
                    if binding:
                        token = binding["file_token"]
                    else:
                        asset = c.execute("SELECT * FROM assets WHERE id=%s", (aid,)).fetchone()
                        token = upload_asset(gateway, asset, gateway.s.storage_root)
                        c.execute(
                            "INSERT INTO external_asset_bindings(asset_id,provider,base_id,file_token) VALUES(%s,'feishu',%s,%s)",
                            (aid, gateway.s.base_id, token),
                        )
                    tokens.append({"file_token": token})
                # Native ZIP/RAR and older originals remain in the cell as historical evidence.
                legacy = c.execute("SELECT snapshot FROM legacy_migration_records WHERE work_item_id=%s", (item_id,)).fetchone()
                if legacy:
                    present = {x["file_token"] for x in tokens}
                    for old in legacy["snapshot"].get("成品图", []):
                        if old["file_token"] not in present:
                            tokens.append({"file_token": old["file_token"]})
                            present.add(old["file_token"])
                fields["成品图"] = tokens
    return fields


def sync_outbox(db, gateway):
    if not gateway.s.sync_enabled:
        return 0
    processed = 0
    # Serialize projections, including different workers; original business transactions never wait on Feishu.
    with db.connection() as c:
        if not c.execute("SELECT pg_try_advisory_xact_lock(58017004) AS locked").fetchone()["locked"]:
            return 0
        rows = c.execute(
            "SELECT * FROM sync_outbox WHERE state IN ('queued','failed') AND attempts<5 ORDER BY id LIMIT 20 FOR UPDATE SKIP LOCKED"
        ).fetchall()
        for row in rows:
            binding = c.execute(
                "SELECT * FROM external_bindings WHERE work_item_id=%s AND provider='feishu'", (row["work_item_id"],)
            ).fetchone()
            if not binding:
                c.execute("UPDATE sync_outbox SET state='succeeded',finished_at=now() WHERE id=%s", (row["id"],))
                continue
            if binding["base_id"] != gateway.s.base_id or binding["table_id"] != gateway.s.sku_table_id:
                raise RuntimeError("Projection target mismatch")
            try:
                fields = projection(c, row["work_item_id"], gateway)
                if row["payload"].get("legacy_reference_only"):
                    legacy = c.execute("SELECT cohort FROM legacy_migration_records WHERE work_item_id=%s", (row["work_item_id"],)).fetchone()
                    if not legacy or legacy["cohort"] != "active":
                        raise RuntimeError("Legacy reference projection requires an active takeover")
                    fields = {k: fields[k] for k in ("编辑资料", "刊登状态")}
                gateway.update(binding["record_id"], fields)
                # Field readback validates that text was not silently dropped/truncated.
                target = gateway.record(binding["record_id"]) if hasattr(gateway, "record") else next((r for r in gateway.records(gateway.s.sku_table_id) if r["record_id"] == binding["record_id"]), None)
                verify_projection(target, fields)
                c.execute(
                    "UPDATE sync_outbox SET state='succeeded',attempts=attempts+1,error=NULL,finished_at=now() WHERE id=%s",
                    (row["id"],),
                )
                processed += 1
            except Exception as e:  # noqa: BLE001 -- Isolate job/resource failures and return a sanitized result.
                c.execute(
                    "UPDATE sync_outbox SET state='failed',attempts=attempts+1,error=%s WHERE id=%s",
                    (type(e).__name__ + ": " + str(e)[:200], row["id"]),
                )
    return processed


def refresh_identities(db, gateway):
    if gateway.s.feishu_mode != "openapi":
        return 0
    from .feishu import user_profile

    with db.connection() as c:
        rows = c.execute(
            "SELECT u.id,e.subject FROM users u JOIN external_identities e ON e.user_id=u.id WHERE e.provider='feishu_union' AND (u.verified_at IS NULL OR u.verified_at<now()-interval '1 hour')"
        ).fetchall()
    updated = 0
    for row in rows:
        try:
            profile = user_profile(gateway, row["subject"])
            status = profile.get("status", {})
            required = {"is_frozen", "is_resigned", "is_activated", "is_exited"}
            if not required.issubset(status):
                continue
            active = status["is_activated"] and not any(status[k] for k in ("is_frozen", "is_resigned", "is_exited"))
            with db.connection() as c:
                c.execute("UPDATE users SET active=%s,verified_at=now() WHERE id=%s", (active, row["id"]))
                if profile.get("open_id"):
                    c.execute(
                        "INSERT INTO external_identities(provider,tenant,subject,user_id) VALUES('feishu',%s,%s,%s) ON CONFLICT DO NOTHING",
                        (gateway.s.tenant, profile["open_id"], row["id"]),
                    )
            updated += 1
        except (RuntimeError, httpx.HTTPError):
            continue
    return updated


def verify_projection(target, fields):
    if not target:
        raise RuntimeError("Feishu record readback missing")
    for key, expected in fields.items():
        actual = target.get(key)
        if key in ("美工", "美工留存"):
            normalize = lambda v: sorted(x["id"] for x in (v or []))
        elif key == "成品图":
            normalize = lambda v: sorted(x["file_token"] for x in (v or []))
        elif key in ("制图派单时间", "首次开工时间", "成品交付日期", "需求提交时间", "制图截止日期"):
            from .legacy import stamp
            normalize = lambda v: int(stamp(v).timestamp() * 1000) if v else None
        elif key == "制图加急":
            normalize = bool
        else:
            normalize = value
        if normalize(actual) != normalize(expected):
            raise RuntimeError("Feishu readback mismatch: " + key)
