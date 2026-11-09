"""Business commands own transactions. Feishu is only an asynchronous projection."""

import hashlib
import json
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from .feishu import value
from .manual_brief import ManualBrief
from .schemas import Facts, Listing


def fail(message, status=409):
    raise HTTPException(status, message)


def serial(value):
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def public_item(item, *, include_sources=False):
    """Keep the immutable review snapshot private; expose only editor inputs."""
    result = {key: value for key, value in item.items() if key != "review_snapshot"}
    snapshot = item.get("review_snapshot") or {}
    result["task_number"] = value(snapshot.get("记录序号")).strip()
    if include_sources:
        result["review_snapshot"] = {
            key: snapshot[key] for key in ("1688货源", "1688链接") if key in snapshot
        }
    return result


def is_admin(user):
    return "admin" in user["roles"]


def has(user, role):
    return role in user["roles"] or is_admin(user)


def active_artist(user):
    from .roster_sync import fresh

    if user.get("read_only_preview") or not fresh(user):
        return False
    verified = user.get("verified_at")
    return (
        user["active"]
        and user["internal"]
        and ("artist" in user["roles"] or "art_lead" in user["roles"])
        and verified
        and (datetime.now(UTC) - verified).total_seconds() < 86400
    )


def can_read(c, user, item):
    from .roster_sync import fresh

    if not fresh(user):
        return False
    if user.get("preview_artist_id") or user.get("preview_all_artists"):
        latest = item.get("task") if "task" in item else c.execute(
            "SELECT t.* FROM artwork_tasks t JOIN content_documents d ON d.id=t.document_id WHERE d.work_item_id=%s ORDER BY t.batch DESC LIMIT 1",
            (item["id"],),
        ).fetchone()
        if not is_admin(user) or not latest or latest["state"] in ("pool", "withdrawn"):
            return False
        if user.get("preview_all_artists"):
            assignee = c.execute("SELECT * FROM users WHERE id=%s", (latest["assignee_id"],)).fetchone()
            if not assignee or not active_artist(assignee):
                return False
        elif str(latest["assignee_id"]) != user["preview_artist_id"]:
            return False
    if is_admin(user) or str(item["owner_id"]) == str(user["id"]):
        return True
    if "supervisor" in user["roles"] and item["group_name"] in user["groups"]:
        return True
    if "art_lead" in user["roles"]:
        return bool(
            c.execute(
                "SELECT 1 FROM artwork_tasks t JOIN content_documents d ON d.id=t.document_id WHERE d.work_item_id=%s AND t.state<>%s",
                (item["id"], "withdrawn"),
            ).fetchone()
        )
    if "artist" in user["roles"]:
        return bool(
            c.execute(
                "SELECT 1 FROM artwork_tasks t JOIN content_documents d ON d.id=t.document_id WHERE d.work_item_id=%s AND (t.assignee_id=%s OR t.state='pool')",
                (item["id"], user["id"]),
            ).fetchone()
        )
    return False


def get_item(c, user, item_id, write=False):
    item = c.execute("SELECT * FROM work_items WHERE id=%s", (item_id,)).fetchone()
    if not item or not can_read(c, user, item):
        fail("记录不存在或无访问权限", 404)
    if write and user.get("read_only_preview"):
        fail("管理员只读预览不允许修改", 403)
    if write and not (is_admin(user) or str(item["owner_id"]) == str(user["id"])):
        fail("仅内容负责人可修改", 403)
    if write and item["review_status"] != "审核通过":
        fail("商品尚未通过评审")
    return item


def get_doc(c, item_id, revision=None):
    row = c.execute("SELECT * FROM content_documents WHERE work_item_id=%s FOR UPDATE", (item_id,)).fetchone()
    if not row:
        fail("资料不存在", 404)
    if revision is not None and row["revision"] != revision:
        fail("内容已被更新，请重新读取后保存")
    return row


def event(c, item_id, user, action, detail):
    row = c.execute(
        "INSERT INTO audit_events(work_item_id,actor_id,action,detail) VALUES(%s,%s,%s,%s) RETURNING id",
        (item_id, user["id"], action, Jsonb(serial(detail))),
    ).fetchone()
    if action.startswith("artwork."):
        from .notifications import enqueue_event

        enqueue_event(c, row["id"], item_id, user, action, detail)


