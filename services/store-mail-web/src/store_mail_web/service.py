"""业务编排层:Web 界面与 Agent API 共用的操作集合。

多用户模型(v0.11):viewer 参数贯穿读取路径，功能由角色权限控制。
- admin: 全量可见，具备固定的成员、审批与角色管理能力；env 超管另有保护。
- operator/supervisor/director: 仅见被分配的邮箱，已读状态独立(message_reads)
Agent API(v1)不做用户区分,始终全量(仅管理员可发 Key)。
所有可预期的业务错误统一抛 ApiError(status, code, message)。
"""

from __future__ import annotations

import hmac
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from . import __version__
from .classifier import (
    CATEGORIES, DEFAULT_AMAZON_RULES, DEFAULT_CATEGORY_RULES,
    classify, extract_amount, is_amazon_mail,
    load_amazon_rules, load_category_rules,
)
from .config import Settings
from .crypto import (
    CryptoError, SessionTokens, SecretBox, generate_api_key, hash_password,
    verify_password,
)
from .imap_client import BODY_HTML_MAX
from .models import (
    Account, ApiKey, ClassifiedMail, DEFAULT_PUSH_CATEGORIES,
    MESSAGE_FLAGS, User,
)
from .notify import PushNotifier, SETTING_PUSH_CATEGORIES, SETTING_SECRET, SETTING_URL
from .security import LoginThrottle, RateLimiter, key_fingerprint
from .state import Store, now_iso
from .sync import SyncEngine
from .risk import ALL_STATUSES, EVENT_LABELS, assess_mail
from .errors import ApiError
from .auth_service import AuthServiceMixin
from .feishu import FeishuClient
from .direct_notify import DirectNotifier
from .roles import RoleServiceMixin, ROLE_NAMES

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{2,32}$")



def _parse_ts(value: str, *, end_of_day: bool = False) -> int:
    """把 ISO 时间或日期转 epoch 秒。日期按 UTC 计,naive 时间按 UTC。"""
    value = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if end_of_day:
                dt = dt.replace(hour=23, minute=59, second=59)
            return int(dt.timestamp())
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError as exc:
        raise ApiError(400, "validation_error",
                       f"时间格式无效: {value!r},应为 ISO 格式如 2026-09-01 或 "
                       "2026-09-01T10:00:00+08:00") from exc


