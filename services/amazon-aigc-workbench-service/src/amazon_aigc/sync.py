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


def owner_identity(row):
    members = row.get("提交运营") or []
    if isinstance(members, list) and members:
        first = members[0]
        if isinstance(first, dict):
            return value(first.get("id")), value(first.get("name"))
        if isinstance(first, str):
            return first, ""
    return value(row.get("提交运营ID")).strip(), ""


def _identity(c, tenant, subject):
    if not subject:
        return None
    return c.execute(
        "SELECT u.* FROM users u JOIN external_identities e ON e.user_id=u.id "
        "WHERE e.provider IN ('feishu','feishu_union') AND e.tenant=%s AND e.subject=%s LIMIT 1",
        (tenant, subject),
    ).fetchone()


def _source_owner_state(c, gateway, record_id):
    return c.execute(
        "SELECT * FROM source_owner_state WHERE base_id=%s AND table_id=%s AND record_id=%s",
        (gateway.s.base_id, gateway.s.sku_table_id, record_id),
    ).fetchone()


def _save_source_owner_state(c, gateway, row, subject, name):
    c.execute(
        "INSERT INTO source_owner_state(base_id,table_id,record_id,owner_subject,owner_name,submitter_id,"
        "group_name,approval_group,sku,record_number,site,updated_at) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now()) "
        "ON CONFLICT(base_id,table_id,record_id) DO UPDATE SET owner_subject=excluded.owner_subject,"
        "owner_name=excluded.owner_name,submitter_id=excluded.submitter_id,group_name=excluded.group_name,"
        "approval_group=excluded.approval_group,sku=excluded.sku,record_number=excluded.record_number,"
        "site=excluded.site,updated_at=now()",
        (
            gateway.s.base_id,
            gateway.s.sku_table_id,
            row["record_id"],
            subject,
            name,
            value(row.get("提交运营ID")).strip(),
            value(row.get("运营小组")),
            value(row.get("审批小组")),
            value(row.get("选品SKU")),
            value(row.get("记录序号")),
            value(row.get("目标站点")),
        ),
    )


def _queue_owner_handover(c, gateway, row, previous, subject, name, work_item_id, batch_id):
    change_key = hashlib.sha256(
        f"{row['record_id']}|{previous['owner_subject'] or ''}|{subject}|{previous['updated_at']}".encode()
    ).hexdigest()
    c.execute(
        "INSERT INTO owner_handover_logs(id,batch_id,change_key,base_id,table_id,record_id,work_item_id,"
        "sku,record_number,site,old_owner_subject,old_owner_name,old_submitter_id,old_group,old_approval_group,"
        "new_owner_subject,new_owner_name) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(change_key) DO NOTHING",
        (
            uuid4(),
            batch_id,
            change_key,
            gateway.s.base_id,
            gateway.s.sku_table_id,
            row["record_id"],
            work_item_id,
            value(row.get("选品SKU")),
            value(row.get("记录序号")),
            value(row.get("目标站点")),
            previous["owner_subject"],
            previous["owner_name"],
            previous["submitter_id"],
            previous["group_name"],
            previous["approval_group"],
            subject,
            name,
        ),
    )