def project(c, item_id):
    c.execute("INSERT INTO sync_outbox(work_item_id,payload) VALUES(%s,%s)", (item_id, Jsonb({"refresh": True})))


def idempotent(c, user, key, request, operation):
    if not key or len(key) > 160:
        fail("写操作缺少有效幂等键", 400)
    digest = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    lock = int.from_bytes(hashlib.sha256((str(user["id"]) + key).encode()).digest()[:8], "big", signed=True)
    c.execute("SELECT pg_advisory_xact_lock(%s)", (lock,))
    old = c.execute("SELECT * FROM idempotency_keys WHERE actor_id=%s AND key=%s", (user["id"], key)).fetchone()
    if old:
        if old["request_hash"] != digest:
            fail("幂等键对应不同请求")
        return old["result"]
    result = serial(operation())
    c.execute(
        "INSERT INTO idempotency_keys(actor_id,key,request_hash,result) VALUES(%s,%s,%s,%s)",
        (user["id"], key, digest, Jsonb(result)),
    )
    return result


def legacy_archived(doc, task, section):
    if not task or not task.get("legacy_takeover") or task["state"] != "accepted":
        return False
    request = (doc.get("body") or doc).get("legacy_edit_request") or {}
    return request.get("task_id") != str(task["id"]) or section not in request.get("sections", [])


def editing_status(doc, task=None):
    """Pending content work is independent of publication and artwork progress."""
    todos = []
    if task and task.get("legacy_takeover") and task["state"] in ("working", "pending", "revision"):
        return {"pending": False, "todos": [], "stage": "facts"}
    if not doc["facts_version"] and not legacy_archived(doc, task, "facts"):
        todos.append({"stage": "facts", "label": "待核对资料"})
    if not doc["listing_version"] and not legacy_archived(doc, task, "listing"):
        todos.append({"stage": "listing", "label": "待确认文案"})
    submitted = (
        task
        and task["state"] != "withdrawn"
        and doc["brief_version"]
        and str(task["brief_version"]) == str(doc["brief_version"])
    )
    if not submitted and not legacy_archived(doc, task, "brief"):
        todos.append({"stage": "brief", "label": "待提交图需" if doc["brief_version"] else "待确认图需"})
    return {"pending": bool(todos), "todos": todos, "stage": todos[0]["stage"] if todos else "listing"}


