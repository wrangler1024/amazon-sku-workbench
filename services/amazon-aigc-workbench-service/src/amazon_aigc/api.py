import hashlib
import io
import json
import secrets
import zipfile
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Annotated, Literal
from urllib.parse import urlencode
from uuid import UUID, uuid4

import httpx
from fastapi import BackgroundTasks, FastAPI, File, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from psycopg import IntegrityError

from . import __version__
from .auth import COOKIE, authenticate, new_session
from .config import settings as load_settings
from .db import Database
from .domain import (
    active_artist,
    assign_batch,
    claim_batch,
    detail,
    event,
    fail,
    get_item,
    idempotent,
    image_receipt,
    publish_brief,
    publish_listing,
    remove_supplier,
    reopen_edit,
    serial,
    task_command,
    write_document,
)
from .jobs import enqueue
from .publication import withdraw_registration
from .schemas import Command, DocumentWrite, JobRequest


def create_app(settings=None, db=None):
    s = settings or load_settings()
    db = db or Database(s.database_url)

    @asynccontextmanager
    async def lifespan(app):
        db.migrate()
        s.storage_root.mkdir(parents=True, exist_ok=True)
        from .base_login import BaseLoginGateway
        login_gateway = BaseLoginGateway(s) if s.base_login_enabled else None
        if login_gateway:
            app.state.base_login_gateway = login_gateway
        try:
            yield
        finally:
            if login_gateway:
                login_gateway.close()
            db.close()

    app = FastAPI(title="amazon-aigc-workbench-service", version=__version__, lifespan=lifespan)
    app.state.settings = s
    app.state.db = db
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[s.base_parent_origin],
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With", "X-Workbench-Context", "X-Workbench-Preview", "Idempotency-Key", "If-None-Match"],
        expose_headers=["Content-Disposition", "ETag"],
        allow_credentials=False,
        max_age=600,
    )

    app.add_middleware(GZipMiddleware, minimum_size=1000)

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith(("/api/", "/auth/")) or request.url.path in ("/", "/index.html"):
            response.headers.setdefault("Cache-Control", "no-store")
        if request.url.path.startswith("/assets/"):
            response.headers.setdefault("Cache-Control", "public,max-age=31536000,immutable")
        return response

    @app.exception_handler(IntegrityError)
    async def integrity(request, exc):
        return Response(json.dumps({"detail": "记录冲突，请刷新后重试"}), 409, media_type="application/json")

    @app.get("/health")
    def health():
        with db.connection() as c:
            c.execute("SELECT 1")
        return {
            "service": "amazon-aigc-workbench-service",
            "version": __version__,
            "environment": s.environment,
            "database": "postgresql",
            "status": "ok",
        }

    @app.get("/api/me")
    def me(request: Request):
        with db.connection() as c:
            user = authenticate(c, request)
            from .provider_config import status

            configured = {p["provider"]: p["enabled"] and p["configured"] for p in status(c)}
            return {
                "user": serial(user),
                "environment": s.environment,
                "base_url": s.feishu_base_origin + "/base/" + s.base_id,
                "providers": {
                    "openai": configured["openai"] or bool(s.model_enabled and s.openai_api_key and s.openai_model),
                    "deepseek": configured["deepseek"]
                    or bool(s.model_enabled and s.deepseek_api_key and s.deepseek_model),
                },
            }

    @app.post("/auth/base/challenge")
    def base_challenge(request: Request):
        from .base_login import check_request, issue

        check_request(request, s)
        return {**issue(db), "parent_origin": s.base_parent_origin}

    @app.post("/auth/base/exchange")
    def base_exchange(payload: dict, request: Request, background: BackgroundTasks):
        from .base_login import EMBED_COOKIE, check_request, exchange

        check_request(request, s)
        token = exchange(db, s, payload, getattr(app.state, "base_login_gateway", None), background.add_task)
        if request.headers.get("origin") == s.base_parent_origin:
            return {"ok": True, "access_token": token}
        response = Response('{"ok":true}', media_type="application/json")
        response.headers.append(
            "Set-Cookie",
            f"{EMBED_COOKIE}={token}; Path=/; Max-Age={s.session_hours * 3600}; Secure; HttpOnly; SameSite=None; Partitioned",
        )
        return response

    @app.get("/auth/login")
    def login():
        if not s.feishu_app_id or not s.feishu_app_secret:
            fail("飞书登录应用尚未配置；请联系工作台管理员", 503)
        state = secrets.token_urlsafe(32)
        url = "https://accounts.feishu.cn/open-apis/authen/v1/authorize?" + urlencode(
            {"client_id": s.feishu_app_id, "redirect_uri": s.public_url + "/auth/callback", "state": state}
        )
        response = RedirectResponse(url)
        response.set_cookie(
            "aigc_oauth_state",
            state,
            max_age=600,
            httponly=True,
            secure=s.public_url.startswith("https"),
            samesite="lax",
        )
        return response

    @app.get("/auth/callback")
    def callback(request: Request, code: str = "", state: str = ""):
        expected = request.cookies.get("aigc_oauth_state", "")
        if not code or not expected or not secrets.compare_digest(expected, state):
            fail("登录验证已过期", 400)
        r = httpx.post(
            "https://open.feishu.cn/open-apis/authen/v2/oauth/token",
            json={
                "grant_type": "authorization_code",
                "client_id": s.feishu_app_id,
                "client_secret": s.feishu_app_secret,
                "code": code,
                "redirect_uri": s.public_url + "/auth/callback",
            },
            timeout=20,
        )
        data = r.json()
        if r.status_code != 200 or not data.get("access_token"):
            fail("飞书登录交换失败", 401)
        u = httpx.get(
            "https://open.feishu.cn/open-apis/authen/v1/user_info",
            headers={"Authorization": "Bearer " + data["access_token"]},
            timeout=20,
        ).json()
        if u.get("code") != 0:
            fail("无法确认飞书登录身份", 401)
        with db.connection() as c:
            user = c.execute(
                "SELECT u.* FROM users u JOIN external_identities e ON e.user_id=u.id WHERE e.provider='feishu_union' AND e.tenant=%s AND e.subject=%s AND u.active=true",
                (s.tenant, u["data"]["union_id"]),
            ).fetchone()
            from .roster_sync import fresh

            if not user or not fresh(user) or "admin" not in user["roles"]:
                fail("独立管理后台仅供管理员使用，请从飞书数据表打开编辑工作台", 403)
            token = new_session(c, user["id"], s.session_hours)
        response = RedirectResponse("/")
        response.set_cookie(
            COOKIE,
            token,
            max_age=s.session_hours * 3600,
            httponly=True,
            secure=s.public_url.startswith("https"),
            samesite="none" if s.public_url.startswith("https") else "lax",
        )
        response.delete_cookie("aigc_oauth_state")
        return response

    @app.post("/auth/logout")
    def logout(request: Request):
        with db.connection() as c:
            authenticate(c, request)
            token = request.cookies.get("__Host-aigc_base_session") or request.cookies.get(COOKIE, "")
            if not token and request.headers.get("authorization", "").startswith("Bearer "):
                token = request.headers["authorization"][7:]
            c.execute("DELETE FROM sessions WHERE token_hash=%s", (hashlib.sha256(token.encode()).hexdigest(),))
        response = Response(status_code=204)
        response.delete_cookie(COOKIE)
        response.headers.append(
            "Set-Cookie", "__Host-aigc_base_session=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=None; Partitioned"
        )
        return response

    @app.get("/api/items")
    def items(request: Request):
        with db.connection() as c:
            user = authenticate(c, request)
            from .workbench_views import item_rows

            return item_rows(c, user)

    people_cache = {"at": 0, "value": None}
    people_lock = Lock()

    @app.get("/api/people")
    def people(request: Request, refresh: bool = False):
        from .workbench_views import artist_rows
        with db.connection() as c:
            authenticate(c, request)
            with people_lock:
                if refresh or people_cache["value"] is None or monotonic()-people_cache["at"]>30:
                    people_cache.update(at=monotonic(),value=artist_rows(c))
                return people_cache["value"]

    @app.get("/api/workbench/summary")
    def summary(request: Request):
        from .workbench_query import workbench_summary
        with db.connection() as c:
            return workbench_summary(c, authenticate(c,request))

    @app.get("/api/workbench/items")
    def page_items(request: Request, tab: str='facts', scope: str='pending', q: str='', days: int=Query(7,ge=0,le=36500),
                   date_from: date | None=None, date_to: date | None=None, page: int=Query(1,ge=1,le=1000000),
                   state: str='', task_range: str='assigned', site: str='', owner: str='', group: str='', person: str='', urgent: bool=False):
        from .workbench_query import workbench_page
        with db.connection() as c:
            user=authenticate(c,request)
            result=workbench_page(c,user,tab=tab,scope=scope,q=q[:300],days=days,date_from=date_from,date_to=date_to,
                page=page,state=state,task_range=task_range,site=site,owner=owner,group=group,person=person,urgent=urgent)
            body=json.dumps(result,ensure_ascii=False,separators=(',',':')).encode()
            tag='"'+hashlib.sha256(body).hexdigest()+'"'
            headers={"ETag":tag,"Cache-Control":"private,no-cache","Vary":"Authorization, Cookie, X-Workbench-Preview"}
            if request.headers.get('if-none-match')==tag:return Response(status_code=304,headers=headers)
            return Response(body,media_type='application/json',headers=headers)

    @app.get("/api/workbench/thumbnails")
    def thumbnails(request: Request, item_id: Annotated[list[UUID], Query(min_length=1, max_length=50)]):
        from .product_images import thumbnail_rows
        with db.connection() as c:
            result = thumbnail_rows(c, authenticate(c, request), item_id)
            return Response(json.dumps(result).encode(), media_type="application/json", headers={"Cache-Control": "private,no-store"})

    @app.get("/api/items/{item_id}")
    def item(item_id: UUID, request: Request, compact: bool=False):
        with db.connection() as c:
            return detail(c, authenticate(c, request), item_id,compact=compact)

    @app.get("/api/items/{item_id}/jobs/status")
    def job_status(item_id: UUID, request: Request):
        with db.connection() as c:
            get_item(c,authenticate(c,request),item_id)
            return serial(c.execute("SELECT id,state,finished_at,md5(coalesce(result::text,'')||coalesce(error,'')) AS result_version FROM jobs WHERE work_item_id=%s ORDER BY created_at DESC LIMIT 100",(item_id,)).fetchall())

    @app.put("/api/items/{item_id}/document")
    def save(item_id: UUID, write: DocumentWrite, request: Request):
        with db.connection() as c:
            user = authenticate(c, request)
            return idempotent(
                c,
                user,
                request.headers.get("Idempotency-Key"),
                {"item": str(item_id), "write": write.model_dump()},
                lambda: write_document(c, user, item_id, write),
            )

    @app.post("/api/items/{item_id}/supplier-pair")
    def supplier_pair(item_id: UUID, command: Command, request: Request):
        from .supplier_pairing import pair

        with db.connection() as c:
            user = authenticate(c, request)
            return idempotent(
                c,
                user,
                request.headers.get("Idempotency-Key"),
                {"item": str(item_id), "action": "supplier-pair", **command.model_dump()},
                lambda: pair(c, user, item_id, command.revision, command.data),
            )

    @app.post("/api/items/{item_id}/{action}")
    def item_action(item_id: UUID, action: str, command: Command, request: Request):
        funcs = {
            "publish-brief": publish_brief,
            "publication": publish_listing,
            "image-receipt": image_receipt,
            "withdraw-publication": withdraw_registration,
            "reopen-edit": reopen_edit,
            "remove-supplier": remove_supplier,
        }
        if action not in funcs:
            fail("未知操作", 404)
        with db.connection() as c:
            user = authenticate(c, request)
            return idempotent(
                c,
                user,
                request.headers.get("Idempotency-Key"),
                {"item": str(item_id), "action": action, **command.model_dump()},
                lambda: funcs[action](c, user, item_id, command.revision, command.data),
            )

    @app.post("/api/artwork/assign")
    def assign(command: dict, request: Request):
        with db.connection() as c:
            user = authenticate(c, request)
            return idempotent(
                c,
                user,
                request.headers.get("Idempotency-Key"),
                {"action": "assign", **command},
                lambda: assign_batch(c, user, command),
            )

    @app.post("/api/artwork/claim-batch")
    def claim_many(command: dict, request: Request):
        with db.connection() as c:
            user = authenticate(c, request)
            return idempotent(
                c,
                user,
                request.headers.get("Idempotency-Key"),
                {"action": "claim-batch", **command},
                lambda: claim_batch(c, user, command),
            )

    @app.post("/api/artwork/{task_id}/{action}")
    def artwork(task_id: UUID, action: str, command: Command, request: Request):
        with db.connection() as c:
            user = authenticate(c, request)
            return idempotent(
                c,
                user,
                request.headers.get("Idempotency-Key"),
                {"task": str(task_id), "action": action, **command.model_dump()},
                lambda: task_command(c, user, task_id, action, command.revision, command.data),
            )

    @app.post("/api/items/{item_id}/jobs/create")
    def jobs(item_id: UUID, job: JobRequest, request: Request):
        with db.connection() as c:
            user = authenticate(c, request)
            return idempotent(
                c,
                user,
                request.headers.get("Idempotency-Key"),
                {"item": str(item_id), **job.model_dump()},
                lambda: enqueue(c, user, item_id, job, s),
            )

    @app.post("/api/items/{item_id}/jobs/{job_id}/resume")
    def resume_job(item_id: UUID, job_id: UUID, command: Command, request: Request):
        from .source_pipeline import resume

        with db.connection() as c:
            user = authenticate(c, request)
            return idempotent(
                c,
                user,
                request.headers.get("Idempotency-Key"),
                {"item": str(item_id), "job": str(job_id), **command.model_dump()},
                lambda: resume(c, user, item_id, job_id, command),
            )

    @app.post("/api/items/{item_id}/assets/upload")
    def upload(item_id: UUID, request: Request, file: Annotated[UploadFile, File()]):
        with db.connection() as c:
            user = authenticate(c, request)
            item = get_item(c, user, item_id)
            own = str(item["owner_id"]) == str(user["id"]) or "admin" in user["roles"]
            task = c.execute(
                "SELECT 1 FROM artwork_tasks t JOIN content_documents d ON d.id=t.document_id WHERE d.work_item_id=%s AND t.assignee_id=%s AND t.state IN ('working','revision')",
                (item_id, user["id"]),
            ).fetchone()
            if not own and not (task and active_artist(user)):
                fail("仅内容负责人或执行美工可上传", 403)
            data = file.file.read(20_000_001)
            if not data or len(data) > 20_000_000:
                fail("图片须小于20MB", 422)
            try:
                with Image.open(io.BytesIO(data)) as im:
                    im.verify()
                with Image.open(io.BytesIO(data)) as im:
                    width, height = im.size
                    mime = Image.MIME.get(im.format, "")
            except Exception:  # noqa: BLE001 -- Fail closed for untrusted external resources.
                fail("无法读取图片", 422)
            if mime not in ("image/jpeg", "image/png", "image/webp"):
                fail("支持JPEG、PNG和WebP原图", 422)
            aid = uuid4()
            key = "uploads/" + str(aid)
            path = s.storage_root / key
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_bytes(data)
            name = Path(file.filename or "image").name[:200]
            c.execute(
                "INSERT INTO assets(id,work_item_id,uploaded_by,object_key,name,sha256,size_bytes,mime,width,height) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (aid, item_id, user["id"], key, name, hashlib.sha256(data).hexdigest(), len(data), mime, width, height),
            )
            event(c, item_id, user, "asset.upload", {"asset_id": aid, "size": len(data)})
            return {"id": str(aid), "name": name, "width": width, "height": height}

    @app.get("/api/assets/{asset_id}")
    def asset(asset_id: UUID, request: Request, download: bool = False, size: Literal["thumbnail", "preview"] | None = None):
        with db.connection() as c:
            user = authenticate(c, request)
            a = c.execute("SELECT * FROM assets WHERE id=%s", (asset_id,)).fetchone()
            if not a:
                fail("图片不存在", 404)
            get_item(c, user, a["work_item_id"])
            if a.get("source_url", "") and a["source_url"].startswith("feishu-legacy:"):
                from .legacy import ensure_asset
                a = ensure_asset(c, s, a)
        if size and not download:
            from .media_previews import asset_preview
            try:
                return display_image(asset_preview(s.storage_root, a, size), request)
            except (ValueError, OSError):
                fail("图片预览生成失败，请尝试查看原图", 422)
        return FileResponse(
            s.storage_root / a["object_key"], media_type=a["mime"], filename=a["name"] if download else None
        )

    def display_image(path, request):
        from .media_previews import preview_etag
        headers = {"Cache-Control": "private, max-age=300", "Vary": "Authorization, Cookie", "ETag": preview_etag(path)}
        if request.headers.get("if-none-match") == headers["ETag"]:
            return Response(status_code=304, headers=headers)
        return FileResponse(path, media_type="image/webp", headers=headers)

    @app.get("/api/source-images/{snapshot_id}/{key}")
    async def source_image(snapshot_id: UUID, key: str, request: Request, size: Literal["thumbnail", "preview"] = "thumbnail"):
        from .collector import CollectionError
        from .media_previews import image_key, source_images, source_preview_async
        def authorized_url():
            with db.connection() as c:
                user = authenticate(c, request)
                snapshot = c.execute("SELECT * FROM source_snapshots WHERE id=%s", (snapshot_id,)).fetchone()
                if not snapshot:
                    fail("来源图片不存在", 404)
                get_item(c, user, snapshot["work_item_id"])
                url = next((url for url in source_images(snapshot["body"]) if image_key(url) == key), None)
                if not url:
                    fail("图片不属于该采集记录", 404)
                return url
        url = await run_in_threadpool(authorized_url)
        try:
            return display_image(await source_preview_async(s.storage_root, url, size), request)
        except CollectionError as exc:
            fail("来源图片读取失败：" + str(exc), 502)
        except (TimeoutError, httpx.HTTPError, OSError):
            fail("来源图片暂时无法读取，请点击图片重试", 502)
        except ValueError:
            fail("来源返回的文件不是可预览图片", 422)

    @app.get("/api/deliveries/{delivery_id}/download-info")
    def download_info(delivery_id: UUID, request: Request):
        from .delivery_download import delivery_download_context

        with db.connection() as c:
            return delivery_download_context(c, authenticate(c, request), delivery_id)[2]

    @app.get("/api/deliveries/{delivery_id}/download")
    def download(delivery_id: UUID, request: Request, short_name: Annotated[str | None, Query(max_length=24)] = None):
        from .delivery_download import archive_filename, delivery_download_context

        with db.connection() as c:
            user = authenticate(c, request)
            delivery, _, info = delivery_download_context(c, user, delivery_id, short_name)
            assets = c.execute("SELECT * FROM assets WHERE id=ANY(%s)", (delivery["asset_ids"],)).fetchall()
            by_id = {str(a["id"]): a for a in assets}
            folder = info["folder"]
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
                for i, aid in enumerate(delivery["asset_ids"], 1):
                    a = by_id[str(aid)]
                    if (a.get("source_url") or "").startswith("feishu-legacy:"):
                        from .legacy import ensure_asset
                        a = ensure_asset(c, s, a)
                    data = (s.storage_root / a["object_key"]).read_bytes()
                    if hashlib.sha256(data).hexdigest() != a["sha256"]:
                        fail("原图完整性校验失败", 500)
                    z.writestr(folder + "/" + str(i).zfill(2) + "_" + archive_filename(a["name"]), data)
        from urllib.parse import quote

        return Response(
            buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(folder + ".zip")},
        )

    @app.get("/api/admin/notifications")
    def notification_status(request: Request):
        from .notifications import status

        with db.connection() as c:
            user = authenticate(c, request)
            if "admin" not in user["roles"]:
                fail("仅管理员可查看通知记录", 403)
            return serial(status(c))

    @app.put("/api/admin/notifications")
    def notification_settings(data: dict, request: Request):
        from .notifications import status

        if type(data.get("enabled")) is not bool:
            fail("通知开关无效", 422)
        with db.connection() as c:
            user = authenticate(c, request)
            if "admin" not in user["roles"]:
                fail("仅管理员可管理通知", 403)
            c.execute(
                "UPDATE notification_config SET enabled=%s,updated_at=now(),updated_by=%s WHERE singleton=true",
                (data["enabled"], user["id"]),
            )
            return serial(status(c))

    @app.post("/api/admin/notifications/test")
    def notification_test(request: Request):
        from .notifications import test_notice

        with db.connection() as c:
            user = authenticate(c, request)
            if "admin" not in user["roles"]:
                fail("仅管理员可测试通知", 403)
            return idempotent(
                c,
                user,
                request.headers.get("Idempotency-Key"),
                {"action": "notification.test"},
                lambda: test_notice(c, user),
            )

    @app.get("/api/admin/capacity")
    def admin_capacity(request: Request):
        from .capacity import status

        with db.connection() as c:
            user = authenticate(c, request)
            if "admin" not in user["roles"]:
                fail("仅管理员可查看容量配置", 403)
            return status(c, s)

    @app.get("/api/admin/collector")
    def admin_collector(request: Request, provider: str | None = None):
        from .collector_config import status

        with db.connection() as c:
            user = authenticate(c, request)
            if "admin" not in user["roles"]:
                fail("仅管理员可查看采集配置", 403)
            return serial(status(c, provider))

    @app.put("/api/admin/collector")
    def admin_collector_save(data: dict, request: Request):
        from .collector_config import save

        with db.connection() as c:
            return serial(save(c, authenticate(c, request), s, data))

    @app.post("/api/admin/collector/check")
    def admin_collector_check(request: Request, provider: str | None = None):
        from .collector_config import check

        with db.connection() as c:
            return check(c, authenticate(c, request), s, provider)

    @app.get("/api/admin/providers")
    def admin_providers(request: Request):
        from .provider_config import status

        with db.connection() as c:
            user = authenticate(c, request)
            if "admin" not in user["roles"]:
                fail("仅管理员可访问管理后台", 403)
            return serial(status(c))

    @app.put("/api/admin/providers/{provider}")
    def admin_provider_save(provider: str, data: dict, request: Request):
        from .provider_config import save

        with db.connection() as c:
            user = authenticate(c, request)
            # The key is never returned or included in audit details.
            return serial(save(c, user, s, provider, data))

    @app.get("/api/admin/jobs")
    def admin_jobs(request: Request):
        with db.connection() as c:
            user = authenticate(c, request)
            if "admin" not in user["roles"]:
                fail("仅管理员可访问管理后台", 403)
            return serial(
                c.execute(
                    "SELECT j.id,j.kind,j.payload->>'task' AS task,j.payload->>'kind' AS source_kind,j.payload->>'url' AS source_url,coalesce(j.payload->>'collector','native') AS collector,j.state,j.attempts,j.error,j.created_at,j.finished_at,w.code,j.result->>'model' AS model,j.result->>'request_id' AS request_id,j.result->'usage' AS usage,j.result->>'incomplete_reason' AS incomplete_reason,j.result->>'duration_ms' AS duration_ms,j.result->>'page_ms' AS page_ms,j.result->>'total_ms' AS total_ms FROM jobs j LEFT JOIN work_items w ON w.id=j.work_item_id ORDER BY j.created_at DESC LIMIT 100"
                ).fetchall()
            )

    @app.post("/api/admin/providers/{provider}/check")
    def admin_provider_check(provider: str, request: Request):
        from .provider_config import check_connection

        with db.connection() as c:
            return check_connection(c, authenticate(c, request), s, provider)

    if s.web_root.exists():
        app.mount("/", StaticFiles(directory=s.web_root, html=True), name="web")
    return app
