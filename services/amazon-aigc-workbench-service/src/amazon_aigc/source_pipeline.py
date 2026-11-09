"""Durable collection dependencies. Page material never becomes confirmed product facts."""

import hashlib
import json
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from .collector import PARSER_VERSION, validate_url
from .domain import event, fail, get_doc, get_item, serial
from .firecrawl_collector import PARSER_VERSION as FIRECRAWL_PARSER_VERSION
from .product_facts import source_fact_hints
from .tmapi_collector import PARSER_VERSION as TMAPI_PARSER_VERSION


def source_inputs(body):
    rows = list(body.get("sources") or [])
    for component in (body.get("facts") or {}).get("components", []):
        if component.get("url"):
            rows.append({"id": "component-" + component["id"], "url": component["url"], "kind": "supplier"})
    merged = {}
    for row in rows:
        if not isinstance(row, dict):
            fail("来源格式不正确", 422)
        url = str(row.get("url", "")).strip()
        if not url:
            continue
        if url not in merged:
            merged[url] = {
                "id": str(row.get("id") or hashlib.sha256(url.encode()).hexdigest()[:24]),
                "url": url,
                "kind": row.get("kind", "supplier"),
                "manual_text": "",
                "selected_skus": row.get("selected_skus", []),
            }
        text = str(row.get("manual_text", "")).strip()
        if len(text) > 12000:
            fail("单条手动补充请控制在12000字以内", 422)
        if text:
            merged[url]["manual_text"] = text
    if len(merged) > 20:
        fail("单个商品最多采集20个不同链接", 422)
    return list(merged.values())