def detail(c, user, item_id, compact=False):
    item = get_item(c, user, item_id)
    doc = c.execute("SELECT * FROM content_documents WHERE work_item_id=%s", (item_id,)).fetchone()
    versions = c.execute(
        ("SELECT * FROM content_versions WHERE document_id=%s AND (id=ANY(%s) OR id IN (SELECT brief_version FROM artwork_tasks WHERE document_id=%s ORDER BY batch DESC LIMIT 1)) ORDER BY created_at DESC" if compact else "SELECT * FROM content_versions WHERE document_id=%s ORDER BY created_at DESC"),
        (doc["id"],[v for v in [doc["facts_version"],doc["listing_version"],doc["brief_version"]] if v],doc["id"]) if compact else (doc["id"],)
    ).fetchall()
    tasks = c.execute("SELECT * FROM artwork_tasks WHERE document_id=%s ORDER BY batch DESC", (doc["id"],)).fetchall()
    deliveries = c.execute(
        "SELECT d.* FROM artwork_deliveries d JOIN artwork_tasks t ON t.id=d.task_id WHERE t.document_id=%s ORDER BY d.delivered_at DESC",
        (doc["id"],),
    ).fetchall()
    publications = c.execute(
        "SELECT p.*,u.name AS created_by_name,w.name AS withdrawn_by_name FROM publication_records p "
        "LEFT JOIN users u ON u.id=p.created_by LEFT JOIN users w ON w.id=p.withdrawn_by "
        "WHERE p.document_id=%s ORDER BY p.created_at DESC,p.id DESC",
        (doc["id"],),
    ).fetchall()
    receipts = c.execute(
        "SELECT r.*,u.name AS created_by_name,w.name AS withdrawn_by_name,d.version,d.asset_ids,t.batch "
        "FROM image_receipts r "
        "JOIN artwork_deliveries d ON d.id=r.delivery_id JOIN artwork_tasks t ON t.id=d.task_id "
        "LEFT JOIN users u ON u.id=r.created_by LEFT JOIN users w ON w.id=r.withdrawn_by "
        "WHERE t.document_id=%s ORDER BY r.created_at DESC,r.id DESC",
        (doc["id"],),
    ).fetchall()
    assets = c.execute(
        "SELECT id,name,mime,width,height,size_bytes,sha256,source_url FROM assets WHERE work_item_id=%s", (item_id,)
    ).fetchall()
    sources = c.execute(
        ("SELECT * FROM (SELECT DISTINCT ON(url) * FROM source_snapshots WHERE work_item_id=%s ORDER BY url,created_at DESC) latest ORDER BY created_at DESC" if compact else "SELECT * FROM source_snapshots WHERE work_item_id=%s ORDER BY created_at DESC"), (item_id,)
    ).fetchall()
    from .product_facts import source_fact_hints

    for source in sources:
        source["fact_hints"] = source_fact_hints(source["body"])
        from .media_previews import source_image_paths
        source["image_previews"] = source_image_paths(source)
    jobs = c.execute(
        ("SELECT id,kind,payload,state,result,error,created_at,finished_at FROM jobs WHERE work_item_id=%s ORDER BY created_at DESC LIMIT 10" if compact else "SELECT id,kind,payload,state,result,error,created_at,finished_at FROM jobs WHERE work_item_id=%s ORDER BY created_at DESC LIMIT 100"),
        (item_id,),
    ).fetchall()
    from .source_pipeline import public_job

    jobs = [public_job(c, job) for job in jobs]
    outbox = c.execute(
        "SELECT id,state,error FROM sync_outbox WHERE work_item_id=%s ORDER BY id DESC LIMIT 1", (item_id,)
    ).fetchone()
    legacy = c.execute("SELECT snapshot FROM legacy_migration_records WHERE work_item_id=%s", (item_id,)).fetchone()
    from .feishu import value

    return serial(
        {
            "compact": compact,
            "item": public_item(item, include_sources=True),
            "legacy_brief": value(legacy["snapshot"].get("制图需求说明")) if legacy else "",
            "document": doc,
            "editing": editing_status(doc, tasks[0] if tasks else None),
            "versions": versions,
            "tasks": tasks,
            "deliveries": deliveries,
            "publications": publications,
            "publication_target": c.execute(
                "SELECT * FROM publication_targets WHERE id=%s", (doc["publication_target_id"],)
            ).fetchone(),
            "image_receipts": receipts,
            "assets": assets,
            "sources": sources,
            "jobs": jobs,
            "sync": outbox,
            "history": c.execute(
                "SELECT e.id,e.action,e.detail,e.created_at,u.name AS actor_name FROM audit_events e LEFT JOIN users u ON u.id=e.actor_id WHERE e.work_item_id=%s ORDER BY e.id DESC LIMIT 100",
                (item_id,),
            ).fetchall(),
        }
    )


