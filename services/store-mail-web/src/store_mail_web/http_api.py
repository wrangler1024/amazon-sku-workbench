"""HTTP 层:Web 会话路由(/api/web/*)、Agent API(/api/v1/*)、静态前端。

- 鉴权:Web 用 HttpOnly 会话 Cookie;Agent 用 X-API-Key(每 Key 独立限流)。
- 错误统一 {"error": {"code", "message"}} + 对应 HTTP 状态码。
- 静态文件白名单服务,无路径穿越;HTML 带 CSP。
"""

from __future__ import annotations

import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse, urlsplit

from . import __version__
from .mail_html import prepare_email_html
from .service import ApiError, Service

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 1024 * 1024
SESSION_COOKIE = "smw_session"
OAUTH_COOKIE = "smw_oauth"
APPLICANT_COOKIE = "smw_applicant"

STATIC_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".yaml": "text/yaml; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}

# (method, pattern, auth) — handler 名 = h_<method>_<name>
ROUTES: list[tuple[str, re.Pattern, str, str]] = [
    ("GET", re.compile(r"^/healthz$"), "public", "healthz"),
    ("GET", re.compile(r"^/$"), "static", "index"),
    ("GET", re.compile(r"^/index\.html$"), "static", "index"),
    ("GET", re.compile(r"^/static/(?P<name>[A-Za-z0-9._-]+)$"), "static", "file"),
    ("GET", re.compile(r"^/favicon\.svg$"), "static", "favicon"),
    ("GET", re.compile(r"^/api/openapi\.yaml$"), "public", "openapi"),

    ("POST", re.compile(r"^/api/auth/login$"), "public", "login"),
    ("POST", re.compile(r"^/api/auth/logout$"), "public", "logout"),
    ("GET", re.compile(r"^/api/auth/me$"), "session", "me"),
    ("GET", re.compile(r"^/api/auth/config$"), "public", "auth_config"),
    ("POST", re.compile(r"^/api/auth/feishu/start$"), "public", "feishu_start"),
    ("POST", re.compile(r"^/api/auth/feishu/bind$"), "session", "feishu_bind"),
    ("GET", re.compile(r"^/api/auth/feishu/callback$"), "public", "feishu_callback"),
    ("GET", re.compile(r"^/api/auth/admission$"), "applicant", "admission_status"),
    ("POST", re.compile(r"^/api/auth/admission$"), "applicant", "admission_submit"),
    ("POST", re.compile(r"^/api/auth/admission/complete$"), "applicant", "admission_complete"),
    ("GET", re.compile(r"^/api/web/admissions$"), "admin", "admissions_list"),
    ("POST", re.compile(r"^/api/web/admissions/(?P<id>\d+)/review$"), "admin", "admissions_review"),
    ("GET", re.compile(r"^/api/web/roles$"), "admin", "roles_list"),
    ("PUT", re.compile(r"^/api/web/roles/(?P<role>[a-z]+)$"), "admin", "roles_update"),

    ("GET", re.compile(r"^/api/web/accounts$"), "session", "web_accounts_list"),
    ("POST", re.compile(r"^/api/web/accounts$"), "admin", "web_accounts_create"),
    ("PUT", re.compile(r"^/api/web/accounts/(?P<id>\d+)$"), "admin", "web_accounts_update"),
    ("DELETE", re.compile(r"^/api/web/accounts/(?P<id>\d+)$"), "admin", "web_accounts_delete"),
    ("POST", re.compile(r"^/api/web/accounts/(?P<id>\d+)/test$"), "admin", "web_accounts_test"),
    ("POST", re.compile(r"^/api/web/accounts/(?P<id>\d+)/sync$"), "session", "web_accounts_sync"),
    ("GET", re.compile(r"^/api/web/dashboard$"), "session", "web_dashboard"),
    ("GET", re.compile(r"^/api/web/risk-events/(?P<id>\d+)$"), "session", "risk_event"),
    ("POST", re.compile(r"^/api/web/risk-events/(?P<id>\d+)/update$"), "session", "risk_event_update"),
    ("GET", re.compile(r"^/api/web/stores/(?P<id>\d+)/signals$"), "session", "store_signals"),
    ("GET", re.compile(r"^/api/web/stores/(?P<id>\d+)/assignees$"), "session", "store_assignees"),
    ("GET", re.compile(r"^/api/web/messages$"), "session", "web_messages"),
    ("GET", re.compile(r"^/api/web/messages/(?P<id>\d+)$"), "session", "web_message_detail"),
    ("GET", re.compile(r"^/api/web/messages/(?P<id>\d+)/html$"), "session", "web_message_html"),
    ("POST", re.compile(r"^/api/web/messages/(?P<id>\d+)/read$"), "session", "web_message_read"),
    ("POST", re.compile(r"^/api/web/messages/(?P<id>\d+)/flags$"), "session", "web_message_flag"),
    ("POST", re.compile(r"^/api/web/messages/mark-all-read$"), "session", "web_messages_mark_all"),
    ("GET", re.compile(r"^/api/web/api-keys$"), "admin", "web_keys_list"),
    ("POST", re.compile(r"^/api/web/api-keys$"), "admin", "web_keys_create"),
    ("POST", re.compile(r"^/api/web/api-keys/(?P<id>\d+)/enable$"), "admin", "web_keys_enable"),
    ("DELETE", re.compile(r"^/api/web/api-keys/(?P<id>\d+)$"), "admin", "web_keys_delete"),
    ("GET", re.compile(r"^/api/web/users$"), "admin", "web_users_list"),
    ("POST", re.compile(r"^/api/web/users$"), "admin", "web_users_create"),
    ("PUT", re.compile(r"^/api/web/users/(?P<id>\d+)$"), "admin", "web_users_update"),
    ("DELETE", re.compile(r"^/api/web/users/(?P<id>\d+)$"), "admin", "web_users_delete"),
    ("GET", re.compile(r"^/api/web/rules$"), "admin", "web_rules_get"),
    ("PUT", re.compile(r"^/api/web/rules$"), "admin", "web_rules_put"),
    ("GET", re.compile(r"^/api/web/sync/status$"), "session", "web_sync_status"),
    ("POST", re.compile(r"^/api/web/sync$"), "admin", "web_sync"),
    ("GET", re.compile(r"^/api/web/notify-config$"), "admin", "web_notify_get"),
    ("PUT", re.compile(r"^/api/web/notify-config$"), "admin", "web_notify_put"),
    ("POST", re.compile(r"^/api/web/notify/test$"), "admin", "web_notify_test"),
    ("POST", re.compile(r"^/api/web/reclassify$"), "admin", "web_reclassify"),

    ("GET", re.compile(r"^/api/v1/accounts$"), "api", "v1_accounts"),
    ("GET", re.compile(r"^/api/v1/messages$"), "api", "v1_messages"),
    ("GET", re.compile(r"^/api/v1/messages/(?P<id>\d+)$"), "api", "v1_message_detail"),
    ("POST", re.compile(r"^/api/v1/sync$"), "api", "v1_sync"),
]

