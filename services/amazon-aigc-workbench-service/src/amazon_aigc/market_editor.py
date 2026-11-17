"""European content plans keep the review source and ordinary document intact."""

import json
from datetime import date
from uuid import uuid4

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from .domain import detail, event, fail, get_doc, get_item, project
from .manual_brief import ManualBrief, normalize_manual_brief
from .schemas import Listing

SOURCES = ("DE", "FR", "ES")
TARGETS = ("DE", "FR", "ES", "IT", "NL")
LANGUAGES = {"DE": "德语", "FR": "法语", "ES": "西班牙语（西班牙）", "IT": "意大利语", "NL": "荷兰语"}


def targets(value, *, empty=False):
    if not isinstance(value, list) or len(value) > 5 or (not value and not empty):
        fail("请选择有效的欧洲生成站点", 422)
    if any(not isinstance(s, str) or s not in TARGETS for s in value) or len(set(value)) != len(value):
        fail("生成目标仅支持 DE、FR、ES、IT、NL，且不能重复", 422)
    return [s for s in TARGETS if s in value]


def _touch(c, doc):
    body = {**doc["body"], "_market_revision": int(doc["body"].get("_market_revision", 0)) + 1}
    c.execute("UPDATE content_documents SET body=%s,revision=revision+1,updated_at=now() WHERE id=%s", (Jsonb(body), doc["id"]))


def _plan_item(c, user, item_id, revision):
    item = get_item(c, user, item_id, True)
    doc = get_doc(c, item_id, revision)
    if item["site"] not in SOURCES or item["editor_mode"] != "pan_eu":
        fail("当前商品未启用欧洲多站计划", 422)
    return item, doc


def set_mode(c, user, item_id, revision, data):
    item = get_item(c, user, item_id, True)
    doc = get_doc(c, item_id, revision)
    mode = data.get("mode")
    if mode not in ("single", "pan_eu") or mode == "pan_eu" and item["site"] not in SOURCES:
        fail("仅德国、法国、西班牙选品可选择欧洲多站计划", 422)
    selected = targets(data.get("targets", item["editor_targets"] or list(TARGETS)), empty=True)
    if mode == item["editor_mode"] and selected == item["editor_targets"]:
        return detail(c, user, item_id)
    if mode == "pan_eu":
        old = doc["body"].get("brief") or {}
        common = {**old, "plans": [{**p, "text": ""} for p in old.get("plans") or []]}
        c.execute("INSERT INTO market_plans(document_id,shared_brief) VALUES(%s,%s) ON CONFLICT DO NOTHING", (doc["id"], Jsonb(common)))
        for site in TARGETS:
            listing = doc["body"].get("listing") or {} if site == item["site"] else {}
            texts = [p.get("text", "") for p in old.get("plans") or []] if site == item["site"] else []
            c.execute(
                "INSERT INTO market_contents(document_id,site,listing,image_texts,listing_version) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (doc["id"], site, Jsonb(listing), Jsonb(texts), doc["listing_version"] if site == item["site"] else None),
            )
    c.execute("UPDATE work_items SET editor_mode=%s,editor_targets=%s,updated_at=now() WHERE id=%s", (mode, selected, item_id))
    _touch(c, doc)
    event(c, item_id, user, "market.mode", {"mode": mode, "targets": selected})
    return detail(c, user, item_id)


def plan_detail(c, doc, item):
    plan = c.execute("SELECT * FROM market_plans WHERE document_id=%s", (doc["id"],)).fetchone()
    if not plan:
        return None
    contents = c.execute("SELECT * FROM market_contents WHERE document_id=%s ORDER BY site", (doc["id"],)).fetchall()
    refs = [r["listing_version"] for r in contents if r["listing_version"]] + ([plan["shared_version"]] if plan["shared_version"] else [])
    versions = c.execute("SELECT * FROM content_versions WHERE id=ANY(%s::uuid[])", (refs,)).fetchall() if refs else []
    queued = c.execute("SELECT id,brief_version,sites,due_date,urgent,created_at FROM market_submissions WHERE document_id=%s AND state='queued' ORDER BY created_at", (doc["id"],)).fetchall()
    submitted = bool(c.execute("SELECT 1 FROM market_submissions s LEFT JOIN artwork_tasks t ON t.id=s.task_id WHERE s.document_id=%s AND s.brief_version=%s AND (s.state='queued' OR t.state<>'withdrawn')", (doc["id"], plan["shared_version"])).fetchone())
    return {**plan, "submitted": submitted, "targets": item["editor_targets"], "contents": contents, "versions": versions, "queued": queued}


