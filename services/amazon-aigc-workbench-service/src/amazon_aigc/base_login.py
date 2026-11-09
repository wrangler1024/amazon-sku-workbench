"""Verify a short-lived SDK write using Feishu's read-only creator metadata.

The browser receives a secret but writes only its digest into the pinned auth
Table. We never trust a client-supplied user, role, Base or table identifier.
"""

import hashlib
import logging
import re
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from .auth import new_session
from .feishu import FeishuGateway

TABLE = "tblExampleProofTest"
PARENT_ORIGIN = "https://blk_000000000000000000000001.feishupkg.com"
EMBED_COOKIE = "__Host-aigc_base_session"
FIELDS = {
    "校验摘要": ("fldExampleProof", 1),
    "创建人": ("fldExample17e46ecf", 1003),
    "创建时间": ("fldExample15cc4591", 1001),
    "更新人": ("fldExample82574217", 1004),
    "更新时间": ("fldExample40c2110c", 1002),
}



class BaseLoginGateway(FeishuGateway):
    """Process-local connection/token reuse, with bounded parallel proof reads."""

    def __init__(self, settings, client=None):
        super().__init__(settings, client or httpx.Client(
            limits=httpx.Limits(max_connections=12, max_keepalive_connections=8, keepalive_expiry=60),
        ))
        self.readers = ThreadPoolExecutor(max_workers=8, thread_name_prefix="base-proof")

    def read_proof(self, prefix, record_id):
        # Both authoritative reads still run for every login; schema is never cached.
        schema = self.readers.submit(self.request, "GET", prefix + "/fields", params={"page_size": 100}, timeout=10)
        record = self.readers.submit(self.request, "GET", prefix + "/records/" + record_id,
                                     params={"user_id_type": "union_id"}, timeout=10)
        return schema.result(), record.result()["record"]

    def close(self):
        self.readers.shutdown(wait=True)
        self.client.close()


def check_request(request, settings):
    if not settings.base_login_enabled:
        raise HTTPException(403, "当前环境尚未启用飞书插件身份识别")
    if (
        request.headers.get("origin") not in (settings.public_url.rstrip("/"), settings.base_parent_origin)
        or request.headers.get("x-requested-with") != "amazon-workbench"
    ):
        raise HTTPException(403, "身份识别请求来源不匹配")