# Every protected business handler has an explicit function gate; mailbox data
# filtering remains in Service. Metadata for selectors is available to members.
ROUTE_PERMISSIONS = {
    **{name: "mail.read" for name in ("web_messages", "web_message_detail", "web_message_html", "web_message_read", "web_message_flag", "web_messages_mark_all")},
    **{name: "dashboard.read" for name in ("web_dashboard", "risk_event", "store_signals", "store_assignees")},
    "risk_event_update": "events.update",
    "web_accounts_sync": "mail.sync", "web_sync": "mail.sync",
    **{name: "accounts.manage" for name in ("web_accounts_create", "web_accounts_update", "web_accounts_delete", "web_accounts_test")},
    **{name: "members.manage" for name in ("web_users_list", "web_users_create", "web_users_update", "web_users_delete")},
    "admissions_list": "admissions.approve", "admissions_review": "admissions.approve",
    "roles_list": "roles.manage", "roles_update": "roles.manage",
    **{name: "api_keys.manage" for name in ("web_keys_list", "web_keys_create", "web_keys_enable", "web_keys_delete")},
    **{name: "settings.manage" for name in ("web_rules_get", "web_rules_put", "web_notify_get", "web_notify_put", "web_notify_test", "web_reclassify")},
}


def make_handler(service: Service, static_dir: Path, openapi_path: Path):
    static_dir = Path(static_dir)
    openapi_bytes = (
        openapi_path.read_bytes()
        if openapi_path.is_file()
        else "openapi: 3.0.3\ninfo: {title: store-mail-web, version: '0.1.0'}\npaths: {}\n".encode()
    )

    class Handler(BaseHTTPRequestHandler):
        server_version = f"store-mail-web/{__version__}"
        protocol_version = "HTTP/1.1"

        # ---------- 基础收发 ----------

        def _json(self, status: int, payload: Any,
                  extra_headers: Optional[list[tuple[str, str]]] = None) -> None:
            body = json.dumps(payload, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for name, value in (extra_headers or []):
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: int, code: str, message: str,
                   extra: Optional[dict] = None,
                   headers: Optional[list[tuple[str, str]]] = None) -> None:
            payload = {"error": {"code": code, "message": message}}
            if extra:
                payload["error"].update(extra)
            self._json(status, payload, headers)

        def _read_json(self, *, required: bool = True) -> dict:
            length_raw = self.headers.get("Content-Length", "")
            try:
                length = int(length_raw)
            except ValueError:
                if not required and not length_raw:
                    return {}
                self._error(411, "length_required", "缺少 Content-Length")
                raise _Handled()
            if length > MAX_BODY_BYTES:
                self._error(413, "body_too_large", f"请求体超过 {MAX_BODY_BYTES} 字节")
                raise _Handled()
            if length == 0:
                if required:
                    self._error(400, "empty_body", "请求体不能为空")
                    raise _Handled()
                return {}
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._error(400, "invalid_json", "请求体不是合法 JSON")
                raise _Handled()
            if not isinstance(payload, dict):
                self._error(400, "validation_error", "请求体必须是 JSON 对象")
                raise _Handled()
            return payload

        def _client_ip(self) -> str:
            forwarded = self.headers.get("X-Forwarded-For", "")
            if forwarded:
                return forwarded.split(",")[-1].strip()
            return self.client_address[0] if self.client_address else ""

        def _is_https(self) -> bool:
            return (self.headers.get("X-Forwarded-Proto", "").strip().lower() == "https"
                    or self.headers.get("X-Forwarded-Ssl", "").strip().lower() == "on")

        def _cookies(self) -> dict[str, str]:
            out: dict[str, str] = {}
            raw = self.headers.get("Cookie", "")
            for part in raw.split(";"):
                name, _, value = part.strip().partition("=")
                if name:
                    out[name] = value
            return out

        # ---------- 分发 ----------

        def _dispatch(self, method: str) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            matched_other_method = False
            for route_method, pattern, auth, name in ROUTES:
                match = pattern.match(path)
                if not match:
                    continue
                if route_method != method:
                    matched_other_method = True
                    continue
                handler = getattr(self, f"h_{name}", None)
                if handler is None:
                    continue
                self._route_context = {
                    "match": match, "query": query, "auth": auth, "name": name,
                }
                try:
                    if method not in {"GET", "HEAD", "OPTIONS"} and auth != "api":
                        self._check_origin()
                    self._authorize(auth)
                    handler(**{k: v for k, v in match.groupdict().items()})
                except _Handled:
                    return
                except ApiError as exc:
                    headers = []
                    if exc.extra.get("retry_after"):
                        headers.append(("Retry-After", str(exc.extra["retry_after"])))
                    self._error(exc.status, exc.code, exc.message,
                                {k: v for k, v in exc.extra.items() if k != "retry_after"},
                                headers)
                    return
                except Exception:
                    logger.exception("HTTP 处理异常 %s %s", method, path)
                    self._error(500, "internal_error", "服务内部错误")
                return
            if matched_other_method:
                self._error(405, "method_not_allowed", f"该方法不支持 {method} {path}")
            else:
                self._error(404, "not_found", f"路径不存在: {path}")

        def _authorize(self, auth: str) -> None:
            """public/static 不鉴权;session=登录即可;admin=需管理员;
            api=Agent Key。登录用户解析为 viewer 存入路由上下文。"""
            if auth == "applicant":
                service.applicant_view(self._cookies().get(APPLICANT_COOKIE, ""))
            elif auth in {"session", "admin"}:
                token = self._cookies().get(SESSION_COOKIE, "")
                if not token:
                    raise ApiError(401, "unauthorized", "未登录")
                payload = service.verify_session(token)
                viewer = service.resolve_viewer(payload)
                if auth == "admin":
                    service.require_admin(viewer)
                permission = ROUTE_PERMISSIONS.get(self._route_context["name"])
                if permission:
                    service.require_permission(viewer, permission)
                self._route_context["viewer"] = viewer
            elif auth == "api":
                key = self.headers.get("X-API-Key", "").strip()
                if not key:
                    bearer = self.headers.get("Authorization", "")
                    if bearer.lower().startswith("bearer "):
                        key = bearer[7:].strip()
                if not key:
                    raise ApiError(401, "unauthorized",
                                   "缺少 API Key(X-API-Key 头或 Authorization: Bearer)")
                service.authenticate_api_key(key)

        def _check_origin(self) -> None:
            origin = self.headers.get("Origin")
            expected = service.settings.public_url.rstrip("/") or (("https" if self._is_https() else "http") + "://" + self.headers.get("Host", ""))
            if (origin and origin != expected) or self.headers.get("Sec-Fetch-Site") == "cross-site":
                raise ApiError(403, "cross_origin_denied", "请求来源校验失败")
            if self.headers.get("Content-Length", "0") != "0" and self.headers.get_content_type() != "application/json":
                raise ApiError(415, "json_required", "请求必须使用 application/json")

        def _cookie(self, name, value, ttl):
            cookie = f"{name}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={ttl}"
            if self._is_https():
                cookie += "; Secure"
            return ("Set-Cookie", cookie)

        def _redirect(self, location, cookies=()):
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            for name, value in cookies:
                self.send_header(name, value)
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._dispatch("PUT")

        def do_DELETE(self) -> None:  # noqa: N802
            self._dispatch("DELETE")

        def log_message(self, format: str, *args: object) -> None:
            # OAuth code/state and search queries must not enter HTTP logs.
            logger.info("%s %s %s", self.address_string(), self.command, urlparse(self.path).path)

        # ---------- 公共/静态 ----------

        def h_healthz(self) -> None:
            self._json(200, service.health())

        def _serve_static(self, filename: str) -> None:
            path = static_dir / filename
            if not path.is_file():
                self._error(404, "not_found", f"静态文件不存在: {filename}")
                return
            body = path.read_bytes()
            suffix = path.suffix.lower()
            self.send_response(200)
            self.send_header("Content-Type",
                             STATIC_CONTENT_TYPES.get(suffix, "application/octet-stream"))
            self.send_header("Content-Length", str(len(body)))
            # no-store:内嵌浏览器对 no-cache + 无验证器的组合可能直接用缓存,
            # 静态资源发版后用户刷新必须拿到新文件
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if suffix == ".html":
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; "
                    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                    "connect-src 'self'; frame-src 'self'",
                )
            self.end_headers()
            self.wfile.write(body)

        def h_index(self) -> None:
            self._serve_static("index.html")

        def h_favicon(self) -> None:
            self._serve_static("favicon.svg")

        def h_file(self, name: str) -> None:
            self._serve_static(name)

        def h_openapi(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/yaml; charset=utf-8")
            self.send_header("Content-Length", str(len(openapi_bytes)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(openapi_bytes)

        # ---------- 登录/会话 ----------

        def h_login(self) -> None:
            payload = self._read_json()
            result = service.login(
                str(payload.get("username") or ""),
                str(payload.get("password") or ""),
                ip=self._client_ip(),
            )
            cookie = (
                f"{SESSION_COOKIE}={result['token']}; Path=/; HttpOnly; SameSite=Lax; "
                f"Max-Age={service.settings.session_ttl_hours * 3600}"
            )
            if self._is_https():
                cookie += "; Secure"
            self._json(200, {"ok": True, "user": result["user"]},
                       extra_headers=[("Set-Cookie", cookie)])

        def h_logout(self) -> None:
            service.store.logout_session(self._cookies().get(SESSION_COOKIE, ""), self._cookies().get(APPLICANT_COOKIE, ""))
            cookie = (f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; "
                      "Max-Age=0")
            self._json(200, {"ok": True}, extra_headers=[("Set-Cookie", cookie), self._cookie(APPLICANT_COOKIE, "", 0)])

        def h_auth_config(self):
            self._json(200, service.auth_config())

        def _start_feishu(self, bind=False):
            payload = self._read_json(required=False)
            result = service.start_feishu_auth(payload.get("return_to"), self._cookies().get(SESSION_COOKIE) if bind else None)
            self._json(200, {"url": result["url"]}, [self._cookie(OAUTH_COOKIE, result["browser"], 300)])

        def h_feishu_start(self):
            if service.login_throttle.blocked(self._client_ip(), "oauth"):
                raise ApiError(429, "rate_limited", "登录请求过于频繁，请稍后重试")
            service.login_throttle.record_failure(self._client_ip(), "oauth")
            self._start_feishu()

        def h_feishu_bind(self):
            self._start_feishu(bind=True)

        def h_feishu_callback(self):
            q = self._route_context["query"]
            cookies = [self._cookie(OAUTH_COOKIE, "", 0)]
            try:
                if not q.get("code") or q.get("error"):
                    raise ApiError(400, "oauth_denied", "未完成飞书授权")
                result = service.finish_feishu_auth(q["code"], q.get("state", ""), self._cookies().get(OAUTH_COOKIE, ""), self._cookies().get(SESSION_COOKIE, ""))
                service.login_throttle.reset(self._client_ip(), "oauth")
                if result.get("token"):
                    cookies += [self._cookie(SESSION_COOKIE, result["token"], service.settings.session_ttl_hours*3600), self._cookie(APPLICANT_COOKIE, "", 0)]
                else:
                    cookies += [self._cookie(APPLICANT_COOKIE, result["applicant_token"], 3600), self._cookie(SESSION_COOKIE, "", 0)]
                self._redirect(result["return_to"], cookies)
            except ApiError as exc:
                logger.warning("Feishu callback rejected: %s", exc.code)
                self._redirect("/?login_error=feishu#/inbox", cookies)

        def h_admission_status(self):
            self._json(200, service.admission_view(service.applicant_view(self._cookies().get(APPLICANT_COOKIE, ""))))

        def h_admission_submit(self):
            self._json(200, service.submit_admission(self._cookies().get(APPLICANT_COOKIE, ""), self._read_json()))

        def h_admission_complete(self):
            result = service.complete_admission(self._cookies().get(APPLICANT_COOKIE, ""))
            service.store.logout_session("", self._cookies().get(APPLICANT_COOKIE, ""))
            self._json(200, {"ok": True, "user": result["user"]}, [self._cookie(SESSION_COOKIE, result["token"], service.settings.session_ttl_hours*3600), self._cookie(APPLICANT_COOKIE, "", 0)])

        def h_admissions_list(self):
            service.require_permission(self._route_context["viewer"], "admissions.approve")
            self._json(200, {"applications": service.store.list_admissions()})

        def h_admissions_review(self, id):
            self._json(200, service.review_admission(int(id), self._read_json(), self._route_context["viewer"]))

        def h_roles_list(self):
            self._json(200, service.roles_view(self._route_context["viewer"]))

        def h_roles_update(self, role):
            self._json(200, service.update_role(role, self._read_json(), self._route_context["viewer"]))

        def h_me(self) -> None:
            viewer = self._route_context["viewer"]
            payload = service.verify_session(
                self._cookies().get(SESSION_COOKIE, ""))
            self._json(200, {
                "ok": True,
                "username": payload["u"],
                "role": viewer.role,
                "user": service._user_view(viewer),
                "expires_at": payload["exp"],
            })

        # ---------- Web:账号 ----------

        def h_web_accounts_list(self) -> None:
            viewer = self._route_context["viewer"]
            self._json(200, {"accounts": service.accounts_view(viewer)})

        def h_web_accounts_create(self) -> None:
            payload = self._read_json()
            viewer = self._route_context["viewer"]
            self._json(201, {"account": service.create_account(payload, viewer)})

        def h_web_accounts_update(self, id: str) -> None:
            payload = self._read_json()
            viewer = self._route_context["viewer"]
            self._json(200, {"account": service.update_account(int(id), payload, viewer)})

        def h_web_accounts_delete(self, id: str) -> None:
            service.delete_account(int(id), self._route_context["viewer"])
            self._json(200, {"ok": True})

        def h_web_accounts_test(self, id: str) -> None:
            self._json(200, service.test_account(
                int(id), self._route_context["viewer"]))

        def h_web_accounts_sync(self, id: str) -> None:
            results = service.trigger_sync(
                {"account_id": int(id)}, force=True,
                viewer=self._route_context["viewer"])
            self._json(200, {"results": results})

        # ---------- Web:用户(admin) ----------

        def h_web_users_list(self) -> None:
            viewer = self._route_context["viewer"]
            self._json(200, {"users": service.users_view(viewer)})

        def h_web_users_create(self) -> None:
            payload = self._read_json()
            viewer = self._route_context["viewer"]
            self._json(201, {"user": service.create_user(payload, viewer)})

        def h_web_users_update(self, id: str) -> None:
            payload = self._read_json()
            viewer = self._route_context["viewer"]
            self._json(200, {"user": service.update_user(int(id), payload, viewer)})

        def h_web_users_delete(self, id: str) -> None:
            service.delete_user(int(id), self._route_context["viewer"])
            self._json(200, {"ok": True})

        # ---------- Web:邮件 ----------

        def h_web_dashboard(self) -> None:
            self._json(200, service.dashboard(self._route_context["viewer"]))

        def h_risk_event(self, id: str) -> None:
            self._json(200, {"event": service.risk_event(
                int(id), self._route_context["viewer"])})

        def h_risk_event_update(self, id: str) -> None:
            payload = self._read_json()
            self._json(200, {"event": service.update_risk_event(
                int(id), payload, self._route_context["viewer"])})

        def h_store_signals(self, id: str) -> None:
            self._json(200, service.risk_store_detail(
                int(id), self._route_context["viewer"]))

        def h_store_assignees(self, id: str) -> None:
            self._json(200, {"users": service.risk_eligible_users(
                int(id), self._route_context["viewer"])})

        def h_web_messages(self) -> None:
            self._json(200, service.messages(self._route_context["query"],
                                             self._route_context["viewer"]))

        def h_web_message_detail(self, id: str) -> None:
            self._json(200, {"message": service.message_detail(
                int(id), viewer=self._route_context["viewer"])})

        def h_web_message_html(self, id: str) -> None:
            query = self._route_context["query"]
            if query.get("remote", "1") not in {"0", "1"}:
                raise ApiError(400, "validation_error", "remote 应为 0 或 1")
            allow_remote = query.get("remote", "1") == "1"
            html = prepare_email_html(
                service.message_html(int(id), self._route_context["viewer"]),
                allow_remote=allow_remote)
            body = html.encode("utf-8")
            # 独立响应使邮件 iframe 能有自己的 CSP；srcdoc 会继承主页面 CSP。
            csp = (
                "sandbox; frame-ancestors 'self'; default-src 'none'; script-src 'none'; "
                "object-src 'none'; frame-src 'none'; connect-src 'none'; "
                "form-action 'none'; base-uri 'none'; "
                "style-src 'unsafe-inline'" + (" https:" if allow_remote else "") + "; "
                "img-src data:" + (" https:" if allow_remote else "") + "; "
                "font-src data:" + (" https:" if allow_remote else "")
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", csp)
            self.end_headers()
            self.wfile.write(body)

        def h_web_message_read(self, id: str) -> None:
            payload = self._read_json()
            unread = payload.get("unread")
            if not isinstance(unread, bool):
                self._error(400, "validation_error", "unread 必须是布尔值")
                return
            service.set_message_read(int(id), unread,
                                     self._route_context["viewer"])
            self._json(200, {"ok": True})

        def h_web_messages_mark_all(self) -> None:
            self._json(200, service.mark_all_read(self._route_context["viewer"]))

        def h_web_message_flag(self, id: str) -> None:
            payload = self._read_json()
            flag = payload.get("flag")
            on = payload.get("on")
            if not isinstance(flag, str):
                self._error(400, "validation_error", "flag 必须是字符串")
                return
            if not isinstance(on, bool):
                self._error(400, "validation_error", "on 必须是布尔值")
                return
            self._json(200, service.set_message_flag(
                int(id), flag, on, self._route_context["viewer"]))

        # ---------- Web:API Key ----------

        def h_web_keys_list(self) -> None:
            self._json(200, {"api_keys": service.list_api_keys(
                self._route_context["viewer"])})

        def h_web_keys_create(self) -> None:
            payload = self._read_json()
            meta, plaintext = service.create_api_key(
                payload, self._route_context["viewer"])
            self._json(201, {"api_key": meta, "key": plaintext})

        def h_web_keys_enable(self, id: str) -> None:
            payload = self._read_json()
            enabled = payload.get("enabled")
            if not isinstance(enabled, bool):
                self._error(400, "validation_error", "enabled 必须是布尔值")
                return
            self._json(200, {"api_key": service.set_api_key_enabled(
                int(id), enabled, self._route_context["viewer"])})

        def h_web_keys_delete(self, id: str) -> None:
            service.delete_api_key(int(id), self._route_context["viewer"])
            self._json(200, {"ok": True})

        # ---------- Web:规则/同步 ----------

        def h_web_rules_get(self) -> None:
            self._json(200, service.get_rules())

        def h_web_rules_put(self) -> None:
            payload = self._read_json()
            self._json(200, service.update_rules(payload))

        def h_web_sync_status(self) -> None:
            self._json(200, service.sync_status(self._route_context["viewer"]))

        def h_web_sync(self) -> None:
            payload = self._read_json(required=False)
            force = bool(payload.get("force"))
            self._json(200, {"results": service.trigger_sync(payload, force=force)})

        def h_web_notify_get(self) -> None:
            self._json(200, service.notify_config())

        def h_web_notify_put(self) -> None:
            payload = self._read_json()
            self._json(200, service.update_notify_config(payload))

        def h_web_notify_test(self) -> None:
            self._json(200, service.notify_test(self._route_context["viewer"]))

        def h_web_reclassify(self) -> None:
            self._json(200, service.reclassify())

        # ---------- Agent API v1 ----------

        def h_v1_accounts(self) -> None:
            self._json(200, {"accounts": service.accounts_view()})

        def h_v1_messages(self) -> None:
            self._json(200, service.messages(self._route_context["query"]))

        def h_v1_message_detail(self, id: str) -> None:
            self._json(200, {"message": service.message_detail(int(id), for_agent=True)})

        def h_v1_sync(self) -> None:
            payload = self._read_json(required=False)
            force = bool(payload.get("force"))
            self._json(200, {"results": service.trigger_sync(payload, force=force)})

    return Handler


class _Handled(Exception):
    """响应已发送,跳过后续处理。"""


def start_http_server(host: str, port: int, service: Service,
                      static_dir: Path, openapi_path: Path) -> ThreadingHTTPServer:
    handler = make_handler(service, static_dir, openapi_path)
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.5},
                              name="http-server", daemon=True)
    thread.start()
    return server