def write_document(c, user, item_id, write, *, allow_pairing=False):
    get_item(c, user, item_id, True)
    doc = get_doc(c, item_id, write.revision)
    active = c.execute(
        "SELECT 1 FROM artwork_tasks WHERE document_id=%s AND state NOT IN ('accepted','withdrawn')", (doc["id"],)
    ).fetchone()
    body = dict(doc["body"])
    old = body.get(write.section)
    value = write.body
    if write.section == "facts" and isinstance(value, dict):
        from .product_facts import normalize_components

        value = normalize_components(value)
        if write.confirm and len(value.get("components") or []) == 1:
            value["components"] = [{**value["components"][0], "confirmed": True}]
    if len(json.dumps(value, ensure_ascii=False)) > 100000:
        fail("单次资料超过100000字符", 422)
    model = {"facts": Facts, "listing": Listing, "brief": ManualBrief}.get(write.section)
    if write.confirm:
        if not model:
            fail("该资料不支持独立确认", 422)
        if write.section == "brief":
            from .manual_brief import normalize_manual_brief

            value = normalize_manual_brief(value)
        try:
            value = model.model_validate(value).model_dump()
        except ValidationError as e:
            if write.section == "facts":
                from .product_facts import facts_validation_message

                fail(facts_validation_message(e), 422)
            if write.section == "brief":
                from .manual_brief import brief_validation_message

                fail(brief_validation_message(e), 422)
            fail(str(e), 422)
        except ValueError as e:
            fail(str(e), 422)
    # Reconfirming the same version is a no-op, including during production.
    # Do not change its UUID: Listing and artwork snapshots refer to it.
    if write.confirm and doc.get(write.section + "_version"):
        try:
            same = model.model_validate(old).model_dump() == value
        except ValueError:
            same = False
        if same:
            return detail(c, user, item_id)
    if active and write.section in ("facts", "brief"):
        fail("制作批次已锁定资料；未开工请先点‘撤回图需并编辑’，已开工请完成当前批次")
    if write.section == "facts" and isinstance(value, dict):
        for row in value.get("components") or []:
            if not isinstance(row, dict):
                fail("组成明细格式不正确", 422)
            ids = row.get("image_asset_ids") or []
            if not isinstance(ids, list) or len(ids) > 5:
                fail("每个组成项最多上传5张图片", 422)
            try:
                asset_ids = [UUID(str(a)) for a in ids]
            except (ValueError, TypeError):
                fail("组成产品图片无效，请重新上传", 422)
            if len(set(asset_ids)) != len(asset_ids):
                fail("组成产品图片不能重复", 422)
            if asset_ids:
                found = c.execute(
                    "SELECT id FROM assets WHERE id=ANY(%s::uuid[]) AND work_item_id=%s "
                    "AND source_url IS NULL AND uploaded_by IS NOT NULL "
                    "AND mime IN ('image/jpeg','image/png','image/webp')",
                    (asset_ids, item_id),
                ).fetchall()
                if len(found) != len(asset_ids):
                    fail("组成产品图片须为当前商品上传的图片", 422)
    if write.section == "sources":
        from .source_pipeline import source_inputs

        if not isinstance(value, list):
            fail("来源须为链接列表", 422)
        # Quotes and paired SKUs can only come from a server-owned collector snapshot.
        if not allow_pairing:
            pair_keys = ("paired_snapshot_id", "paired_at", "supplier_title", "selected_skus")
            cleaned = []
            for source in value:
                if not isinstance(source, dict):
                    fail("来源格式不正确", 422)
                previous = next(
                    (
                        s
                        for s in (old or [])
                        if s.get("id") == source.get("id")
                        and s.get("url") == source.get("url")
                        and s.get("kind") == source.get("kind")
                    ),
                    {},
                )
                cleaned.append(
                    {
                        **{k: v for k, v in source.items() if k not in pair_keys},
                        **{k: previous[k] for k in pair_keys if k in previous},
                    }
                )
            value = cleaned
        source_inputs({**body, "sources": value})
    body[write.section] = value
    facts_version = doc["facts_version"]
    listing_version = doc["listing_version"]
    brief_version = doc["brief_version"]
    if old != value:
        if write.section == "facts":
            facts_version = listing_version = brief_version = None
        elif write.section == "listing":
            listing_version = None
        elif write.section in ("brief", "selling_points"):
            brief_version = None
    if write.confirm:
        version = uuid4()
        c.execute(
            "INSERT INTO content_versions(id,document_id,kind,body,facts_version,created_by) VALUES(%s,%s,%s,%s,%s,%s)",
            (
                version,
                doc["id"],
                write.section,
                Jsonb(value),
                facts_version if write.section != "facts" else None,
                user["id"],
            ),
        )
        if write.section == "facts":
            facts_version = version
        if write.section == "listing":
            listing_version = version
        if write.section == "brief":
            brief_version = version
    c.execute(
        "UPDATE content_documents SET body=%s,revision=revision+1,facts_version=%s,listing_version=%s,brief_version=%s,updated_at=now() WHERE id=%s",
        (Jsonb(body), facts_version, listing_version, brief_version, doc["id"]),
    )
    event(
        c,
        item_id,
        user,
        "content.confirm" if write.confirm else "content.save",
        {"section": write.section, "revision": doc["revision"] + 1},
    )
    project(c, item_id)
    return detail(c, user, item_id)


