import hashlib
import secrets
from uuid import UUID

from fastapi import HTTPException

COOKIE = "aigc_session"


def new_session(c, user_id, hours=8):
    token = secrets.token_urlsafe(36)
    c.execute(
        "INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(%s,%s,now()+(%s * interval '1 hour'))",
        (hashlib.sha256(token.encode()).hexdigest(), user_id, hours),
    )
    return token


def authenticate(c, request):
    embedded = request.cookies.get("__Host-aigc_base_session")
    token = embedded or request.cookies.get(COOKIE)
    if request.headers.get("x-workbench-context") == "feishu-base":
        token = embedded
    auth = request.headers.get("Authorization", "")
    if not token and auth.startswith("Bearer "):
        token = auth[7:]
    if not token:
        raise HTTPException(401, "请使用飞书账号登录")
    user = c.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=%s AND s.expires_at>now() AND u.active=true",
        (hashlib.sha256(token.encode()).hexdigest(),),
    ).fetchone()
    from .roster_sync import fresh

    if not user or not fresh(user):
        raise HTTPException(401, "登录已过期或账号无工作台权限")
    if request.method not in ("GET", "HEAD", "OPTIONS") and (request.cookies.get(COOKIE) or embedded):
        origin = request.headers.get("origin")
        if (
            origin != request.app.state.settings.public_url.rstrip("/")
            or request.headers.get("x-requested-with") != "amazon-workbench"
        ):
            raise HTTPException(403, "请求来源校验失败")
    preview = request.headers.get("x-workbench-preview")
    if preview:
        if "admin" not in user["roles"]:
            raise HTTPException(403, "仅管理员可使用美工只读预览")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            raise HTTPException(403, "管理员只读预览不允许执行写操作，请返回实际工作页面")
        if preview == "all":
            return {**user, "preview_all_artists": True, "read_only_preview": True}
        try:
            artist_id = UUID(preview)
        except (ValueError, TypeError):
            raise HTTPException(403, "预览美工无效") from None
        from .domain import active_artist

        artist = c.execute("SELECT * FROM users WHERE id=%s", (artist_id,)).fetchone()
        if not artist or not active_artist(artist):
            raise HTTPException(403, "仅可预览在职内部美工，请重新选择")
        # Keep the authenticated administrator; this is a read scope, never impersonation.
        user = {**user, "preview_artist_id": str(artist_id), "read_only_preview": True}
    return user
