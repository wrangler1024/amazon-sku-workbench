"""Feishu OAuth and application bot client. Credentials never enter logs."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


class FeishuError(Exception):
    def __init__(self, code, message="飞书服务暂不可用", retryable=True):
        super().__init__(message)
        self.code = str(code)
        self.retryable = retryable


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class FeishuClient:
    AUTH_URL = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
    # The authorize endpoint's official PKCE compatibility note specifies v2.
    # Select it explicitly; never retry a consumed authorization code on another endpoint.
    TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
    API = "https://open.feishu.cn/open-apis"

    def __init__(self, settings):
        self.settings = settings
        self._token = ""
        self._token_until = 0
        self._lock = threading.RLock()
        self._member_cache = {}
        self._opener = urllib.request.build_opener(NoRedirect())

    @property
    def configured(self):
        return bool(self.settings.feishu_app_id and self.settings.feishu_app_secret
                    and self.settings.feishu_tenant_key and self.settings.public_url)

    def _request(self, url, payload=None, token=None):
        headers = {"Content-Type": "application/json"}
        data_bytes = json.dumps(payload).encode() if payload is not None else None
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(url, data=data_bytes,
                                     headers=headers, method="POST" if payload is not None else "GET")
        try:
            with self._opener.open(req, timeout=12) as response:
                data = json.loads(response.read(1024 * 1024).decode())
        except urllib.error.HTTPError as exc:
            # Never include provider bodies, credential-bearing request URLs or headers.
            try:
                error = json.loads(exc.read(65536).decode())
                detail = str(error.get("code") or error.get("error") or "")
                detail = detail if re.fullmatch(r"[A-Za-z0-9_:-]{1,64}", detail) else ""
            except (ValueError, OSError):
                detail = ""
            raise FeishuError("http_" + str(exc.code) + (":"+detail if detail else ""),
                              retryable=exc.code == 429 or exc.code >= 500) from None
        except (OSError, ValueError, TimeoutError):
            raise FeishuError("network_error") from None
        if not isinstance(data, dict):
            raise FeishuError("invalid_response")
        code = data.get("code", 0)
        if code not in (0, "0") or data.get("error"):
            code = code or data.get("error")
            if str(code) in {"99991663", "99991668"}:
                self._token_until = 0
            raise FeishuError(code, retryable=str(code) in {"230020", "99991400", "99991663", "99991668"})
        return data

    def authorize_url(self, state, verifier):
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        return self.AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": self.settings.feishu_app_id, "response_type": "code",
            "redirect_uri": self.settings.public_url.rstrip("/") + "/api/auth/feishu/callback",
            "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        })

    def exchange(self, code, verifier):
        token = self._request(self.TOKEN_URL, {
            "grant_type": "authorization_code", "client_id": self.settings.feishu_app_id,
            "client_secret": self.settings.feishu_app_secret, "code": code,
            "code_verifier": verifier,
            "redirect_uri": self.settings.public_url.rstrip("/") + "/api/auth/feishu/callback",
        }).get("access_token")
        if not isinstance(token, str) or not token:
            raise FeishuError("missing_token", retryable=False)
        info = self._request(self.API + "/authen/v1/user_info", token=token).get("data", {})
        if (not info.get("open_id") or info.get("tenant_key") != self.settings.feishu_tenant_key):
            raise FeishuError("wrong_tenant", "仅允许本企业成员使用", retryable=False)
        return {"open_id": info["open_id"], "tenant_key": info["tenant_key"],
                "name": str(info.get("name") or "飞书成员")[:64]}

    def tenant_token(self):
        with self._lock:
            if time.time() < self._token_until:
                return self._token
            data = self._request(self.API + "/auth/v3/tenant_access_token/internal", {
                "app_id": self.settings.feishu_app_id, "app_secret": self.settings.feishu_app_secret})
            token = data.get("tenant_access_token")
            if not token:
                raise FeishuError("missing_tenant_token")
            self._token = token
            self._token_until = time.time() + max(1, int(data.get("expire", 7200)) - 120)
            return token

    def member_active(self, open_id):
        """Five-minute upper bound for directory revocation checks; fail closed."""
        with self._lock:
            cached = self._member_cache.get(open_id)
            if cached and cached[0] > time.time():
                return cached[1]
        try:
            data = self._request(self.API + "/contact/v3/users/" + urllib.parse.quote(open_id, safe="")
                                 + "?user_id_type=open_id", token=self.tenant_token())
        except FeishuError as exc:
            if exc.code in {"41050", "43001", "44004"}:
                return False
            raise
        status = data.get("data", {}).get("user", {}).get("status")
        if not isinstance(status, dict) or "is_activated" not in status:
            raise FeishuError("member_status_unavailable", "无法核验飞书成员状态，请检查通讯录权限")
        active = status.get("is_activated") is True and not any(
            status.get(k) for k in ("is_frozen", "is_resigned", "is_exited", "is_unjoin"))
        with self._lock:
            self._member_cache[open_id] = (time.time() + 300, active)
        return active

    def send_text(self, open_id, text, uuid):
        result = self._request(self.API + "/im/v1/messages?receive_id_type=open_id", {
            "receive_id": open_id, "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False), "uuid": uuid,
        }, token=self.tenant_token())
        mid = result.get("data", {}).get("message_id")
        if not mid:
            raise FeishuError("missing_message_id")
        return mid