class Service(RoleServiceMixin, AuthServiceMixin):
    def __init__(self, settings: Settings, store: Store, engine: SyncEngine,
                 tokens: SessionTokens, rate_limiter: RateLimiter,
                 login_throttle: LoginThrottle, notifier: PushNotifier):
        self.settings = settings
        self.store = store
        self.engine = engine
        self.tokens = tokens
        self.rate_limiter = rate_limiter
        self.login_throttle = login_throttle
        self.notifier = notifier
        self.started_at = now_iso()
        self.feishu = FeishuClient(settings)
        self.direct_notifier = DirectNotifier(store, self.feishu, settings.public_url)

    @classmethod
    def build(cls, settings: Settings) -> "Service":
        settings.require_secret_key()
        box = SecretBox(settings.secret_key)
        store = Store(settings.db_path, box)
        superadmin = store.ensure_admin_user(settings.admin_user)
        store.configure_superadmin(superadmin.id)
        store.configure_feishu(settings.feishu_app_id, settings.feishu_tenant_key)
        engine = SyncEngine(store, settings)
        notifier = PushNotifier(store,
                                env_url=settings.feishu_webhook_url,
                                env_secret=settings.feishu_webhook_secret)
        engine.set_notifier(notifier)
        tokens = SessionTokens(settings.secret_key,
                               settings.session_ttl_hours * 3600)
        service = cls(settings, store, engine, tokens,
                      RateLimiter(), LoginThrottle(), notifier)
        if settings.feishu_app_id:
            engine.set_notifier(service.direct_notifier)
        engine.set_risk_callback(service.assess_synced_message)
        # 首次启用时历史邮件仅进入时间线，不作为新告警推送或待办。
        epoch = store.get_setting("risk_feature_epoch")
        if not epoch:
            epoch = now_iso()
            store.set_setting("risk_feature_epoch", epoch)
        service.assess_pending_mail(epoch)
        return service

    def assess_pending_mail(self, epoch: Optional[str] = None) -> None:
        """补齐旧邮件及异常中断后未完成的风险判定。"""
        epoch = epoch or self.store.get_setting("risk_feature_epoch") or now_iso()
        while True:
            batch = self.store.risk_unassessed_messages()
            if not batch:
                break
            for row in batch:
                finding = assess_mail(
                    row["subject"], row["from_addr"], row["body_text"] or "",
                    is_amazon=bool(row["is_amazon"]),
                    legacy_category=row["category"],
                )
                self.store.risk_record_message(
                    row["id"], finding, historical=row["fetched_at"] < epoch)

    def assess_synced_message(self, account_id: int, uid: int) -> Optional[int]:
        row = self.store.risk_message_by_uid(account_id, uid)
        if row:
            finding = assess_mail(
                row["subject"], row["from_addr"], row["body_text"] or "",
                is_amazon=bool(row["is_amazon"]),
                legacy_category=row["category"],
            )
            return self.store.risk_record_message(row["id"], finding)
        return None

    def start_background_workers(self) -> None:
        """启动可后台化的组件(推送队列)。HTTP/调度器由调用方控制。"""
        if self.settings.feishu_app_id:
            self.direct_notifier.start()
        else:
            self.notifier.start()

    def stop_background_workers(self) -> None:
        self.direct_notifier.stop()
        self.notifier.stop()

    # ---------- 登录与会话 ----------

    def login(self, username: str, password: str, ip: str = "") -> dict:
        """双路登录:env 超管(密码在 env)或子账号(密码哈希在库)。

        返回 {token, user},失败统一 401,防爆破按 (ip, 用户名) 记账。
        """
        username = (username or "").strip()
        if self.login_throttle.blocked(ip, username):
            raise ApiError(429, "too_many_attempts",
                           "失败次数过多,请 10 分钟后再试")
        if username.lower() == self.settings.admin_user.lower():
            ok = self._verify_admin_password(password)
            if ok:
                user = self.store.ensure_admin_user(self.settings.admin_user)
                self.login_throttle.reset(ip, username)
                self.store.set_user_last_login(user.id)
                return self._issue_session(user)
            self.login_throttle.record_failure(ip, username)
            raise ApiError(401, "invalid_credentials", "用户名或密码错误")
        # 子账号
        user = self.store.get_user_by_username(username)
        if user is None or not user.enabled:
            self.login_throttle.record_failure(ip, username)
            raise ApiError(401, "invalid_credentials", "用户名或密码错误")
        if not user.password_hash or not verify_password(password or "",
                                                         user.password_hash):
            self.login_throttle.record_failure(ip, username)
            raise ApiError(401, "invalid_credentials", "用户名或密码错误")
        self.login_throttle.reset(ip, username)
        self.store.set_user_last_login(user.id)
        identity = self.store.identity_for_user(user.id)
        if self.feishu.configured and identity:
            raise ApiError(403, "use_feishu_login", "该账号已绑定飞书，请使用企业飞书登录")
        return self._issue_session(user)

    def _verify_admin_password(self, password: str) -> bool:
        if self.settings.admin_password_hash:
            return verify_password(password or "",
                                   self.settings.admin_password_hash)
        if self.settings.admin_password:
            return hmac.compare_digest(password or "",
                                       self.settings.admin_password)
        raise ApiError(503, "admin_not_configured",
                       "管理密码未配置,请先设置 STORE_MAIL_WEB_ADMIN_PASSWORD")

    def verify_session(self, token: str) -> dict:
        try:
            payload = self.tokens.verify(token)
            session = self.store.web_session(token)
            if not session or session["user_id"] != payload["uid"]:
                raise ApiError(401, "invalid_session", "会话已注销，请重新登录")
            if session["identity_id"]:
                identity = self.store.identity(session["identity_id"])
                if not identity or identity["status"] != "approved" or identity["user_id"] != payload["uid"]:
                    raise ApiError(401, "invalid_session", "飞书绑定已失效")
                self._check_feishu_member(identity)
            return payload
        except CryptoError as exc:
            raise ApiError(401, "invalid_session", str(exc)) from exc

    def resolve_viewer(self, payload: dict) -> User:
        """会话载荷 → 当前用户。校验存在且启用(env 超管恒可用)。"""
        user = self.store.get_user(int(payload.get("uid", 0)))
        if user is None:
            raise ApiError(401, "invalid_session", "用户不存在或已删除")
        if not user.enabled:
            raise ApiError(401, "account_disabled", "账号已停用")
        self.require_pilot_role(user.role)
        return user

    # ---------- 可见性 ----------

    @staticmethod
    def _visible_account_ids(viewer: Optional[User]) -> Optional[list]:
        """None = 不限制(admin);否则为该用户可见邮箱 id 列表。"""
        if viewer is None or viewer.role == "admin":
            return None
        return list(viewer.account_ids)

    def _require_visible_account(self, viewer: Optional[User],
                                 account_id: int) -> None:
        visible = self._visible_account_ids(viewer)
        if visible is not None and account_id not in visible:
            raise ApiError(404, "account_not_found", f"账号不存在: {account_id}")

    def require_admin(self, viewer: Optional[User]) -> None:
        """viewer=None 表示 Agent/内部上下文(API Key 已鉴权),视为可信;
        只有"已登录且非管理员"的用户会被拒绝。"""
        if viewer is not None and viewer.role != "admin":
            raise ApiError(403, "forbidden", "需要管理员权限")

    # ---------- 用户管理(admin) ----------

    def _user_view(self, user: User, include_hash: bool = False) -> dict:
        view = {
            "id": user.id,
            "username": user.username,
            "full_name": user.full_name,
            "role": user.role,
            "role_name": ROLE_NAMES.get(user.role, user.role),
            "permissions": sorted(self.permissions_for(user)),
            "is_superadmin": self.is_superadmin(user),
            "enabled": user.enabled,
            "created_at": user.created_at,
            "last_login_at": user.last_login_at,
            "account_ids": list(user.account_ids),
        }
        identity = self.store.identity_for_user(user.id)
        view["feishu_bound"] = bool(identity and identity["status"] == "approved")
        view["feishu_name"] = identity["name"] if identity else None
        if include_hash:
            view["password_hash"] = user.password_hash
        return view

    def users_view(self, viewer: Optional[User]) -> list[dict]:
        self.require_permission(viewer, "members.manage")
        return [self._user_view(u) for u in self.store.list_users()]

    def create_user(self, payload: dict, viewer: Optional[User]) -> dict:
        self.require_permission(viewer, "members.manage")
        username = (payload.get("username") or "").strip()
        full_name = self._validate_full_name(payload.get("full_name"))
        password = payload.get("password") or ""
        if not USERNAME_RE.fullmatch(username):
            raise ApiError(400, "validation_error",
                           "用户名 2-32 位,仅限字母/数字/._-")
        if username.lower() == self.settings.admin_user.lower():
            raise ApiError(409, "conflict", "该用户名保留给管理员")
        if len(password) < 6:
            raise ApiError(400, "validation_error", "初始密码至少 6 位")
        account_ids = self._clean_account_ids(payload.get("account_ids"))
        if self.store.get_user_by_username(username) is not None:
            raise ApiError(409, "conflict", f"用户已存在: {username}")
        user = self.store.create_user(username, hash_password(password),
                                      role=self.clean_role(payload.get("role", "operator")), account_ids=account_ids,
                                      full_name=full_name)
        logger.info("创建子账号 %s, 可见邮箱 %s 个", username, len(account_ids))
        return self._user_view(user)

    def update_user(self, user_id: int, payload: dict,
                    viewer: Optional[User]) -> dict:
        self.require_permission(viewer, "members.manage")
        user = self.store.get_user(user_id)
        if user is None:
            raise ApiError(404, "not_found", f"用户不存在: {user_id}")
        full_name = (self._validate_full_name(payload["full_name"])
                     if "full_name" in payload else None)
        if self.is_superadmin(user):
            if not set(payload) or not set(payload).issubset({"full_name", "account_ids"}):
                raise ApiError(400, "validation_error",
                               "管理员仅可修改姓名和个人提醒邮箱")
            self.require_superadmin(viewer)
            return self._user_view(self.store.update_user(
                user_id, full_name=full_name, actor_id=viewer.id,
                account_ids=self._clean_account_ids(payload["account_ids"]) if "account_ids" in payload else None))
        password = payload.get("password")
        if password is not None:
            if not isinstance(password, str) or len(password) < 6:
                raise ApiError(400, "validation_error", "新密码至少 6 位")
        enabled = payload.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            raise ApiError(400, "validation_error", "enabled 必须是布尔值")
        account_ids = None
        if "account_ids" in payload:
            account_ids = self._clean_account_ids(payload.get("account_ids"))
        role = self.clean_role(payload["role"]) if "role" in payload else None
        if viewer and user.id == viewer.id and (enabled is False or (role is not None and role != user.role)):
            raise ApiError(403, "self_lockout", "不能停用自己或修改自己的角色，请由另一位管理员处理")
        updated = self.store.update_user(
            user_id,
            password_hash=(hash_password(password)
                           if isinstance(password, str) and password else None),
            enabled=enabled, account_ids=account_ids,
            full_name=full_name, role=role, actor_id=viewer.id)
        return self._user_view(updated)

    @staticmethod
    def _validate_full_name(value) -> str:
        if not isinstance(value, str) or not (1 <= len(value.strip()) <= 64):
            raise ApiError(400, "validation_error", "姓名应为 1-64 字")
        return value.strip()

    def delete_user(self, user_id: int, viewer: Optional[User]) -> None:
        self.require_permission(viewer, "members.manage")
        user = self.store.get_user(user_id)
        if user is None:
            raise ApiError(404, "not_found", f"用户不存在: {user_id}")
        if self.is_superadmin(user) or (viewer and user.id == viewer.id):
            raise ApiError(400, "validation_error", "不能删除超级管理员或自己的账号")
        self.store.delete_user(user_id)
        logger.info("删除子账号 id=%s username=%s", user_id, user.username)

    def _clean_account_ids(self, raw) -> tuple:
        if raw is None:
            return ()
        if not isinstance(raw, list):
            raise ApiError(400, "validation_error", "account_ids 必须是数组")
        cleaned = []
        for item in raw:
            try:
                account_id = int(item)
            except (TypeError, ValueError) as exc:
                raise ApiError(400, "validation_error",
                               "account_ids 含非整数") from exc
            if self.store.get_account(account_id) is None:
                raise ApiError(404, "account_not_found",
                               f"邮箱账号不存在: {account_id}")
            if account_id not in cleaned:
                cleaned.append(account_id)
        return tuple(cleaned)

    # ---------- 账号 ----------

    def _account_view(self, account: Account, summaries: dict) -> dict:
        summary = summaries.get(account.id, {})
        owner = (self.store.get_user(account.owner_user_id)
                 if account.owner_user_id else None)
        return {
            "id": account.id,
            "display_name": account.display_name,
            "chinese_name": account.chinese_name,
            "email": account.email,
            "imap_host": account.imap_host,
            "imap_port": account.imap_port,
            "site": account.site,
            "owner_user_id": account.owner_user_id,
            "owner_name": owner.username if owner else None,
            "owner_display_name": (owner.full_name or owner.username) if owner else None,
            "enabled": account.enabled,
            "last_sync_at": account.last_sync_at,
            "last_success_at": account.last_success_at,
            "last_sync_status": account.last_sync_status,
            "last_error": account.last_error,
            "created_at": account.created_at,
            "message_count": summary.get("total", 0),
            "unread_count": summary.get("unread", 0),
            "latest_message_at": summary.get("latest"),
        }

    def accounts_view(self, viewer: Optional[User] = None) -> list[dict]:
        visible = self._visible_account_ids(viewer)
        if visible is not None:
            accounts = [a for a in self.store.list_accounts()
                        if a.id in visible]
            summaries = self.store.account_summaries(user_id=viewer.id)
        else:
            accounts = self.store.list_accounts()
            summaries = self.store.account_summaries()
        return [self._account_view(a, summaries) for a in accounts]

    def _validate_account_payload(self, payload: dict, *, creating: bool) -> dict:
        display_name = (payload.get("display_name") or "").strip()
        chinese_name_raw = payload.get("chinese_name", "")
        if chinese_name_raw is not None and not isinstance(chinese_name_raw, str):
            raise ApiError(400, "validation_error", "内部中文名称应为文字")
        chinese_name = (chinese_name_raw or "").strip()
        if len(chinese_name) > 64:
            raise ApiError(400, "validation_error", "内部中文名称最多 64 字")
        email = (payload.get("email") or "").strip().lower()
        auth_code = payload.get("auth_code") or ""
        if creating:
            if not display_name:
                raise ApiError(400, "validation_error", "缺少店铺名称(display_name)")
            if len(display_name) > 64:
                raise ApiError(400, "validation_error", "店铺名称过长(最多 64 字)")
            if not EMAIL_RE.fullmatch(email):
                raise ApiError(400, "validation_error", f"邮箱格式无效: {email!r}")
            if not auth_code.strip():
                raise ApiError(400, "validation_error", "缺少 IMAP 授权码(auth_code)")
        elif display_name and len(display_name) > 64:
            raise ApiError(400, "validation_error", "店铺名称过长(最多 64 字)")
        elif email and not EMAIL_RE.fullmatch(email):
            raise ApiError(400, "validation_error", f"邮箱格式无效: {email!r}")
        imap_host = (payload.get("imap_host") or "").strip() or self.settings.imap_host
        try:
            imap_port = int(payload.get("imap_port") or self.settings.imap_port)
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "validation_error", "imap_port 必须是整数") from exc
        if not (1 <= imap_port <= 65535):
            raise ApiError(400, "validation_error", "imap_port 超出范围")
        site = str(payload.get("site") or "").strip().upper()
        if site and not re.fullmatch(r"[A-Z]{2,8}", site):
            raise ApiError(400, "validation_error", "站点应为 US/CA/MX/UK/AU 等代码")
        owner_id = payload.get("owner_user_id")
        if owner_id not in (None, ""):
            try:
                owner_id = int(owner_id)
            except (TypeError, ValueError) as exc:
                raise ApiError(400, "validation_error", "负责人 ID 必须是整数") from exc
            owner = self.store.get_user(owner_id)
            if owner is None or not owner.enabled:
                raise ApiError(400, "validation_error", "负责人不存在或已停用")
        else:
            owner_id = None
        return {
            "display_name": display_name,
            "chinese_name": chinese_name,
            "email": email,
            "auth_code": auth_code,
            "imap_host": imap_host,
            "imap_port": imap_port,
            "site": site,
            "owner_user_id": owner_id,
        }

    def create_account(self, payload: dict, viewer: Optional[User]) -> dict:
        self.require_permission(viewer, "accounts.manage")
        clean = self._validate_account_payload(payload, creating=True)
        if self.store.find_account(clean["email"]):
            raise ApiError(409, "conflict", f"邮箱已存在: {clean['email']}")
        account = self.store.create_account(
            clean["display_name"], clean["email"], clean["auth_code"],
            clean["imap_host"], clean["imap_port"], enabled=True,
            site=clean["site"], owner_user_id=clean["owner_user_id"],
            chinese_name=clean["chinese_name"])
        self._grant_owner_account_access(account.id, account.owner_user_id)
        logger.info("新增账号 %s (%s)", account.display_name, account.email)
        return self._account_view(account, {})

    def update_account(self, account_id: int, payload: dict,
                       viewer: Optional[User]) -> dict:
        self.require_permission(viewer, "accounts.manage")
        account = self.store.get_account(account_id)
        if account is None:
            raise ApiError(404, "not_found", f"账号不存在: {account_id}")
        clean = self._validate_account_payload(payload, creating=False)
        new_email = clean["email"]
        if new_email and new_email != account.email.lower():
            existing = self.store.find_account(new_email)
            if existing and existing.id != account_id:
                raise ApiError(409, "conflict", f"邮箱已被其他账号使用: {new_email}")
        enabled = payload.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            raise ApiError(400, "validation_error", "enabled 必须是布尔值")
        updated = self.store.update_account(
            account_id,
            display_name=clean["display_name"] or None,
            chinese_name=clean["chinese_name"],
            set_chinese_name="chinese_name" in payload,
            email=new_email or None,
            auth_code=clean["auth_code"] or None,
            imap_host=clean["imap_host"],
            imap_port=clean["imap_port"],
            enabled=enabled,
            site=clean["site"] if "site" in payload else None,
            owner_user_id=clean["owner_user_id"],
            set_owner="owner_user_id" in payload,
        )
        if "owner_user_id" in payload:
            self._grant_owner_account_access(account_id, updated.owner_user_id)
        return self._account_view(updated, {})

    def _grant_owner_account_access(self, account_id: int,
                                    owner_user_id: Optional[int]) -> None:
        if owner_user_id is None:
            return
        user = self.store.get_user(owner_user_id)
        if user and user.role != "admin" and account_id not in user.account_ids:
            self.store.update_user(user.id,
                                   account_ids=tuple(user.account_ids) + (account_id,))

    def delete_account(self, account_id: int, viewer: Optional[User]) -> None:
        self.require_permission(viewer, "accounts.manage")
        if self.store.delete_account(account_id):
            logger.info("删除账号 id=%s", account_id)
        else:
            raise ApiError(404, "not_found", f"账号不存在: {account_id}")

    def resolve_account(self, ref: Optional[str]) -> Account:
        """按 id/邮箱/店铺名解析账号。找不到抛 404。"""
        if not ref:
            raise ApiError(400, "validation_error",
                           "缺少账号标识(account_id 或 account)")
        account = self.store.find_account(ref)
        if account is None:
            raise ApiError(404, "account_not_found", f"账号不存在: {ref}")
        return account

    def test_account(self, account_id: int,
                     viewer: Optional[User] = None) -> dict:
        self.require_permission(viewer, "accounts.manage")
        account = self.resolve_account(str(account_id))
        try:
            return self.engine.test_account(account)
        except CryptoError as exc:
            return {"ok": False, "exists": None, "error": str(exc)}

    # ---------- 邮件 ----------

    @staticmethod
    def _bool_param(value: Optional[str]) -> bool:
        return (value or "").strip().lower() in {"1", "true", "yes", "on"}

    def messages(self, params: dict,
                 viewer: Optional[User] = None) -> dict:
        """聚合查询。params 来自 query string;子账号自动限定可见邮箱。"""
        account_id: Optional[int] = None
        ref = (params.get("account") or "").strip()
        raw_account_id = (params.get("account_id") or "").strip()
        if raw_account_id:
            if not raw_account_id.isdigit():
                raise ApiError(400, "validation_error", "account_id 必须是整数")
            account = self.store.get_account(int(raw_account_id))
            if account is None:
                raise ApiError(404, "account_not_found",
                               f"账号不存在: {raw_account_id}")
            account_id = account.id
        elif ref:
            account_id = self.resolve_account(ref).id
        if account_id is not None:
            self._require_visible_account(viewer, account_id)

        category = (params.get("category") or "").strip()
        if category and category not in CATEGORIES:
            raise ApiError(400, "validation_error",
                           f"category 无效,可选: {', '.join(CATEGORIES)}")

        try:
            limit = int((params.get("limit") or "50").strip())
            offset = int((params.get("offset") or "0").strip())
        except ValueError as exc:
            raise ApiError(400, "validation_error", "limit/offset 必须是整数") from exc
        if not (1 <= limit <= 200):
            raise ApiError(400, "validation_error", "limit 取值 1-200")
        if offset < 0:
            raise ApiError(400, "validation_error", "offset 不能为负")

        since_ts = until_ts = None
        if (params.get("since") or "").strip():
            since_ts = _parse_ts(params["since"])
        if (params.get("until") or "").strip():
            until_ts = _parse_ts(params["until"], end_of_day=True)

        if "otp_only" in params:
            raise ApiError(400, "validation_error", "验证码筛选已取消；请使用资金或货件筛选")
        total, rows = self.store.query_messages(
            account_id=account_id,
            visible_account_ids=self._visible_account_ids(viewer),
            user_id=viewer.id if viewer is not None else None,
            flag_type=self._clean_flag_param(params.get("flag")),
            q=(params.get("q") or "").strip() or None,
            category=category or None,
            amazon_only=self._bool_param(params.get("amazon_only")),
            finance_only=self._bool_param(params.get("finance_only")),
            shipment_only=self._bool_param(params.get("shipment_only")),
            unread_only=self._bool_param(params.get("unread_only")),
            important_only=self._bool_param(params.get("important_only")),
            since_ts=since_ts, until_ts=until_ts,
            limit=limit, offset=offset,
        )
        return {"total": total, "limit": limit, "offset": offset, "messages": rows}

    def message_detail(self, message_id: int, *, for_agent: bool = False,
                       viewer: Optional[User] = None) -> dict:
        if not isinstance(message_id, int):
            raise ApiError(400, "validation_error", "message id 必须是整数")
        row = self.store.get_message(
            message_id, with_body=True,
            user_id=viewer.id if viewer is not None else None)
        if row is None:
            raise ApiError(404, "not_found", f"邮件不存在: {message_id}")
        self._require_visible_account(viewer, row["account_id"])
        account = self.store.get_account(row["account_id"])
        # Web 用户打开详情 → 记入该用户的已读;Agent 读取不改变任何人未读。
        # IMAP \Seen 回写为全局一次性(任一用户首读即回写)。
        if not for_agent and viewer is not None and row["unread"]:
            if (self.settings.mark_seen_on_open and not row["seen_synced"]):
                if self.engine.mark_seen(row["account_id"], row["uid"]):
                    self.store.set_message_seen_synced(message_id, True)
            self.store.mark_user_read(viewer.id, message_id)
            row["unread"] = 0
        detail = dict(row)
        if not for_agent:
            detail["has_html"] = bool(detail.get("body_html"))
            detail["html_truncated"] = bool(
                detail.get("body_html") and len(detail["body_html"]) >= BODY_HTML_MAX)
        if account is not None:
            detail["account_display_name"] = account.display_name
            detail["account_chinese_name"] = account.chinese_name
            detail["account_email"] = account.email
        if for_agent:
            detail.pop("body_html", None)
            detail.pop("seen_synced", None)
        detail.pop("imap_seen", None)
        return detail

    def message_html(self, message_id: int, viewer: Optional[User]) -> str:
        """读取 Web 邮件的 HTML 正文，不改变已读状态。"""
        if viewer is None:
            raise ApiError(401, "unauthorized", "未登录")
        if not isinstance(message_id, int):
            raise ApiError(400, "validation_error", "message id 必须是整数")
        row = self.store.get_message(message_id, with_body=True)
        if row is None:
            raise ApiError(404, "not_found", f"邮件不存在: {message_id}")
        self._require_visible_account(viewer, row["account_id"])
        if not row["body_html"]:
            raise ApiError(404, "not_found", "该邮件没有 HTML 正文")
        return row["body_html"]

    def set_message_read(self, message_id: int, unread: bool,
                         viewer: Optional[User]) -> None:
        row = self.store.get_message(message_id)
        if row is None:
            raise ApiError(404, "not_found", f"邮件不存在: {message_id}")
        self._require_visible_account(viewer, row["account_id"])
        if viewer is None:
            return
        if unread:
            self.store.mark_user_unread(viewer.id, message_id)
        else:
            self.store.mark_user_read(viewer.id, message_id)

    @staticmethod
    def _clean_flag_param(value: Optional[str]) -> Optional[str]:
        raw = (value or "").strip().lower()
        if not raw:
            return None
        if raw in {"any", "marked", "flagged"}:
            return "any"
        if raw in MESSAGE_FLAGS:
            return raw
        raise ApiError(400, "validation_error",
                       f"flag 无效: {raw!r},可选 any/star/flag/pin")

    def set_message_flag(self, message_id: int, flag: str, on: bool,
                         viewer: Optional[User]) -> dict:
        """设置/取消当前用户的邮件标记。"""
        if viewer is None:
            raise ApiError(401, "unauthorized", "未登录")
        flag_type = self._clean_flag_param(flag)
        row = self.store.get_message(message_id)
        if row is None:
            raise ApiError(404, "not_found", f"邮件不存在: {message_id}")
        self._require_visible_account(viewer, row["account_id"])
        self.store.set_message_flag(viewer.id, message_id, flag_type, on)
        flags = self.store.get_message(
            message_id, user_id=viewer.id).get("flags", [])
        return {"message_id": message_id, "flags": flags}

    def mark_all_read(self, viewer: Optional[User]) -> dict:
        """把当前可见范围内全部邮件标为已读(仅影响自己)。"""
        if viewer is None:
            raise ApiError(401, "unauthorized", "未登录")
        count = self.store.mark_all_read(
            viewer.id, self._visible_account_ids(viewer))
        return {"marked": count}

    # ---------- API Key ----------

    def _api_key_view(self, key: ApiKey) -> dict:
        return {
            "id": key.id,
            "name": key.name,
            "key_prefix": key.key_prefix,
            "enabled": key.enabled,
            "rate_limit_per_min": key.rate_limit_per_min,
            "created_at": key.created_at,
            "last_used_at": key.last_used_at,
            "request_count": key.request_count,
        }

    def create_api_key(self, payload: dict,
                       viewer: Optional[User] = None) -> tuple[dict, str]:
        self.require_permission(viewer, "api_keys.manage")
        name = (payload.get("name") or "").strip()
        if not name or len(name) > 64:
            raise ApiError(400, "validation_error", "Key 名称必填(1-64 字)")
        try:
            rate = int(payload.get("rate_limit_per_min") or self.settings.api_rate_per_min)
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "validation_error", "rate_limit_per_min 必须是整数") from exc
        if not (1 <= rate <= 10000):
            raise ApiError(400, "validation_error", "rate_limit_per_min 取值 1-10000")
        plaintext = generate_api_key()
        key = self.store.create_api_key(name, plaintext[:11],
                                        key_fingerprint(plaintext), rate)
        logger.info("创建 API Key id=%s name=%s", key.id, name)
        return self._api_key_view(key), plaintext

    def list_api_keys(self, viewer: Optional[User] = None) -> list[dict]:
        self.require_permission(viewer, "api_keys.manage")
        return [self._api_key_view(k) for k in self.store.list_api_keys()]

    def set_api_key_enabled(self, key_id: int, enabled: bool,
                            viewer: Optional[User]) -> dict:
        self.require_permission(viewer, "api_keys.manage")
        key = self.store.get_api_key(key_id)
        if key is None:
            raise ApiError(404, "not_found", f"API Key 不存在: {key_id}")
        self.store.set_api_key_enabled(key_id, enabled)
        return self._api_key_view(self.store.get_api_key(key_id))

    def delete_api_key(self, key_id: int, viewer: Optional[User]) -> None:
        self.require_permission(viewer, "api_keys.manage")
        if not self.store.delete_api_key(key_id):
            raise ApiError(404, "not_found", f"API Key 不存在: {key_id}")
        logger.info("删除 API Key id=%s", key_id)

    def authenticate_api_key(self, plaintext: str) -> ApiKey:
        """Agent API 鉴权:指纹查库 + 启用检查 + 限流。失败抛 ApiError。"""
        key = self.store.find_api_key_by_hash(key_fingerprint(plaintext or ""))
        if key is None or not key.enabled:
            raise ApiError(401, "invalid_api_key", "API Key 无效或已停用")
        allowed, retry_after = self.rate_limiter.allow(key.id, key.rate_limit_per_min)
        if not allowed:
            raise ApiError(429, "rate_limited",
                           f"请求过于频繁,每分钟限 {key.rate_limit_per_min} 次",
                           extra={"retry_after": retry_after})
        self.store.record_api_key_use(key.id)
        return key

    # ---------- 规则配置 ----------

    def get_rules(self) -> dict:
        amazon = load_amazon_rules(self.store.get_setting("amazon_rules"))
        categories = load_category_rules(self.store.get_setting("category_rules"))
        return {
            "amazon_rules": amazon,
            "category_rules": categories,
            "defaults": {
                "amazon_rules": DEFAULT_AMAZON_RULES,
                "category_rules": DEFAULT_CATEGORY_RULES,
            },
        }

    def update_rules(self, payload: dict) -> dict:
        if "amazon_rules" in payload:
            raw = payload["amazon_rules"]
            if not isinstance(raw, dict):
                raise ApiError(400, "validation_error", "amazon_rules 必须是对象")
            senders = [str(x) for x in raw.get("senders", [])]
            keywords = [str(x) for x in raw.get("subject_keywords", [])]
            self.store.set_setting("amazon_rules", json.dumps(
                {"senders": senders, "subject_keywords": keywords},
                ensure_ascii=False))
        if "category_rules" in payload:
            raw = payload["category_rules"]
            if not isinstance(raw, dict):
                raise ApiError(400, "validation_error", "category_rules 必须是对象")
            clean = {}
            for category, keywords in raw.items():
                if category not in CATEGORIES or category == "其他":
                    continue
                if not isinstance(keywords, list):
                    raise ApiError(400, "validation_error",
                                   f"category_rules.{category} 必须是数组")
                clean[category] = [str(x) for x in keywords]
            self.store.set_setting("category_rules",
                                   json.dumps(clean, ensure_ascii=False))
        return self.get_rules()

    # ---------- 同步 ----------

    def trigger_sync(self, payload: Optional[dict], force: bool = False,
                     viewer: Optional[User] = None) -> list[dict]:
        self.require_permission(viewer, "mail.sync")
        payload = payload or {}
        ref = (payload.get("account") or "").strip()
        account_id = payload.get("account_id")
        if account_id is not None:
            try:
                account_id = int(account_id)
            except (TypeError, ValueError) as exc:
                raise ApiError(400, "validation_error",
                               "account_id 必须是整数") from exc
            account = self.store.get_account(account_id)
            if account is None:
                raise ApiError(404, "account_not_found", f"账号不存在: {account_id}")
            self._require_visible_account(viewer, account_id)
            results = [self.engine.sync_account(account_id, manual=True, force=force)]
        elif ref:
            account = self.resolve_account(ref)
            self._require_visible_account(viewer, account.id)
            results = [self.engine.sync_account(account.id, manual=True, force=force)]
        else:
            # 全量同步仅管理员(结果会暴露全部账号邮箱)
            self.require_admin(viewer)
            results = self.engine.sync_all(manual=True, force=force)
        return [{
            "account_id": r.account_id,
            "email": r.email,
            "status": r.status,
            "new_messages": r.new_messages,
            "fetched": r.fetched,
            "duration_ms": r.duration_ms,
            "error": r.error,
        } for r in results]

    def sync_status(self, viewer: Optional[User] = None) -> dict:
        visible = self._visible_account_ids(viewer)
        accounts = self.engine.sync_status()
        if visible is not None:
            accounts = [a for a in accounts if a["account_id"] in visible]
        return {
            "scheduler_running": self.engine.scheduler_running(),
            "interval_seconds": self.settings.sync_interval_seconds,
            "jitter_seconds": self.settings.sync_jitter_seconds,
            "accounts": accounts,
        }

    # ---------- 店铺看板 ----------

    def dashboard(self, viewer: Optional[User] = None) -> dict:
        """只呈现邮件发现的风险，不把沉默邮箱标作健康。"""
        visible = self._visible_account_ids(viewer)
        user_id = viewer.id if viewer is not None else None
        if visible is not None:
            accounts = [a for a in self.store.list_accounts()
                        if a.id in visible]
            summaries = self.store.account_summaries(user_id=user_id)
            stats = self.store.dashboard_stats(visible_account_ids=visible,
                                               user_id=user_id)
        else:
            accounts = self.store.list_accounts()
            summaries = self.store.account_summaries()
            stats = self.store.dashboard_stats()
        views = []
        for account in accounts:
            view = self._account_view(account, summaries)
            view["stats"] = stats.get(account.id, {
                "last_alert_at": None, "last_perf_at": None,
                "last_debit_at": None, "last_debit_subject": None,
                "last_payout_at": None, "last_payout_amount": None,
                "last_payout_subject": None, "important_unread": 0,
            })
            views.append(view)
        _, recent = self.store.query_messages(
            important_only=True, limit=20,
            visible_account_ids=visible, user_id=user_id)
        for row in recent:
            account = self.store.get_account(row["account_id"])
            row["account_display_name"] = account.display_name if account else ""
            row["account_chinese_name"] = account.chinese_name if account else ""
        events = self.store.risk_list_events(visible_account_ids=visible, limit=2000)
        active = [e for e in events if e["action_required"] and
                  e["status"] in ("待处理", "处理中", "待核对")]
        now = datetime.now(timezone.utc)

        def sync_interrupted(a: Account) -> bool:
            if not a.enabled:
                return False
            if a.last_sync_status != "ok" or not a.last_success_at:
                return True
            try:
                last = datetime.fromisoformat(a.last_success_at)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                return (now - last).total_seconds() > max(
                    900, self.settings.sync_interval_seconds * 3)
            except ValueError:
                return True

        def cell(items: list[dict], unavailable: bool) -> dict:
            if unavailable:
                return {"state": "unknown", "label": "监控中断", "count": len(items)}
            if not items:
                return {"state": "clear", "label": "未见邮件异常", "count": 0}
            high = any(item["severity"] == "P0" for item in items)
            return {"state": "critical" if high else "warning",
                    "label": "需立即处理" if high else "需跟进", "count": len(items)}

        matrix = []
        for account in accounts:
            interrupted = sync_interrupted(account)
            own = [e for e in active if e["account_id"] == account.id]
            aview = next(v for v in views if v["id"] == account.id)
            matrix.append({
                "account": aview, "sync_interrupted": interrupted,
                "open_count": len(own),
                "account_risk": cell([e for e in own if e["event_type"] in
                                      ("account", "performance", "compliance", "review")], interrupted),
                "listing": cell([e for e in own if e["event_type"] == "listing"], interrupted),
                "fba": cell([e for e in own if e["event_type"] == "fba"], interrupted),
                "finance": cell([e for e in own if e["event_type"] == "finance"], interrupted),
                "customer": cell([e for e in own if e["event_type"] == "customer"], interrupted),
            })
        queue = sorted(active, key=lambda e: (
            0 if e["severity"] == "P0" else 1 if e["severity"] == "P1" else 2,
            e["due_at"] or "9999", e["first_seen_at"], e["id"]))
        finance = [e for e in self.store.risk_list_events(
            visible_account_ids=visible, limit=1000)
            if e["event_type"] == "finance"][:20]
        soon = now.timestamp() + 3600
        due_soon = sum(1 for e in active if e["due_at"] and
                       datetime.fromisoformat(e["due_at"]).timestamp() <= soon)
        return {
            "accounts": views, "recent_important": recent,
            "summary": {
                "critical_open": sum(e["severity"] == "P0" for e in active),
                "due_soon": due_soon,
                "listing_open": sum(e["event_type"] == "listing" for e in active),
                "fba_open": sum(e["event_type"] == "fba" for e in active),
                "finance_open": sum(e["event_type"] == "finance" for e in active),
                "sync_interrupted": sum(sync_interrupted(a) for a in accounts),
            },
            "matrix": matrix, "queue": queue[:100], "finance": finance,
            "event_types": EVENT_LABELS,
            "data_scope": "仅基于已同步邮件；未见异常不代表卖家后台健康",
        }

    def risk_event(self, event_id: int, viewer: Optional[User]) -> dict:
        item = self.store.risk_get_event(event_id)
        if item is None:
            raise ApiError(404, "not_found", "事件不存在")
        self._require_visible_account(viewer, item["account_id"])
        return item

    def risk_store_detail(self, account_id: int, viewer: Optional[User]) -> dict:
        account = self.store.get_account(account_id)
        if account is None:
            raise ApiError(404, "account_not_found", "店铺邮箱不存在")
        self._require_visible_account(viewer, account_id)
        events = self.store.risk_list_events(
            visible_account_ids=self._visible_account_ids(viewer),
            account_id=account_id, include_archived=True, limit=300)
        groups: dict[str, dict] = {}
        for event in events:
            key = f"{event['entity_type']}:{event['entity_ref']}" if event["entity_ref"] else f"事件:{event['id']}"
            group = groups.setdefault(key, {"entity_type": event["entity_type"],
                                            "entity_ref": event["entity_ref"],
                                            "events": []})
            group["events"].append(event)
        return {"account": self._account_view(account, {}), "events": events,
                "groups": list(groups.values()),
                "data_scope": "邮件事件时间线；真实状态请在卖家后台核对"}

    def risk_eligible_users(self, account_id: int, viewer: Optional[User]) -> list[dict]:
        self._require_visible_account(viewer, account_id)
        return [self._user_view(user) for user in self.store.list_users()
                if user.enabled and (user.role == "admin" or account_id in user.account_ids)]

    def update_risk_event(self, event_id: int, payload: dict,
                          viewer: Optional[User]) -> dict:
        self.require_permission(viewer, "events.update")
        item = self.risk_event(event_id, viewer)
        if viewer is None:
            raise ApiError(401, "unauthorized", "需要登录")
        status = payload.get("status")
        if status is not None and status not in ALL_STATUSES:
            raise ApiError(400, "validation_error", "无效处理状态")
        note = payload.get("note")
        if note is not None and (not isinstance(note, str) or len(note.strip()) > 2000):
            raise ApiError(400, "validation_error", "备注最多 2000 字")
        if note is not None:
            note = note.strip()
        bank_receipt_status = payload.get("bank_receipt_status")
        if bank_receipt_status is not None:
            self.require_permission(viewer, "finance.confirm")
            if item["finance_state"] != "平台已发起":
                raise ApiError(400, "validation_error", "只有平台发起回款可核对到账")
            if bank_receipt_status not in ("待核对到账", "已核对到账"):
                raise ApiError(400, "validation_error", "无效到账核对状态")

            if bank_receipt_status == "已核对到账" and bank_receipt_status != item["bank_receipt_status"] and not note:
                raise ApiError(400, "validation_error", "确认到账须填写银行核对依据")
        set_owner = "owner_user_id" in payload
        owner_id = payload.get("owner_user_id")
        if set_owner:
            if owner_id not in (None, ""):
                try:
                    owner_id = int(owner_id)
                except (TypeError, ValueError) as exc:
                    raise ApiError(400, "validation_error", "负责人 ID 无效") from exc
                eligible = {u["id"] for u in self.risk_eligible_users(item["account_id"], viewer)}
                if owner_id not in eligible:
                    raise ApiError(400, "validation_error", "负责人无权查看该邮箱")
            else:
                owner_id = None
            if not self.has_permission(viewer, "events.assign") and (
                owner_id != viewer.id or item["owner_user_id"] not in (None, viewer.id)
            ):
                raise ApiError(403, "forbidden", "只能认领未分配给他人的事件；改派请联系管理员")
        version = payload.get("version")
        if version is not None and (not isinstance(version, int) or version < 1):
            raise ApiError(400, "validation_error", "版本号无效")
        try:
            updated = self.store.risk_update_event(
                event_id, user_id=viewer.id, status=status,
                owner_user_id=owner_id, set_owner=set_owner,
                bank_receipt_status=bank_receipt_status,
                note=note, expected_version=version)
        except ValueError as exc:
            raise ApiError(409, "conflict", str(exc)) from exc
        return updated

    # ---------- 飞书推送配置 ----------

    def notify_config(self) -> dict:
        if self.settings.feishu_app_id:
            return self.direct_notifier.config()
        config = self.notifier.config()
        raw = ((self.store.get_setting(SETTING_PUSH_CATEGORIES) or "").strip())
        categories = raw if raw else ",".join(DEFAULT_PUSH_CATEGORIES)
        return {
            "url": config["url"],
            "secret": config["secret"],
            "push_categories": categories,
            "configured": bool(config["url"]),
            "worker_running": self.notifier._thread is not None,
        }

    def update_notify_config(self, payload: dict) -> dict:
        if self.settings.feishu_app_id:
            if set(payload) != {"enabled"} or type(payload["enabled"]) is not bool:
                raise ApiError(400, "validation_error", "请设置全局个人提醒开关")
            if payload["enabled"] and not self.feishu.configured:
                raise ApiError(503, "feishu_unavailable", "飞书应用尚未配置完整")
            self.direct_notifier.set_enabled(payload["enabled"])
            return self.notify_config()
        if "url" in payload:
            url = str(payload.get("url") or "").strip()
            if url and not (url.startswith("https://open.feishu.cn")
                            or url.startswith("https://open.larksuite.com")):
                raise ApiError(400, "validation_error",
                               "Webhook 地址应为飞书/Lark 开放平台机器人 URL"
                               "(https://open.feishu.cn/... 或 https://open.larksuite.com/...)")
            self.store.set_setting(SETTING_URL, url)
        if "secret" in payload:
            self.store.set_setting(SETTING_SECRET,
                                   str(payload.get("secret") or "").strip())
        if "push_categories" in payload:
            categories = payload["push_categories"]
            if not isinstance(categories, (list, str)):
                raise ApiError(400, "validation_error",
                               "push_categories 应为逗号分隔字符串或数组")
            if isinstance(categories, list):
                categories = ",".join(str(c) for c in categories)
            cleaned = tuple(c.strip() for c in str(categories).split(",")
                            if c.strip())
            invalid = [c for c in cleaned if c not in CATEGORIES]
            if invalid:
                raise ApiError(400, "validation_error",
                               f"无效分类: {', '.join(invalid)};可选: {', '.join(CATEGORIES)}")
            self.store.set_setting(SETTING_PUSH_CATEGORIES,
                                   ",".join(cleaned))
        return self.notify_config()

    def notify_test(self, viewer=None) -> dict:
        if self.settings.feishu_app_id:
            self.require_permission(viewer, "settings.manage")
            identity = self.store.identity_for_user(viewer.id)
            if not identity or identity["status"] != "approved":
                raise ApiError(400, "binding_required", "请先绑定自己的飞书身份")
            self._check_feishu_member(identity)
            import secrets
            from .feishu import FeishuError
            try:
                mid = self.feishu.send_text(identity["open_id"], "📮 Xynigo 云邮箱 · 私聊通知测试\n新邮件将按客户端邮箱授权发送给对应员工。", secrets.token_hex(16))
                return {"ok": True, "message_id": mid}
            except FeishuError as exc:
                return {"ok": False, "error": "飞书发送失败：" + exc.code}
        result = self.notifier.send_now(
            "📮 Xynigo 邮箱客户端 · 测试推送\n配置成功，首次识别的 P0/P1 待办将推送到本群。")
        if result["ok"]:
            logger.info("飞书测试推送成功")
        else:
            logger.warning("飞书测试推送失败: %s", result["error"])
        return result

    # ---------- 重新分类 ----------

    def reclassify(self) -> dict:
        """对已入库的全部邮件重跑业务分类、回款金额和亚马逊标记。

        规则更新或版本升级后使用,不重新拉信。
        """
        amazon_rules = load_amazon_rules(self.store.get_setting("amazon_rules"))
        category_rules = load_category_rules(self.store.get_setting("category_rules"))
        total = 0
        changed = 0
        for batch in self.store.iter_messages_for_reclassify():
            for row in batch:
                total += 1
                amazon = is_amazon_mail(row["from_addr"], row["subject"],
                                        amazon_rules)
                category, priority = classify(
                    row["subject"], row["from_addr"], row["preview"],
                    category_rules)
                amount = (extract_amount(row["subject"], row["body_text"] or "")
                          if category == "回款" else None)
                self.store.update_classification(row["id"], ClassifiedMail(
                    amazon, category, priority, amount))
                changed += 1
        logger.info("重新分类完成: %d/%d 封", changed, total)
        return {"total": total, "reclassified": changed}

    # ---------- 健康 ----------

    def health(self) -> dict:
        accounts = self.store.list_accounts()
        keys = self.store.list_api_keys()
        users = self.store.list_users()
        enabled = [a for a in accounts if a.enabled]
        degraded = any(a.last_sync_status == "auth_error" for a in enabled)
        return {
            "service": "store-mail-web",
            "name": "Xynigo 邮箱客户端",
            "version": __version__,
            "status": "degraded" if degraded else "ok",
            "started_at": self.started_at,
            "scheduler_running": self.engine.scheduler_running(),
            "accounts": {"total": len(accounts), "enabled": len(enabled)},
            "users": {"total": len(users),
                      "sub_accounts": sum(1 for u in users if not self.is_superadmin(u))},
            "messages": self.store.message_count(),
            "api_keys_active": sum(1 for k in keys if k.enabled),
        }
