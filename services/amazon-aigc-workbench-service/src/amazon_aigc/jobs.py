import asyncio
import json
import threading
import time
from datetime import UTC, datetime
from uuid import uuid4

from psycopg.types.json import Jsonb

from .capacity import claim, daily_usage
from .collector import CollectionError, collect, validate_url
from .domain import event, fail, get_doc, get_item, serial
from .model_gateway import PROMPT_VERSION, GenerationError, generate
from .source_pipeline import advance_models, attach_sources, collection_report, input_stamp, queue_source

NATIVE_COLLECTION_SLOT = threading.BoundedSemaphore(1)


def enqueue(c, user, item_id, request, settings):
    item = get_item(c, user, item_id, True)
    doc = get_doc(c, item_id, request.revision)
    allowed = ("url", "kind", "source_id") if request.kind == "source" else ("provider", "task")
    payload = {k: request.payload[k] for k in allowed if k in request.payload}
    state = "queued"
    payload["revision"] = doc["revision"]
    from .collector_config import status as collector_status

    payload["collector"] = collector_status(c)["provider"]
    if request.kind == "source":
        try:
            validate_url(str(payload.get("url", "")), resolve=False)
        except ValueError as e:
            fail(str(e), 422)
        if payload.get("kind") not in ("supplier", "competitor"):
            fail("请指定货源或竞品来源", 422)
        if not isinstance(payload.get("source_id"), str) or not payload["source_id"]:
            fail("来源ID不能为空", 422)
        source = {"id": payload["source_id"], "url": payload["url"], "kind": payload["kind"]}
        link = queue_source(c, item_id, user["id"], source, doc["revision"], force=True, collector=payload["collector"])
        row = c.execute("SELECT state FROM jobs WHERE id=%s", (link["job_id"],)).fetchone()
        event(c, item_id, user, "job.enqueue", {"job_id": link["job_id"], "kind": "source"})
        return {"job_id": link["job_id"], "state": row["state"]}
    else:
        provider = payload.get("provider")
        task = payload.get("task")
        if provider not in ("openai", "deepseek") or task not in ("listing", "brief", "facts"):
            fail("未知生成类型", 422)
        if task != "facts" and not doc["facts_version"]:
            fail("请先确认产品事实")
        existing = c.execute(
            "SELECT id,state FROM jobs WHERE work_item_id=%s AND kind='model' AND payload->>'task'=%s "
            "AND payload->>'provider'=%s AND payload->>'input_stamp'=%s AND coalesce(payload->>'collector','native')=%s "
            "AND state IN ('waiting_sources','waiting_input','queued','running') ORDER BY created_at DESC LIMIT 1",
            (item_id, task, provider, input_stamp(doc), payload["collector"]),
        ).fetchone()
        if existing:
            return {"job_id": str(existing["id"]), "state": existing["state"]}
        from .provider_config import runtime

        settings = runtime(c, settings, provider)
        if not settings.model_enabled:
            fail("模型服务尚未启用，可先手工编辑", 503)
        c.execute("SELECT pg_advisory_xact_lock(58017005)")
        usage = daily_usage(c)
        if settings.model_total_daily_limit > 0 and usage["total"] >= settings.model_total_daily_limit:
            fail("今日团队共享模型任务额度已用完", 429)
        if settings.model_daily_limit > 0 and usage[provider] >= settings.model_daily_limit:
            fail("今日所选模型任务额度已用完", 429)
        facts = (
            c.execute("SELECT body FROM content_versions WHERE id=%s", (doc["facts_version"],)).fetchone()
            if doc["facts_version"]
            else None
        )
        payload["vision_enabled"] = provider == "openai"
        payload["context"] = {
            "site": item["site"],
            "operator_notes": (facts["body"] if facts else doc["body"].get("facts", {})).get("source_text", ""),
            "editor_requirements": {
                k: v for k, v in (doc["body"].get("brief") or {}).items() if k not in ("plans", "image_count")
            },
            "facts": facts["body"] if facts else doc["body"].get("facts", {}),
            "keywords": doc["body"].get("keywords", ""),
            "selling_points": doc["body"].get("selling_points", ""),
        }
        # Supplier notes are reference material, even after facts are confirmed.
        # Packaging dimensions must not become product dimensions.
        payload["context"]["facts"] = {
            k: v
            for k, v in payload["context"]["facts"].items()
            if k not in ("source_text", "verification_basis", "packaging_dimensions")
        }
        attach_sources(c, item_id, user["id"], doc, payload)
        payload["context"]["references"] = [s["url"] for s in payload["source_inputs"]]
        state = "waiting_sources"
    job_id = uuid4()
    c.execute(
        "INSERT INTO jobs(id,kind,work_item_id,actor_id,payload,state) VALUES(%s,%s,%s,%s,%s,%s)",
        (job_id, request.kind, item_id, user["id"], Jsonb(serial(payload)), state),
    )
    event(c, item_id, user, "job.enqueue", {"job_id": job_id, "kind": request.kind})
    return {"job_id": str(job_id), "state": state}