def digest(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def issue(db):
    secret = secrets.token_urlsafe(36)
    identifier = uuid4()
    with db.connection() as c:
        c.execute("SELECT pg_advisory_xact_lock(58017002)")
        c.execute("DELETE FROM base_login_challenges WHERE expires_at<now()-interval '1 day'")
        n = c.execute(
            "SELECT count(*) AS n FROM base_login_challenges WHERE issued_at>now()-interval '1 minute'"
        ).fetchone()["n"]
        if n >= 300:
            raise HTTPException(429, "身份识别请求较多，请稍后重试")
        c.execute("INSERT INTO base_login_challenges(id,secret_hash) VALUES(%s,%s)", (identifier, digest(secret)))
    return {"challenge_id": str(identifier), "secret": secret, "proof": digest(secret), "expires_in": 120}


def validate_challenge(row, secret):
    if (
        not row
        or row["used_at"]
        or row["expires_at"] <= datetime.now(UTC)
        or not secrets.compare_digest(row["secret_hash"], digest(secret))
    ):
        raise HTTPException(401, "身份校验已失效，请重新识别")


def verified_actor(record, fields, challenge, expected_fields=None):
    # Field IDs and immutable system types must still be the configured schema.
    actual = {x.get("field_name"): (x.get("field_id"), x.get("type")) for x in fields}
    if any(actual.get(name) != expected for name, expected in (expected_fields or FIELDS).items()):
        raise HTTPException(503, "身份校验表结构不匹配，请联系管理员")
    f = record.get("fields") or {}
    created = f.get("创建人") or {}
    modified = f.get("更新人") or {}
    created_at, modified_at = f.get("创建时间"), f.get("更新时间")
    if (
        f.get("校验摘要") != challenge["secret_hash"]
        or not isinstance(created, dict)
        or not isinstance(modified, dict)
        or not str(created.get("id", "")).startswith("on_")
        or created.get("id") != modified.get("id")
        or isinstance(created_at, bool)
        or not isinstance(created_at, (int, float))
        or created_at != modified_at
        or not challenge["issued_at"].timestamp() * 1000 - 2000
        <= created_at
        <= challenge["expires_at"].timestamp() * 1000
    ):
        raise HTTPException(401, "无法核验当前飞书操作者，请重新识别")
    return created["id"]


def exchange(db, settings, payload, gateway=None, defer_cleanup=None):
    try:
        identifier = UUID(payload.get("challenge_id", ""))
        secret, record_id = payload["secret"], payload["record_id"]
        if (
            not isinstance(secret, str)
            or not 32 <= len(secret) <= 100
            or not re.fullmatch(r"rec[A-Za-z0-9]{6,60}", record_id)
        ):
            raise ValueError()
    except (ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(422, "身份校验信息不完整") from None
    with db.connection() as c:
        row = c.execute("SELECT * FROM base_login_challenges WHERE id=%s FOR UPDATE", (identifier,)).fetchone()
        validate_challenge(row, secret)
        if row["attempts"] >= 4:
            raise HTTPException(429, "本次身份校验重试过多，请重新识别")
        c.execute("UPDATE base_login_challenges SET attempts=attempts+1 WHERE id=%s", (identifier,))
    gateway = gateway or FeishuGateway(settings)
    prefix = f"/bitable/v1/apps/{settings.base_id}/tables/{settings.base_auth_table}"
    try:
        if isinstance(gateway, BaseLoginGateway):
            schema, record = gateway.read_proof(prefix, record_id)
        else:
            schema = gateway.request("GET", prefix + "/fields", params={"page_size": 100}, timeout=10)
            record = gateway.request("GET", prefix + "/records/" + record_id, params={"user_id_type": "union_id"}, timeout=10)["record"]
    except (RuntimeError, KeyError, httpx.HTTPError) as exc:
        raise HTTPException(503, "飞书身份核验暂未完成，请重试识别") from exc
    actor = verified_actor(record, schema.get("items", []), row, settings.base_auth_fields)
    with db.connection() as c:
        row = c.execute("SELECT * FROM base_login_challenges WHERE id=%s FOR UPDATE", (identifier,)).fetchone()
        validate_challenge(row, secret)
        user = c.execute(
            "SELECT u.* FROM users u JOIN external_identities e ON e.user_id=u.id WHERE e.provider='feishu_union' AND e.tenant=%s AND e.subject=%s AND u.active=true AND u.internal=true",
            (settings.tenant, actor),
        ).fetchone()
        if not user or not user["roles"]:
            raise HTTPException(403, "当前飞书账号未获得工作台权限或已停用，请联系管理员")
        token = new_session(c, user["id"], settings.session_hours)
        c.execute(
            "UPDATE base_login_challenges SET used_at=now(),record_id=%s,user_id=%s WHERE id=%s",
            (record_id, user["id"], identifier),
        )
        c.execute(
            "INSERT INTO audit_events(actor_id,action,detail) VALUES(%s,'auth.base_login',%s)",
            (user["id"], Jsonb({"challenge_id": str(identifier), "base_id": settings.base_id})),
        )
    # Cleanup is independent of the completed authoritative identity check. API
    # responses schedule it after sending the token, avoiding another Feishu RTT.
    def cleanup():
        try:
            gateway.request("DELETE", prefix + "/records/" + record_id, timeout=5)
        except (RuntimeError, httpx.HTTPError):
            logging.getLogger(__name__).warning("Temporary Base identity proof cleanup pending")

    if defer_cleanup:
        defer_cleanup(cleanup)
    else:
        cleanup()
    return token