def import_records(db, gateway):
    rows = gateway.records(gateway.s.sku_table_id)
    submitter_ids = _submitter_id_directory(rows)
    count = 0
    images = []
    batch_id = uuid4()
    with db.connection() as c:
        c.execute("SELECT pg_advisory_xact_lock(58017003)")
        for row in rows:
            legacy = c.execute("SELECT cohort FROM legacy_migration_records WHERE base_id=%s AND table_id=%s AND record_id=%s", (gateway.s.base_id, gateway.s.sku_table_id, row["record_id"])).fetchone()
            if legacy and legacy["cohort"] == "history":
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
            subject, owner_name = owner_identity(row)
            previous = _source_owner_state(c, gateway, row["record_id"])
            owner_changed = bool(previous and subject and subject != previous["owner_subject"])
            if owner_changed:
                _queue_owner_handover(
                    c,
                    gateway,
                    row,
                    previous,
                    subject,
                    owner_name,
                    b["work_item_id"] if b else None,
                    batch_id,
                )
            state_stale = previous and any(
                (
                    value(row.get("提交运营ID")).strip() != (previous["submitter_id"] or ""),
                    value(row.get("运营小组")) != (previous["group_name"] or ""),
                    value(row.get("审批小组")) != (previous["approval_group"] or ""),
                    value(row.get("选品SKU")) != (previous["sku"] or ""),
                    value(row.get("记录序号")) != (previous["record_number"] or ""),
                    value(row.get("目标站点")) != (previous["site"] or ""),
                )
            )
            if subject and (previous is None or owner_changed or state_stale):
                _save_source_owner_state(c, gateway, row, subject, owner_name)
            status = value(row.get("审核状态"))
            name = value(row.get("中文品名"))
            site = value(row.get("目标站点"))
            if not name or not site:
                continue
            if not b and status != "审核通过":
                continue
            ident = _identity(c, gateway.s.tenant, subject)
            # Only the explicitly provisioned test roster can create or transfer linked work.
            if not ident or not ident["active"] or not ident["groups"]:
                continue
            # A handover is completed by process_owner_handovers only after the
            # Base projection has been written and read back successfully.
            if owner_changed and not b:
                continue
            item_id = b["work_item_id"] if b else uuid4()
            fingerprint = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            group = value(row.get("运营小组")) or ident["groups"][0]
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
            elif owner_changed:
                c.execute(
                    "UPDATE work_items SET editor_mode=CASE WHEN %s IN ('DE','FR','ES') THEN editor_mode ELSE 'single' END,name=%s,code=%s,site=%s,review_status=%s,source_revision=%s,"
                    "review_snapshot=%s,updated_at=now() WHERE id=%s",
                    (
                        site,
                        name,
                        value(row.get("选品SKU")),
                        site,
                        status,
                        fingerprint,
                        Jsonb(row),
                        item_id,
                    ),
                )
            else:
                c.execute(
                    "UPDATE work_items SET editor_mode=CASE WHEN %s IN ('DE','FR','ES') THEN editor_mode ELSE 'single' END,name=%s,code=%s,site=%s,owner_id=%s,group_name=%s,review_status=%s,source_revision=%s,review_snapshot=%s,updated_at=now() WHERE id=%s",
                    (
                        site,
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
    owner_handovers = process_owner_handovers(db, gateway, rows, submitter_ids)
    thumbnails = cache_product_images(db, gateway, images)
    return {
        "read": len(rows),
        "linked": count,
        "owner_handovers": owner_handovers,
        "thumbnails": thumbnails,
    }


def _when(value, gateway):
    if not value:
        return None
    if gateway.s.feishu_mode == "openapi":
        return int(value.timestamp() * 1000)
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _person_identity(c, tenant, subject):
    if not subject:
        return None
    target = c.execute(
        "SELECT user_id FROM external_identities WHERE tenant=%s AND subject=%s LIMIT 1",
        (tenant, subject),
    ).fetchone()
    if not target:
        return None
    rows = c.execute(
        "SELECT u.id,u.name,u.active,u.internal,u.groups,"
        "max(e.subject) FILTER (WHERE e.provider='feishu') AS open_id,"
        "max(e.subject) FILTER (WHERE e.provider='feishu_union') AS union_id "
        "FROM users u JOIN external_identities e ON e.user_id=u.id "
        "WHERE e.tenant=%s AND u.id=%s GROUP BY u.id",
        (tenant, target["user_id"]),
    ).fetchall()
    if not rows:
        return None
    row = next((r for r in rows if r["active"]), rows[0])
    open_id = row["open_id"] or (subject if subject.startswith("ou_") else "")
    union_id = row["union_id"] or (subject if subject.startswith("on_") else "")
    return {
        "user_id": row["id"],
        "name": row["name"],
        "active": row["active"],
        "internal": row["internal"],
        "groups": row["groups"] or [],
        "open_id": open_id,
        "union_id": union_id,
    }


def _submitter_id_directory(rows):
    counts = {}
    for row in rows:
        creators = row.get("创建人") or []
        if not isinstance(creators, list) or not creators or not isinstance(creators[0], dict):
            continue
        creator_id = value(creators[0].get("id"))
        submitter_id = value(row.get("提交运营ID")).strip()
        if creator_id and submitter_id:
            counts.setdefault(creator_id, {}).setdefault(submitter_id, 0)
            counts[creator_id][submitter_id] += 1
    return {
        creator_id: max(values, key=values.get)
        for creator_id, values in counts.items()
    }


def _resolve_owner(c, gateway, subject, roster, submitter_ids):
    person = _person_identity(c, gateway.s.tenant, subject)
    if not person:
        raise RuntimeError("提交运营未同步到工作台人员表")
    if not person["active"] or not person["internal"]:
        raise RuntimeError("提交运营已停用或不是内部人员")
    configured = roster.get(person["union_id"]) or roster.get(subject)
    if not configured:
        raise RuntimeError("提交运营未在插件人员权限配置中启用")
    if "operator" not in configured["roles"] or not configured["groups"]:
        raise RuntimeError("提交运营角色不是运营或没有所属小组")
    person["name"] = configured.get("name") or person["name"]
    person["groups"] = configured["groups"]
    person["group"] = configured["groups"][0]
    person["submitter_id"] = submitter_ids.get(subject) or submitter_ids.get(person["open_id"])
    if not person["submitter_id"]:
        raise RuntimeError("缺少该运营的Base应用身份映射，请先让本人保存一条选品草稿")
    return person


def _log_labels(log):
    base = {
        "pending": "待同步",
        "succeeded": "成功",
        "failed": "失败",
        "superseded": "已取消",
        "not_applicable": "无需同步",
    }
    workbench = dict(base)
    workbench["not_applicable"] = "无需同步"
    overall = {
        "pending": "待处理",
        "succeeded": "成功",
        "failed": "失败",
        "superseded": "已取消",
    }
    return base[log["base_state"]], workbench[log["workbench_state"]], overall[log["state"]]


def _base_log_fields(gateway, log, old_person, new_person):
    base_label, workbench_label, overall_label = _log_labels(log)
    return {
        "日志ID": str(log["id"]),
        "交接批次": str(log["batch_id"]),
        "交接时间": _when(log["created_at"], gateway),
        "完成时间": _when(log["finished_at"], gateway),
        "记录序号": log["record_number"] or "",
        "选品SKU": log["sku"] or "",
        "目标站点": log["site"] or "",
        "原提交运营": [{"id": old_person["union_id"]}] if old_person and old_person.get("union_id") else None,
        "新提交运营": [{"id": new_person["union_id"]}] if new_person and new_person.get("union_id") else None,
        "原提交运营ID": log["old_submitter_id"] or "",
        "新提交运营ID": log["new_submitter_id"] or "",
        "原运营小组": log["old_group"] or "",
        "新运营小组": log["new_group"] or "",
        "原审批小组": log["old_approval_group"] or "",
        "新审批小组": log["new_approval_group"] or "",
        "Base字段同步": base_label,
        "工作台同步": workbench_label,
        "交接状态": overall_label,
        "失败原因": (log["error"] or "")[:2000],
        "操作来源": "后台同步",
        "重试次数": log["attempts"],
    }


def _sync_base_log(gateway, log, old_person, new_person):
    if not getattr(gateway.s, "handover_log_table_id", ""):
        return None
    fields = _base_log_fields(gateway, log, old_person, new_person)
    record_id = log["base_log_record_id"]
    if record_id:
        gateway.update_handover_log(record_id, fields)
        return record_id
    existing = None
    try:
        existing = next(
            (r for r in gateway.records(gateway.s.handover_log_table_id) if value(r.get("日志ID")) == fields["日志ID"]),
            None,
        )
    except Exception:  # noqa: BLE001 -- creation below remains the durable path
        existing = None
    if existing:
        gateway.update_handover_log(existing["record_id"], fields)
        return existing["record_id"]
    return gateway.create_handover_log(fields)


def process_owner_handovers(db, gateway, rows, submitter_ids=None):
    with db.connection() as c:
        pending = c.execute(
            "SELECT * FROM owner_handover_logs WHERE base_id=%s AND table_id=%s "
            "AND state IN ('pending','failed') AND attempts<20 ORDER BY created_at,id LIMIT 20",
            (gateway.s.base_id, gateway.s.sku_table_id),
        ).fetchall()
    if not pending:
        return {"processed": 0, "succeeded": 0, "failed": 0}
    by_record = {row["record_id"]: row for row in rows}
    submitter_ids = submitter_ids or _submitter_id_directory(rows)
    roster = {}
    roster_error = ""
    personnel_table_id = getattr(gateway.s, "personnel_table_id", "")
    if not personnel_table_id:
        roster_error = "插件人员权限配置表未配置"
    else:
        try:
            from .roster_sync import effective_rows

            roster = effective_rows(gateway.records(personnel_table_id))
        except Exception as exc:  # noqa: BLE001 -- report a bounded, sanitized handover failure
            roster_error = "人员配置读取失败: " + type(exc).__name__
    result = {"processed": 0, "succeeded": 0, "failed": 0}
    for candidate in pending:
        with db.connection() as c:
            log = c.execute("SELECT * FROM owner_handover_logs WHERE id=%s", (candidate["id"],)).fetchone()
        if not log:
            continue
        row = by_record.get(log["record_id"])
        old_person = None
        new_person = None
        error = ""
        base_state = "pending"
        workbench_state = "pending"
        state = "failed"
        if not row:
            base_state = workbench_state = "superseded"
            state = "superseded"
            error = "源记录已从登记表删除"
        else:
            current_subject, _ = owner_identity(row)
            if current_subject != log["new_owner_subject"]:
                base_state = workbench_state = "superseded"
                state = "superseded"
                error = "提交运营已再次变更，本次交接取消"
            else:
                with db.connection() as c:
                    old_person = _person_identity(c, gateway.s.tenant, log["old_owner_subject"])
                try:
                    if roster_error:
                        raise RuntimeError(roster_error)
                    with db.connection() as c:
                        new_person = _resolve_owner(
                            c, gateway, log["new_owner_subject"], roster, submitter_ids
                        )
                    fields = {
                        "提交运营ID": new_person["submitter_id"],
                        "运营小组": new_person["group"],
                        "审批小组": new_person["group"],
                    }
                    gateway.update(log["record_id"], fields)
                    verify_projection(gateway.record(log["record_id"]), fields)
                    base_state = "succeeded"
                except Exception as exc:  # noqa: BLE001 -- persist a sanitized failure and retry
                    base_state = "failed"
                    error = type(exc).__name__ + ": " + str(exc)[:500]
                if log["work_item_id"] and new_person and base_state == "succeeded":
                    with db.connection() as c:
                        changed = c.execute(
                            "UPDATE work_items SET owner_id=%s,group_name=%s,updated_at=now() "
                            "WHERE id=%s RETURNING id",
                            (new_person["user_id"], new_person["group"], log["work_item_id"]),
                        ).fetchone()
                    workbench_state = "succeeded" if changed else "failed"
                elif log["work_item_id"]:
                    workbench_state = "failed"
                else:
                    workbench_state = "not_applicable"
                if base_state == "succeeded" and workbench_state in ("succeeded", "not_applicable"):
                    state = "succeeded"
                else:
                    state = "failed"
                    if not error:
                        error = "工作台负责人更新失败"
        attempts = log["attempts"] + 1
        old_submitter_id = submitter_ids.get(log["old_owner_subject"]) or log["old_submitter_id"]
        with db.connection() as c:
            c.execute(
                "UPDATE owner_handover_logs SET base_state=%s,workbench_state=%s,state=%s,attempts=%s,"
                "error=%s,old_submitter_id=%s,new_submitter_id=%s,new_group=%s,new_approval_group=%s,finished_at=now(),"
                "updated_at=now() WHERE id=%s",
                (
                    base_state,
                    workbench_state,
                    state,
                    attempts,
                    error[:2000] or None,
                    old_submitter_id,
                    new_person["submitter_id"] if new_person else None,
                    new_person["group"] if new_person else None,
                    new_person["group"] if new_person else None,
                    log["id"],
                ),
            )
            updated = c.execute("SELECT * FROM owner_handover_logs WHERE id=%s", (log["id"],)).fetchone()
        try:
            record_id = _sync_base_log(gateway, updated, old_person, new_person)
            if record_id and record_id != updated["base_log_record_id"]:
                with db.connection() as c:
                    c.execute(
                        "UPDATE owner_handover_logs SET base_log_record_id=%s WHERE id=%s",
                        (record_id, updated["id"]),
                    )
        except Exception as exc:  # noqa: BLE001 -- the PostgreSQL log remains the source of truth
            with db.connection() as c:
                c.execute(
                    "UPDATE owner_handover_logs SET state='failed',error=%s,updated_at=now() WHERE id=%s",
                    (("交接日志镜像失败: " + type(exc).__name__ + ": " + str(exc)[:400]), updated["id"]),
                )
            state = "failed"
        result["processed"] += 1
        result["succeeded" if state == "succeeded" else "failed"] += 1
    return result


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