def run_job(db, settings, kind):
    token = uuid4()
    with db.connection() as c:
        # Expired source jobs can retry safely. Model jobs are not automatically reissued after uncertain completion.
        c.execute(
            "UPDATE jobs SET state='failed',error='任务执行中断，请确认后重试' WHERE state='running' AND lease_until<now() AND kind='model'"
        )
        c.execute(
            "UPDATE jobs SET state='queued' WHERE state='running' AND lease_until<now() AND kind='source' AND attempts<3"
        )
        c.execute(
            "UPDATE jobs SET state='failed',error='采集执行中断，请重试或手动补充',finished_at=now(),lease_until=NULL "
            "WHERE kind='source' AND state='running' AND lease_until<now() AND attempts>=3"
        )
        if kind == "model":
            advance_models(c)
        job = claim(c, settings, kind)
        if not job:
            return False
        if kind == "model" and job["payload"].get("input_stamp"):
            doc = c.execute("SELECT * FROM content_documents WHERE work_item_id=%s", (job["work_item_id"],)).fetchone()
            if input_stamp(doc) != job["payload"]["input_stamp"]:
                c.execute(
                    "UPDATE jobs SET state='failed',error='生成依据已变化，请重新生成',finished_at=now() WHERE id=%s",
                    (job["id"],),
                )
                return True
        c.execute(
            "UPDATE jobs SET state='running',started_at=now(),attempts=attempts+1,lease_token=%s,lease_until=now()+interval '15 minutes' WHERE id=%s",
            (token, job["id"]),
        )
    worker_started = time.monotonic()
    queue_ms = max(0, round((datetime.now(UTC) - job["created_at"]).total_seconds() * 1000))
    print(json.dumps({"job_id": str(job["id"]), "kind": kind, "state": "running"}), flush=True)
    try:
        if kind == "source":
            if job["payload"].get("collector") in ("firecrawl", "tmapi"):
                from .collector_config import reserve
                from .firecrawl_collector import collect as firecrawl_collect
                from .tmapi_collector import collect as tmapi_collect

                with db.connection() as c:
                    api_key = reserve(c, settings, job)
                collect_source = tmapi_collect if job["payload"]["collector"] == "tmapi" else firecrawl_collect
                result = asyncio.run(collect_source(job["payload"]["url"], settings.storage_root, api_key))
            else:
                with NATIVE_COLLECTION_SLOT:
                    result = asyncio.run(collect(job["payload"]["url"], settings.storage_root, settings.render_enabled))
        else:
            from .provider_config import runtime

            with db.connection() as c:
                settings = runtime(c, settings, job["payload"]["provider"])
            context = dict(job["payload"]["context"])
            report = None
            if job["payload"].get("vision_enabled") and not job["payload"].get("vision_text_only"):
                from .vision import candidates, prepare_images

                with db.connection() as c:
                    rows = candidates(c, job)

                def progress(report):
                    with db.connection() as c:
                        c.execute(
                            "UPDATE jobs SET result=coalesce(result,'{}'::jsonb)||%s::jsonb WHERE id=%s AND lease_token=%s",
                            (Jsonb({"vision": report}), job["id"], token),
                        )

                images, report = asyncio.run(prepare_images(settings.storage_root, job["work_item_id"], rows, progress))
                context.update(
                    _vision_images=images,
                    _vision_report=report,
                    vision_manifest=[
                        {k: v for k, v in r.items() if k in ("image_id", "kind", "label", "bindings")}
                        for r in report["images"]
                        if r["state"] == "ready"
                    ],
                )
                progress({**report, "state": "submitted" if images else "text_only", "sent_count": len(images)})
            result = generate(settings, job["payload"]["provider"], job["payload"]["task"], context)
            if report and not context.get("_vision_images"):
                result["vision"] = {**report, "state": "text_only"}
            if job["payload"].get("vision_text_only"):
                result["vision"] = {
                    "state": "text_only",
                    "sent_count": 0,
                    "recognized_count": 0,
                    "images": [],
                    "reason": "运营选择仅使用文字继续",
                }
        if kind == "model":
            result["timings"] = {
                "queue_and_collection_ms": queue_ms,
                "image_preparation_ms": (report or {}).get("preparation_ms", 0),
                "processing_ms": round((time.monotonic() - worker_started) * 1000),
            }
        with db.connection() as c:
            current = c.execute("SELECT * FROM jobs WHERE id=%s FOR UPDATE", (job["id"],)).fetchone()
            if current["lease_token"] != token:
                return True
            if kind == "source":
                # Result remains a candidate, even if its originating document has since changed.
                snapshot_id = uuid4()
                c.execute(
                    "INSERT INTO source_snapshots(id,work_item_id,source_id,url,document_revision,method,status,body,object_key,parser_version) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        snapshot_id,
                        job["work_item_id"],
                        job["payload"]["source_id"],
                        job["payload"]["url"],
                        job["payload"]["revision"],
                        result["method"],
                        result["status"],
                        Jsonb(result),
                        result["snapshot_key"],
                        result["parser_version"],
                    ),
                )
                for d in result["downloads"]:
                    if not d.get("key"):
                        continue
                    aid = uuid4()
                    d["asset_id"] = str(aid)
                    c.execute(
                        "INSERT INTO assets(id,work_item_id,uploaded_by,object_key,name,sha256,size_bytes,mime,width,height,source_url) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            aid,
                            job["work_item_id"],
                            job["actor_id"],
                            d["key"],
                            "来源图片",
                            d["sha256"],
                            d["size"],
                            d["mime"],
                            d["width"],
                            d["height"],
                            d["url"],
                        ),
                    )
                c.execute("UPDATE source_snapshots SET body=%s WHERE id=%s", (Jsonb(result), snapshot_id))
            else:
                c.execute(
                    "INSERT INTO model_calls(id,job_id,provider,model,task,prompt_version,usage,status,request_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        uuid4(),
                        job["id"],
                        result["provider"],
                        result["model"],
                        job["payload"]["task"],
                        PROMPT_VERSION,
                        Jsonb(result["usage"]),
                        "succeeded",
                        result["request_id"],
                    ),
                )
            if kind == "model" and "source_jobs" in job["payload"]:
                result["collection"] = collection_report(c, job["payload"])
                result["allow_confirmed_only"] = bool(job["payload"].get("allow_confirmed_only"))
            result["document_revision"] = job["payload"]["revision"]
            c.execute(
                "UPDATE jobs SET state='succeeded',error=NULL,result=%s,finished_at=now(),lease_until=NULL WHERE id=%s",
                (Jsonb(result), job["id"]),
            )
        print(json.dumps({"job_id": str(job["id"]), "kind": kind, "state": "succeeded"}), flush=True)
    except Exception as e:  # noqa: BLE001 -- Isolate job/resource failures and return a sanitized result.
        with db.connection() as c:
            # Avoid HTTP response bodies, request headers, or credentials in persisted errors.
            message = str(e)[:300] if isinstance(e, (ValueError, RuntimeError)) else type(e).__name__
            diagnostic = e.diagnostic if isinstance(e, (GenerationError, CollectionError)) else None
            current = c.execute("SELECT lease_token FROM jobs WHERE id=%s FOR UPDATE", (job["id"],)).fetchone()
            if current["lease_token"] != token:
                return True
            if kind == "model" and diagnostic:
                c.execute(
                    "INSERT INTO model_calls(id,job_id,provider,model,task,prompt_version,usage,status,request_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        uuid4(),
                        job["id"],
                        diagnostic["provider"],
                        diagnostic["model"],
                        job["payload"]["task"],
                        PROMPT_VERSION,
                        Jsonb(diagnostic["usage"]),
                        diagnostic["status"],
                        diagnostic["request_id"],
                    ),
                )
            if kind == "model" and diagnostic and diagnostic.get("retryable") and job["attempts"] < 2:
                delay = max(diagnostic.get("retry_after_seconds", 15), 15 * (2 ** job["attempts"]))
                delay += int(job["id"]) % 5
                diagnostic["retry_after_seconds"] = delay
                c.execute(
                    "UPDATE jobs SET state='queued',error=%s,result=coalesce(result,'{}'::jsonb)||%s::jsonb,run_after=now()+(%s * interval '1 second'),lease_until=NULL,lease_token=NULL WHERE id=%s AND lease_token=%s",
                    ("模型服务暂时繁忙，稍后自动继续", Jsonb(diagnostic), delay, job["id"], token),
                )
                print(
                    json.dumps({"job_id": str(job["id"]), "state": "queued", "retry_after_seconds": delay}), flush=True
                )
                return True
            c.execute(
                "UPDATE jobs SET state='failed',error=%s,result=coalesce(result,'{}'::jsonb)||coalesce(%s::jsonb,'{}'::jsonb),finished_at=now(),lease_until=NULL WHERE id=%s AND lease_token=%s",
                (message, Jsonb(diagnostic) if diagnostic else None, job["id"], token),
            )
        print(
            json.dumps(
                {"job_id": str(job["id"]), "kind": kind, "state": "failed", "error": message}, ensure_ascii=False
            ),
            flush=True,
        )
    return True