def remove_supplier(c, user, item_id, revision, data):
    """Detach a supplier and its components in one revision-checked transaction."""
    from .schemas import DocumentWrite

    c.execute("SELECT pg_advisory_xact_lock(58017002)")
    get_item(c, user, item_id, True)
    doc = get_doc(c, item_id, revision)
    sources = doc["body"].get("sources") or []
    source = next((s for s in sources if s.get("id") == data.get("source_id") and s.get("kind") == "supplier"), None)
    if source is None:
        fail("基础商品已移除或不存在，请刷新数据", 409)
    if c.execute(
        "SELECT 1 FROM artwork_tasks WHERE document_id=%s AND state NOT IN ('accepted','withdrawn')", (doc["id"],)
    ).fetchone():
        fail("当前制作批次已锁定，未开工请先撤回图需再移除基础商品")
    facts = dict(doc["body"].get("facts") or {})
    rows = facts.get("components") or []
    remaining = [
        r
        for r in rows
        if not (
            r.get("source_id") == source.get("id")
            if r.get("source_id")
            else bool(source.get("url")) and r.get("url") == source.get("url")
        )
    ]
    removed = len(rows) - len(remaining)
    if removed:
        facts["components"] = remaining
        included = data.get("included", facts.get("included", ""))
        if not isinstance(included, str) or len(included) > 4000:
            fail("实际包含物须不超过4000字符", 422)
        facts["included"] = included
        if not remaining:
            facts.update(product_type="ordinary", specification="", quantity="", unit="")
        result = write_document(c, user, item_id, DocumentWrite(revision=revision, section="facts", body=facts))
        revision = result["document"]["revision"]
    write_document(
        c,
        user,
        item_id,
        DocumentWrite(revision=revision, section="sources", body=[s for s in sources if s.get("id") != source["id"]]),
    )
    # Removing a source changes the basis even for old ordinary records without components.
    c.execute(
        "UPDATE content_documents SET facts_version=NULL,listing_version=NULL,brief_version=NULL WHERE id=%s",
        (doc["id"],),
    )
    event(c, item_id, user, "supplier.remove", {"source_id": source["id"], "removed_components": removed})
    return detail(c, user, item_id)


def reopen_edit(c, user, item_id, revision, data):
    """Reopen current confirmation pointers; immutable versions remain usable."""
    section = data.get("section")
    if section not in ("facts", "listing", "brief"):
        fail("请选择产品资料、文案或图需", 422)
    # Use the same lock as claiming/starting/withdrawing before reading task state.
    c.execute("SELECT pg_advisory_xact_lock(58017002)")
    get_item(c, user, item_id, True)
    doc = get_doc(c, item_id, revision)
    active = c.execute(
        "SELECT * FROM artwork_tasks WHERE document_id=%s AND state NOT IN ('accepted','withdrawn') FOR UPDATE",
        (doc["id"],),
    ).fetchone()
    if section != "listing" and active:
        if active["state"] not in ("pool", "waiting"):
            fail("当前批次已开工，请完成制作与验收后再修改产品资料或图需；文案可单独修改")
        c.execute("UPDATE artwork_tasks SET state='withdrawn',revision=revision+1 WHERE id=%s", (active["id"],))
        event(c, item_id, user, "artwork.withdraw", {"task_id": active["id"], "reason": "发起内容修改"})
    fields = ["facts_version", "listing_version", "brief_version"] if section == "facts" else [section + "_version"]
    latest = active or c.execute(
        "SELECT * FROM artwork_tasks WHERE document_id=%s ORDER BY batch DESC LIMIT 1", (doc["id"],)
    ).fetchone()
    body = dict(doc["body"])
    if latest and latest.get("legacy_takeover") and latest["state"] == "accepted":
        previous = body.get("legacy_edit_request") or {}
        sections = set(previous.get("sections", [])) if previous.get("task_id") == str(latest["id"]) else set()
        sections.update(("facts", "listing", "brief") if section == "facts" else (section,))
        if not doc["facts_version"]:
            sections.add("facts")
        body["legacy_edit_request"] = {"task_id": str(latest["id"]), "sections": sorted(sections)}
    if body == doc["body"] and not any(doc[f] for f in fields) and not (active and section != "listing"):
        return detail(c, user, item_id)
    # Column names are selected only from the fixed list above.
    c.execute(
        "UPDATE content_documents SET "
        + ",".join(f + "=NULL" for f in fields)
        + ",body=%s,revision=revision+1,updated_at=now() WHERE id=%s",
        (Jsonb(body), doc["id"]),
    )
    event(c, item_id, user, "content.reopen", {"section": section, "previous_versions": {f: doc[f] for f in fields}})
    project(c, item_id)
    return detail(c, user, item_id)