def editing(doc, item, plan):
    if item["editor_mode"] != "pan_eu":
        return None
    selected = item["editor_targets"]
    versions = {v["id"]: v for v in (plan or {}).get("versions", [])}
    rows = {r["site"]: r for r in (plan or {}).get("contents", [])}
    listing_pending = not selected or any(not rows.get(s) or not versions.get(rows[s]["listing_version"]) or (versions[rows[s]["listing_version"]]["facts_version"] != doc["facts_version"] or versions[rows[s]["listing_version"]]["body"] != rows[s]["listing"]) for s in selected)
    shared = versions.get((plan or {}).get("shared_version"))
    body = shared["body"] if shared else {}
    common = {k: v for k, v in body.items() if k not in ("market_texts", "market_sites", "shared_design")}
    brief_pending = not (plan or {}).get("submitted") or not selected or not shared or shared["facts_version"] != doc["facts_version"] or common != plan["shared_brief"] or any(s not in body.get("market_sites", []) or rows[s]["image_texts"] != body.get("market_texts", {}).get(s) for s in selected)
    return {"facts": not bool(doc["facts_version"]), "listing": listing_pending, "brief": bool(brief_pending)}


def save(c, user, item_id, revision, data, *, returning=True):
    _, doc = _plan_item(c, user, item_id, revision)
    selected = targets(data.get("targets", _["editor_targets"]), empty=True)
    if len(json.dumps(data, ensure_ascii=False)) > 300000:
        fail("本批内容过大，请减少单次编辑范围", 422)
    if "generation_targets" in data:
        scopes = data["generation_targets"]
        if not isinstance(scopes, dict) or set(scopes) - {"listing", "brief"}:
            fail("生成站点选择格式无效", 422)
        scopes = {kind: targets(value, empty=True) for kind, value in scopes.items()}
        if any(set(value) - set(selected) for value in scopes.values()):
            fail("生成站点必须属于当前多站计划", 422)
        c.execute("UPDATE market_plans SET generation_targets=%s WHERE document_id=%s", (Jsonb(scopes), doc["id"]))
    listings = data.get("listings", {})
    texts = data.get("image_texts", {})
    if not isinstance(listings, dict) or not isinstance(texts, dict) or set(listings) - set(TARGETS) or set(texts) - set(TARGETS):
        fail("站点内容格式无效", 422)
    for site, value in listings.items():
        if not isinstance(value, dict):
            fail("文案格式无效", 422)
        c.execute("UPDATE market_contents SET listing=%s,listing_review=%s WHERE document_id=%s AND site=%s", (Jsonb(value), Jsonb((data.get("listing_reviews") or {}).get(site) or {}), doc["id"], site))
    for site, value in texts.items():
        if not isinstance(value, list) or len(value) > 12 or any(not isinstance(t, str) or len(t) > 2000 for t in value):
            fail("图中文字须为最多12项、每项最多2000字的列表", 422)
        c.execute("UPDATE market_contents SET image_texts=%s WHERE document_id=%s AND site=%s", (Jsonb(value), doc["id"], site))
    if "shared_brief" in data:
        brief = data["shared_brief"]
        if not isinstance(brief, dict) or set(brief) - {"product", "instructions", "selling_points", "visual_focus", "requirements", "references", "plans", "image_count"}:
            fail("共用图需格式无效", 422)
        c.execute("UPDATE market_plans SET shared_brief=%s,updated_at=now() WHERE document_id=%s", (Jsonb(brief), doc["id"]))
    c.execute("UPDATE work_items SET editor_targets=%s WHERE id=%s", (selected, item_id))
    _touch(c, doc)
    event(c, item_id, user, "market.save", {"targets": selected})
    return detail(c, user, item_id) if returning else doc