def input_stamp(doc):
    body = doc["body"]
    basis = {
        "facts_version": str(doc["facts_version"]),
        "facts": body.get("facts"),
        "keywords": body.get("keywords", ""),
        "selling_points": body.get("selling_points", ""),
        "editor_requirements": {
            k: v for k, v in (body.get("brief") or {}).items() if k not in ("plans", "image_count")
        },
        "sources": sorted(
            (s["url"], s["kind"], json.dumps(s.get("selected_skus", []), sort_keys=True, ensure_ascii=False))
            for s in source_inputs(body)
        ),
    }
    return hashlib.sha256(json.dumps(basis, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def queue_source(c, item_id, actor_id, source, revision, force=False, collector="native"):
    parser_version = {"firecrawl": FIRECRAWL_PARSER_VERSION, "tmapi": TMAPI_PARSER_VERSION, "native": PARSER_VERSION}[
        collector
    ]
    # The caller holds the document lock, serializing per-item deduplication.
    old = c.execute(
        "SELECT id,state FROM jobs WHERE kind='source' AND work_item_id=%s AND payload->>'url'=%s "
        "AND coalesce(payload->>'collector','native')=%s "
        "AND (state IN ('queued','running') OR (state='succeeded' AND finished_at>now()-interval '24 hours' AND result->>'parser_version'=%s AND %s)) "
        "ORDER BY created_at DESC LIMIT 1",
        (item_id, source["url"], collector, parser_version, not force),
    ).fetchone()
    if old:
        return {"job_id": str(old["id"]), "reused": old["state"] == "succeeded"}
    jid = uuid4()
    error = None
    try:
        validate_url(source["url"], resolve=False)
        if source["kind"] not in ("supplier", "competitor"):
            raise ValueError("来源类型须为货源或竞品")
    except ValueError as e:
        error = str(e)[:200]
    payload = {
        "url": source["url"],
        "kind": source["kind"],
        "source_id": source["id"],
        "revision": revision,
        "collector": collector,
    }
    c.execute(
        "INSERT INTO jobs(id,kind,work_item_id,actor_id,payload,state,error,finished_at) "
        "VALUES(%s,'source',%s,%s,%s,%s,%s,CASE WHEN %s THEN now() ELSE NULL END)",
        (jid, item_id, actor_id, Jsonb(payload), "failed" if error else "queued", error, bool(error)),
    )
    return {"job_id": str(jid), "reused": False}


def attach_sources(c, item_id, actor_id, doc, payload):
    payload["source_inputs"] = source_inputs(doc["body"])
    payload["input_stamp"] = input_stamp(doc)
    payload["source_jobs"] = [
        queue_source(c, item_id, actor_id, s, doc["revision"], collector=payload.get("collector", "native"))
        for s in payload["source_inputs"]
    ]


def source_rows(c, payload):
    links = payload.get("source_jobs", [])
    ids = [UUID(x["job_id"]) for x in links]
    jobs = c.execute("SELECT * FROM jobs WHERE id=ANY(%s)", (ids,)).fetchall() if ids else []
    by_id = {str(j["id"]): j for j in jobs}
    return [
        (source, link, by_id.get(link["job_id"], {}), by_id.get(link["job_id"], {}).get("result") or {})
        for source, link in zip(payload.get("source_inputs", []), links)
    ]


def has_text(body):
    return bool(body.get("title") or body.get("specifications") or body.get("bullets") or body.get("description"))


def collection_report(c, payload):
    report = []
    for source, link, job, body in source_rows(c, payload):
        state = job.get("state", "failed")
        error = job.get("error") or ("采集任务不存在" if not job else "")
        warning = "；".join(filter(None, [body.get("render_error", ""), *source_fact_hints(body)["warnings"]]))
        if source.get("selected_skus"):
            warning = warning.replace("尚未绑定具体供应规格", f"当前已配对{len(source['selected_skus'])}个供应规格")
        if state == "succeeded" and not has_text(body):
            error = "仅取得图片链接，缺少可用于生成的商品文字，请手动补充"
        report.append(
            {
                "source_id": source["id"],
                "url": source["url"],
                "kind": source["kind"],
                "job_id": link["job_id"],
                "state": state,
                "status": body.get("status"),
                "error": error,
                "warning": warning,
                "title": body.get("title", ""),
                "method": body.get("method", ""),
                "collector": job.get("payload", {}).get("collector", "native"),
                "page_ms": body.get("page_ms"),
                "total_ms": body.get("total_ms"),
                "reused": link["reused"],
                "manual": bool(source.get("manual_text")),
                "has_text": has_text(body),
                "variant_count": body.get("variant_count", 0),
                "variant_images_excluded": body.get("variant_images_excluded", 0),
                "image_count": sum(bool(x.get("key")) for x in body.get("downloads", [])),
            }
        )
    return report


def build_materials(c, payload):
    materials = []
    for source, link, job, body in source_rows(c, payload):
        material = {
            "url": source["url"],
            "kind": source["kind"],
            "authority": "untrusted_reference",
            "reference_scope": "supplier_product_reference"
            if source["kind"] == "supplier"
            else "optional_expression_and_composition_reference",
            "collection_job_id": link["job_id"],
            "collection_state": job.get("state", "failed"),
        }
        if job.get("state") == "succeeded":
            material.update(
                {
                    "title": str(body.get("title", ""))[:500],
                    "specifications": [str(x)[:400] for x in body.get("specifications", [])[:30]],
                    "bullets": [str(x)[:1000] for x in body.get("bullets", [])[:10]],
                    "description": str(body.get("description", ""))[:6000],
                    "source_text": str(body.get("source_text", ""))[:6000],
                    "image_references": [
                        {"url": d["url"], "width": d.get("width"), "height": d.get("height")}
                        for d in body.get("downloads", [])[:8]
                        if d.get("key")
                    ],
                    "collected_at": str(job.get("finished_at")),
                    "collector": body.get("collector", job.get("payload", {}).get("collector", "native")),
                    "variant_verified": False,
                    "variant_count": body.get("variant_count", 0),
                    "selected_variants": [
                        {k: v for k, v in sku.items() if k in ("sku_id", "specification", "images")}
                        for sku in body.get("selected_variants", [])
                    ],
                    "warnings": [*body.get("warnings", []), *source_fact_hints(body)["warnings"]],
                }
            )
        if source.get("selected_skus"):
            material["paired_skus"] = [
                {k: v for k, v in sku.items() if k in ("sku_id", "specification", "quantity", "unit", "images")}
                for sku in source["selected_skus"]
            ]
            material["variant_verified"] = True
            material["selected_variants"] = material["paired_skus"]
            material["warnings"] = [w for w in material.get("warnings", []) if "尚未绑定具体供应规格" not in w]
            material["pairing_note"] = "货源配对仅作规格参考，销售组合与数量仍以确认版facts为准"
        if source.get("manual_text"):
            material["manual_text"] = source["manual_text"]
            material["manual_origin"] = "operator_supplement"
        if job.get("state") == "failed":
            material["collection_error"] = job.get("error")
        materials.append(material)
    return materials


def advance_models(c):
    rows = c.execute(
        "SELECT * FROM jobs WHERE kind='model' AND state='waiting_sources' ORDER BY created_at FOR UPDATE SKIP LOCKED"
    ).fetchall()
    for job in rows:
        payload = job["payload"]
        report = collection_report(c, payload)
        if any(r["state"] in ("queued", "running") for r in report):
            continue
        blocked = any(
            r["kind"] == "supplier" and (r["state"] != "succeeded" or not r["has_text"]) and not r["manual"]
            for r in report
        )
        payload["context"]["source_materials"] = build_materials(c, payload)
        state = "waiting_input" if blocked and not payload.get("allow_confirmed_only") else "queued"
        c.execute(
            "UPDATE jobs SET state=%s,payload=%s,result=%s WHERE id=%s",
            (state, Jsonb(payload), Jsonb({"collection": report}), job["id"]),
        )


def resume(c, user, item_id, job_id, command):
    get_item(c, user, item_id, True)
    doc = get_doc(c, item_id, command.revision)
    job = c.execute("SELECT * FROM jobs WHERE id=%s AND work_item_id=%s FOR UPDATE", (job_id, item_id)).fetchone()
    if not job or job["kind"] != "model" or job["state"] != "waiting_input":
        fail("该任务当前不需要补充资料，请刷新结果")
    payload = job["payload"]
    if payload["input_stamp"] != input_stamp(doc):
        fail("产品事实、关键词或来源链接已变化，请重新生成")
    mode = command.data.get("mode")
    if mode not in ("manual", "confirmed_facts", "retry", "retry_images", "text_only"):
        fail("请选择补充资料、仅用确认资料或重试采集", 422)
    current = {s["url"]: s for s in source_inputs(doc["body"])}
    payload["source_inputs"] = [current[s["url"]] for s in payload["source_inputs"]]
    payload["revision"] = doc["revision"]
    report = collection_report(c, payload)
    missing = [
        r
        for r in report
        if r["kind"] == "supplier" and (r["state"] != "succeeded" or not r["has_text"]) and not r["manual"]
    ]
    if mode == "manual" and missing:
        fail("请补充读取失败的货源资料，或选择仅用已确认资料继续")
    if mode == "retry":
        payload["source_jobs"] = [
            queue_source(
                c, item_id, user["id"], s, doc["revision"], force=True, collector=payload.get("collector", "native")
            )
            if r["state"] != "succeeded" or not r["has_text"] or r["status"] == "partial"
            else link
            for s, link, r in zip(payload["source_inputs"], payload["source_jobs"], report)
        ]
    if mode == "text_only":
        payload["vision_text_only"] = True
    elif mode == "retry_images":
        payload["vision_text_only"] = False
    payload["allow_confirmed_only"] = mode == "confirmed_facts" or payload.get("allow_confirmed_only", False)
    c.execute("UPDATE jobs SET state='waiting_sources',payload=%s,error=NULL WHERE id=%s", (Jsonb(payload), job_id))
    event(c, item_id, user, "job.resume", {"job_id": str(job_id), "mode": mode})
    return {"job_id": str(job_id), "state": "waiting_sources"}


def public_job(c, job):
    payload = job.pop("payload", {})
    job.update(
        {
            "task": payload.get("task"),
            "vision_enabled": payload.get("vision_enabled", False),
            "source_id": payload.get("source_id"),
            "source_url": payload.get("url"),
            "source_kind": payload.get("kind"),
            "collector": payload.get("collector", "native"),
        }
    )
    if "source_jobs" in payload:
        job["collection"] = collection_report(c, payload)
    return serial(job)