def publish_brief(c, user, item_id, revision, data):
    get_item(c, user, item_id, True)
    doc = get_doc(c, item_id, revision)
    if not doc["brief_version"]:
        fail("请先确认图需")
    if c.execute(
        "SELECT 1 FROM artwork_tasks WHERE document_id=%s AND state NOT IN ('accepted','withdrawn')", (doc["id"],)
    ).fetchone():
        fail("已有进行中的制图任务")
    due = None
    if data.get("due_date"):
        try:
            due = date.fromisoformat(data["due_date"])
        except ValueError:
            fail("交期格式无效", 422)
        if due < date.today():
            fail("交期不能早于今天", 422)
    batch = c.execute(
        "SELECT coalesce(max(batch),0)+1 AS n FROM artwork_tasks WHERE document_id=%s", (doc["id"],)
    ).fetchone()["n"]
    c.execute(
        "INSERT INTO artwork_tasks(id,document_id,brief_version,batch,state,urgent,due_date) VALUES(%s,%s,%s,%s,'pool',%s,%s)",
        (uuid4(), doc["id"], doc["brief_version"], batch, bool(data.get("urgent")), due),
    )
    c.execute("UPDATE content_documents SET revision=revision+1 WHERE id=%s", (doc["id"],))
    event(c, item_id, user, "artwork.publish", {"batch": batch})
    project(c, item_id)
    return detail(c, user, item_id)