def _version(c, doc, user, kind, body):
    ident = uuid4()
    c.execute("INSERT INTO content_versions(id,document_id,kind,body,facts_version,created_by) VALUES(%s,%s,%s,%s,%s,%s)", (ident, doc["id"], kind, Jsonb(body), doc["facts_version"], user["id"]))
    return ident


def confirm(c, user, item_id, revision, data):
    item, doc = _plan_item(c, user, item_id, revision)
    selected = targets(data.get("targets"))
    if set(selected) - set(item["editor_targets"]):
        fail("确认范围已变化，请先保存生成站点")
    if not doc["facts_version"]:
        fail("请先人工确认本品资料")
    kind = data.get("kind")
    if kind not in ("listing", "brief"):
        fail("请选择确认文案或共用图需", 422)
    if data.get("acknowledged") is not True:
        fail("请先统一核对本批内容", 422)
    if not isinstance(data.get("listings", {}), dict) or not isinstance(data.get("image_texts", {}), dict):
        fail("本批站点内容格式无效", 422)
    plan = c.execute("SELECT * FROM market_plans WHERE document_id=%s FOR UPDATE", (doc["id"],)).fetchone()
    rows = {r["site"]: r for r in c.execute("SELECT * FROM market_contents WHERE document_id=%s", (doc["id"],)).fetchall()}
    try:
        if kind == "listing":
            incoming = data.get("listings", {})
            values = {site: Listing.model_validate(incoming.get(site, rows[site]["listing"])).model_dump(exclude={"translations"}) for site in selected}
        else:
            common = ManualBrief.model_validate(normalize_manual_brief(data.get("shared_brief", plan["shared_brief"]))).model_dump()
            if common.get("plans") and common["plans"][0].get("text", "").strip():
                fail("共用白底主图应保持无字", 422)
            count = len(common.get("plans") or []) or common["image_count"]
            incoming = data.get("image_texts", {})
            values = {site: incoming.get(site, rows[site]["image_texts"]) for site in selected}
            for site, text in values.items():
                if not isinstance(text, list) or len(text) != count or any(not isinstance(t, str) or len(t) > 2000 for t in text):
                    fail(site + " 图中文字数量须与共用套图一致", 422)
                if text and text[0].strip():
                    fail("白底主图应保持无字", 422)
                for i, p in enumerate(common.get("plans") or []):
                    if i and bool(p.get("text", "").strip()) != bool(text[i].strip()):
                        fail(site + " 第" + str(i + 1) + "张图中文字待补全", 422)
    except (ValidationError, ValueError, KeyError) as exc:
        fail("本批内容尚未完整，请核对字段、长度及站点语言：" + str(exc)[:400], 422)
    if kind == "listing":
        changed = False
        for site, body in values.items():
            old = c.execute("SELECT body,facts_version FROM content_versions WHERE id=%s", (rows[site]["listing_version"],)).fetchone() if rows[site]["listing_version"] else None
            if old and old["body"] == body and old["facts_version"] == doc["facts_version"]:
                continue
            version = _version(c, doc, user, "listing", body)
            c.execute("UPDATE market_contents SET listing=%s,listing_version=%s,listing_review=%s WHERE document_id=%s AND site=%s", (Jsonb(body), version, Jsonb((data.get("listing_reviews") or {}).get(site) or {}), doc["id"], site))
            changed = True
        if changed:
            _touch(c, doc)
            event(c, item_id, user, "market.confirm", {"kind": kind, "sites": selected})
        return detail(c, user, item_id)
    snapshot = {**common, "market_texts": values, "market_sites": selected, "shared_design": True}
    previous = c.execute("SELECT body,facts_version FROM content_versions WHERE id=%s", (plan["shared_version"],)).fetchone() if plan["shared_version"] else None
    if previous and previous["body"] == snapshot and previous["facts_version"] == doc["facts_version"]:
        published = c.execute("SELECT 1 FROM market_submissions s JOIN artwork_tasks t ON t.id=s.task_id WHERE s.brief_version=%s AND t.state='withdrawn'", (plan["shared_version"],)).fetchone()
        if published:
            c.execute("UPDATE market_submissions SET state='queued',task_id=NULL WHERE brief_version=%s", (plan["shared_version"],))
            activate_pending(c, doc["id"])
            project(c, item_id)
        return detail(c, user, item_id)
    version = _version(c, doc, user, "brief", snapshot)
    c.execute("UPDATE market_plans SET shared_brief=%s,shared_version=%s,updated_at=now() WHERE document_id=%s", (Jsonb(common), version, doc["id"]))
    for site, text in values.items():
        c.execute("UPDATE market_contents SET image_texts=%s WHERE document_id=%s AND site=%s", (Jsonb(text), doc["id"], site))
    due = None
    if data.get("due_date"):
        try:
            due = date.fromisoformat(data["due_date"])
        except (ValueError, TypeError):
            fail("交期格式无效", 422)
        if due < date.today():
            fail("交期不能早于今天", 422)
    c.execute("INSERT INTO market_submissions(id,document_id,brief_version,sites,state,created_by,urgent,due_date) VALUES(%s,%s,%s,%s,'queued',%s,%s,%s)", (uuid4(), doc["id"], version, selected, user["id"], bool(data.get("urgent")), due))
    _touch(c, doc)
    event(c, item_id, user, "market.confirm", {"kind": kind, "sites": selected, "version": version})
    activate_pending(c, doc["id"])
    project(c, item_id)
    return detail(c, user, item_id)