def task_command(c, user, task_id, action, revision, data):
    # A single allocation lock protects the per-person unstarted limit even across different tasks.
    c.execute("SELECT pg_advisory_xact_lock(58017002)")
    t = c.execute(
        "SELECT t.*,d.work_item_id FROM artwork_tasks t JOIN content_documents d ON d.id=t.document_id WHERE t.id=%s FOR UPDATE OF t",
        (task_id,),
    ).fetchone()
    if not t:
        fail("任务不存在", 404)
    item = get_item(c, user, t["work_item_id"])
    if t["revision"] != revision:
        fail("任务已更新，请刷新")
    owned = str(t["assignee_id"]) == str(user["id"])
    if action in ("review", "save-review", "cancel-review") and not set(user["roles"]) & {"admin", "operator", "supervisor"}:
        fail("美工可查看验收进度与返修意见，验收操作由内容负责人或管理员处理", 403)
    if action == "claim":
        if not active_artist(user):
            fail("仅已核实在职的内部美工可认领", 403)
        if t["state"] != "pool":
            fail("任务已被认领")
        if (
            c.execute(
                "SELECT count(*) AS n FROM artwork_tasks WHERE assignee_id=%s AND state='waiting'", (user["id"],)
            ).fetchone()["n"]
            >= 2
        ):
            fail("已有2条未开工任务，请先开工或退回公共池")
        c.execute(
            "UPDATE artwork_tasks SET state='waiting',assignee_id=%s,assigned_by=%s,assigned_at=now() WHERE id=%s",
            (user["id"], user["id"], task_id),
        )
    elif action == "start":
        if not owned or not active_artist(user):
            fail("仅在职执行美工可开工", 403)
        if t["state"] != "waiting":
            fail("仅待制图任务可开工")
        c.execute(
            "UPDATE artwork_tasks SET state='working',started_at=coalesce(started_at,now()) WHERE id=%s", (task_id,)
        )
    elif action == "return":
        if not owned or not active_artist(user):
            fail("仅执行美工可退回", 403)
        if t["state"] != "waiting" or not str(data.get("reason", "")).strip():
            fail("仅未开工任务可退回，并需填写原因")
        c.execute(
            "UPDATE artwork_tasks SET state='pool',assignee_id=NULL,assigned_by=NULL,assigned_at=NULL WHERE id=%s",
            (task_id,),
        )
    elif action == "withdraw":
        get_item(c, user, t["work_item_id"], True)
        if t["state"] not in ("pool", "waiting"):
            fail("任务已开工，无法撤回")
        c.execute("UPDATE artwork_tasks SET state='withdrawn' WHERE id=%s", (task_id,))
    elif action == "deliver":
        if not owned or not active_artist(user):
            fail("仅在职执行美工可交付", 403)
        if t["state"] not in ("working", "revision"):
            fail("请先开工或从待返修重新交付")
        ids = data.get("asset_ids", [])
        if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids):
            fail("请至少上传1张原图，且图片不可重复", 422)
        try:
            ids = [str(UUID(str(aid))) for aid in ids]
        except (ValueError, TypeError):
            fail("图片编号无效", 422)
        images = c.execute(
            "SELECT * FROM assets WHERE id=ANY(%s::uuid[]) AND work_item_id=%s AND uploaded_by=%s",
            (ids, t["work_item_id"], user["id"]),
        ).fetchall()
        if (
            len(images) != len(ids)
            or len({a["sha256"] for a in images}) != len(ids)
            or any(not a["mime"].startswith("image/") for a in images)
        ):
            fail("存在无权限、重复或无效图片")
        delivery = uuid4()
        version = c.execute(
            "SELECT coalesce(max(version),0)+1 AS n FROM artwork_deliveries WHERE task_id=%s", (task_id,)
        ).fetchone()["n"]
        c.execute(
            "INSERT INTO artwork_deliveries(id,task_id,version,asset_ids,note,delivered_by) VALUES(%s,%s,%s,%s,%s,%s)",
            (delivery, task_id, version, ids, str(data.get("note", ""))[:4000], user["id"]),
        )
        c.execute("UPDATE artwork_tasks SET state='pending',current_delivery_id=%s WHERE id=%s", (delivery, task_id))
    elif action in ("review", "save-review"):
        get_item(c, user, t["work_item_id"], True)
        if owned:
            fail("不能验收本人制作的图片", 403)
        if t["state"] != "pending":
            fail("仅待验收图稿可填写意见或验收")
        delivery = c.execute("SELECT * FROM artwork_deliveries WHERE id=%s", (t["current_delivery_id"],)).fetchone()
        if str(data.get("delivery_id", delivery["id"])) != str(delivery["id"]):
            fail("交付版本已更新，请刷新")
        notes = data.get("image_notes", delivery["image_notes"])
        if not isinstance(notes, dict) or any(
            str(k) not in {str(x) for x in delivery["asset_ids"]} or not isinstance(v, str) or len(v) > 4000
            for k, v in notes.items()
        ):
            fail("逐图意见只能关联当前交付图片，每条最多4000字", 422)
        notes = {str(k): v.strip() for k, v in notes.items() if v.strip()}
        note = data.get("note", delivery["review_note"] or "")
        if not isinstance(note, str) or len(note) > 4000:
            fail("整体意见最多4000字", 422)
        note = note.strip()
        c.execute(
            "UPDATE artwork_deliveries SET image_notes=%s,review_note=%s WHERE id=%s",
            (Jsonb(notes), note, delivery["id"]),
        )
        if action == "review":
            result = data.get("result")
            if result not in ("accepted", "rejected"):
                fail("验收结果无效", 422)
            if result == "rejected" and not note and not notes:
                fail("请填写逐图或整体返修意见", 422)
            c.execute(
                "UPDATE artwork_deliveries SET result=%s,reviewed_by=%s,reviewed_at=now() WHERE id=%s",
                (result, user["id"], delivery["id"]),
            )
            c.execute(
                "UPDATE artwork_tasks SET state=%s,first_review_result=CASE WHEN legacy_takeover THEN first_review_result ELSE coalesce(first_review_result,%s) END WHERE id=%s",
                ("accepted" if result == "accepted" else "revision", result, task_id),
            )
    elif action == "cancel-review":
        get_item(c, user, t["work_item_id"], True)
        if owned:
            fail("不能验收本人制作的图片", 403)
        if t["state"] != "accepted":
            fail("只能取消当前已验收图稿")
        if c.execute(
            "SELECT 1 FROM artwork_tasks WHERE document_id=%s AND batch>%s", (t["document_id"], t["batch"])
        ).fetchone():
            fail("已有后续制作批次，不能取消历史批次验收")
        delivery = c.execute("SELECT * FROM artwork_deliveries WHERE id=%s", (t["current_delivery_id"],)).fetchone()
        if (
            not delivery
            or delivery["result"] != "accepted"
            or str(data.get("delivery_id", delivery["id"])) != str(delivery["id"])
        ):
            fail("验收版本已更新，请刷新")
        canceled = {
            "result": delivery["result"],
            "review_note": delivery["review_note"],
            "image_notes": delivery["image_notes"],
            "reviewed_by": delivery["reviewed_by"],
            "reviewed_at": delivery["reviewed_at"],
            "canceled_by": user["id"],
            "canceled_at": datetime.now(UTC),
        }
        c.execute(
            "UPDATE artwork_deliveries SET result=NULL,reviewed_by=NULL,reviewed_at=NULL,canceled_reviews=canceled_reviews||%s WHERE id=%s",
            (Jsonb([serial(canceled)]), delivery["id"]),
        )
        c.execute(
            "UPDATE image_receipts SET invalidated_at=now() WHERE delivery_id=%s AND invalidated_at IS NULL",
            (delivery["id"],),
        )
        first = c.execute(
            "SELECT result FROM artwork_deliveries WHERE task_id=%s AND result IS NOT NULL ORDER BY reviewed_at,version LIMIT 1",
            (task_id,),
        ).fetchone()
        c.execute(
            "UPDATE artwork_tasks SET state='pending',first_review_result=CASE WHEN legacy_takeover THEN NULL ELSE %s END WHERE id=%s",
            (first["result"] if first else None, task_id),
        )
    else:
        fail("未知操作", 404)
    c.execute("UPDATE artwork_tasks SET revision=revision+1 WHERE id=%s", (task_id,))
    event(c, item["id"], user, "artwork." + action, data)
    if action != "save-review":
        project(c, item["id"])
    return detail(c, user, item["id"])