def activate_pending(c, document_id):
    c.execute("SELECT id FROM content_documents WHERE id=%s FOR UPDATE", (document_id,))
    if c.execute("SELECT 1 FROM artwork_tasks WHERE document_id=%s AND state NOT IN ('accepted','withdrawn')", (document_id,)).fetchone():
        return
    queued = c.execute("SELECT * FROM market_submissions WHERE document_id=%s AND state='queued' ORDER BY created_at,id LIMIT 1 FOR UPDATE", (document_id,)).fetchone()
    if not queued:
        return
    task = uuid4()
    batch = c.execute("SELECT coalesce(max(batch),0)+1 AS n FROM artwork_tasks WHERE document_id=%s", (document_id,)).fetchone()["n"]
    c.execute("INSERT INTO artwork_tasks(id,document_id,brief_version,batch,state,urgent,due_date,market_sites) VALUES(%s,%s,%s,%s,'pool',%s,%s,%s)", (task, document_id, queued["brief_version"], batch, queued["urgent"], queued["due_date"], queued["sites"]))
    for site in queued["sites"]:
        c.execute("INSERT INTO artwork_task_markets(task_id,site) VALUES(%s,%s)", (task, site))
    c.execute("UPDATE market_submissions SET state='published',task_id=%s WHERE id=%s", (task, queued["id"]))
    item = c.execute("SELECT work_item_id FROM content_documents WHERE id=%s", (document_id,)).fetchone()["work_item_id"]
    actor = c.execute("SELECT * FROM users WHERE id=%s", (queued["created_by"],)).fetchone()
    event(c, item, actor, "artwork.publish", {"batch": batch, "task_id": task, "sites": queued["sites"]})


def generation_context(c, item, doc, requested):
    if item["editor_mode"] != "pan_eu" or item["site"] not in SOURCES:
        fail("当前商品不支持多站生成", 422)
    selected = targets(requested)
    if set(selected) - set(item["editor_targets"]):
        fail("生成范围已变化，请重新选择站点")
    plan = c.execute("SELECT shared_brief FROM market_plans WHERE document_id=%s", (doc["id"],)).fetchone()
    return {"editor_market_targets": selected, "shared_brief_reference": plan["shared_brief"] if plan else {}}