def claim_batch(c, user, data):
    c.execute("SELECT pg_advisory_xact_lock(58017002)")
    tasks = data.get("tasks", [])
    if (
        not isinstance(tasks, list)
        or not 1 <= len(tasks) <= 2
        or any(not isinstance(t, dict) or not t.get("id") or not isinstance(t.get("revision"), int) for t in tasks)
        or len({t["id"] for t in tasks}) != len(tasks)
    ):
        fail("每次请选择1至2条不同任务", 422)
    if not active_artist(user):
        fail("仅已核实在职的内部美工可认领", 403)
    count = c.execute(
        "SELECT count(*) AS n FROM artwork_tasks WHERE assignee_id=%s AND state='waiting'", (user["id"],)
    ).fetchone()["n"]
    if count + len(tasks) > 2:
        fail("未开工任务最多保留2条，请先开工或退回公共池")
    for t in sorted(tasks, key=lambda t: t["id"]):
        task_command(c, user, t["id"], "claim", t["revision"], {})
    return {"claimed": len(tasks)}


def assign_batch(c, user, data):
    if not has(user, "art_lead"):
        fail("仅美工负责人可分配", 403)
    c.execute("SELECT pg_advisory_xact_lock(58017002)")
    assignee = c.execute("SELECT * FROM users WHERE id=%s", (data.get("assignee_id"),)).fetchone()
    if not assignee or not active_artist(assignee):
        fail("请选择已核实在职的内部美工")
    tasks = data.get("tasks", [])
    if not tasks or len(tasks) > 100 or len({t["id"] for t in tasks}) != len(tasks):
        fail("请选择1至100条不同任务", 422)
    for x in sorted(tasks, key=lambda t: t["id"]):
        t = c.execute(
            "SELECT t.*,d.work_item_id FROM artwork_tasks t JOIN content_documents d ON d.id=t.document_id WHERE t.id=%s FOR UPDATE OF t",
            (x["id"],),
        ).fetchone()
        if not t or t["state"] != "pool" or t["revision"] != x["revision"]:
            fail("所选任务已更新；本批次未分配")
        c.execute(
            "UPDATE artwork_tasks SET state='waiting',assignee_id=%s,assigned_by=%s,assigned_at=now(),revision=revision+1 WHERE id=%s",
            (assignee["id"], user["id"], t["id"]),
        )
        event(c, t["work_item_id"], user, "artwork.assign", {"assignee_id": assignee["id"], "task_id": t["id"]})
        project(c, t["work_item_id"])
    return {"assigned": len(tasks)}


def publish_listing(c, user, item_id, revision, data):
    from .publication import publish_listing as register

    return register(c, user, item_id, revision, data)


def image_receipt(c, user, item_id, revision, data):
    from .publication import image_receipt as register

    return register(c, user, item_id, revision, data)
