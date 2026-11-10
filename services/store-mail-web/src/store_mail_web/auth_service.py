"""SSO admission and account binding; an OAuth identity alone is not a member."""
from __future__ import annotations
import hmac
import re
import secrets
import time
import logging
from urllib.parse import urlsplit

from .errors import ApiError
from .feishu import FeishuError
from .identity_store import fingerprint


def safe_return(value):
    # Only known SPA pages. Prevent external redirects, header injection and arbitrary callbacks.
    if isinstance(value, str) and re.fullmatch(r"/#/(?:dashboard|inbox|users|roles|settings|accounts|join|message/\d+|event/\d+|store/\d+)", value):
        return value
    return "/#/inbox"


class AuthServiceMixin:
    def require_superadmin(self, viewer):
        if not viewer or viewer.role != "admin" or viewer.username != self.settings.admin_user:
            raise ApiError(403, "forbidden", "仅超级管理员可处理加入申请")

    def require_pilot_role(self, role):
        if self.store.get_setting("supervisor_access_only") == "1" and role not in {"supervisor", "director", "admin"}:
            raise ApiError(403, "supervisor_access_only", "云邮箱前期面向主管、总监和管理员开放，请由主管接收并转告邮件")

    def _issue_session(self, user, identity_id=None):
        self.require_pilot_role(user.role)
        token = self.tokens.issue(user.username, uid=user.id, role=user.role)
        expires = self.tokens.verify(token)["exp"]
        self.store.save_web_session(token,user.id,identity_id,expires)
        self.store.set_user_last_login(user.id)
        return {"token":token,"user":self._user_view(user)}

    def _check_feishu_member(self, identity):
        if (not self.feishu.configured or identity["app_id"] != self.settings.feishu_app_id
                or identity["tenant_key"] != self.settings.feishu_tenant_key):
            raise ApiError(403,"identity_unavailable","飞书身份不属于当前应用")
        try:
            active = self.feishu.member_active(identity["open_id"])
        except FeishuError:
            raise ApiError(503,"identity_check_unavailable","暂时无法核验企业成员状态，请稍后重试") from None
        if not active:
            if identity["user_id"]:
                self.store.revoke_sessions(identity["user_id"])
            raise ApiError(403,"member_inactive","企业成员已停用或离职")

    def auth_config(self):
        return {"feishu_enabled":self.feishu.configured,"app_name":"Xynigo 云邮箱"}

    def start_feishu_auth(self, return_to, binding_token=None):
        if not self.feishu.configured:
            raise ApiError(503,"sso_unavailable","企业飞书登录尚未配置")
        parsed=urlsplit(self.settings.public_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.path not in ("", "/"):
            raise ApiError(503,"sso_unavailable","企业飞书回调地址配置无效")
        if binding_token:
            self.resolve_viewer(self.verify_session(binding_token))
        state,browser,verifier=(secrets.token_urlsafe(32) for _ in range(3))
        self.store.save_oauth_attempt(state,browser,verifier,safe_return(return_to),binding_token)
        return {"url":self.feishu.authorize_url(state,verifier),"browser":browser}

    def finish_feishu_auth(self, code, state, browser, binding_token=""):
        attempt=self.store.consume_oauth_attempt(state,browser)
        if not attempt:
            raise ApiError(400,"invalid_oauth_state","登录请求已失效，请重新发起飞书登录")
        try:
            profile=self.feishu.exchange(code,attempt["verifier"])
        except FeishuError as exc:
            logging.getLogger(__name__).warning("Feishu OAuth exchange failed: %s", exc.code)
            raise ApiError(403 if not exc.retryable else 503,"feishu_login_failed",
                           "飞书登录失败，请确认使用公司账号后重试") from None
        identity=self.store.touch_identity(self.settings.feishu_app_id,profile)
        self._check_feishu_member(identity)
        if attempt["binding_session_hash"]:
            if not hmac.compare_digest(attempt["binding_session_hash"],fingerprint(binding_token)):
                raise ApiError(403,"binding_session_changed","原账号会话已变更，请重新登录后绑定")
            viewer=self.resolve_viewer(self.verify_session(binding_token))
            try:
                identity=self.store.bind_identity(identity["id"],viewer.id,viewer.id)
            except ValueError as exc:
                raise ApiError(409,"binding_conflict",str(exc)) from None
        if identity["status"]=="approved" and identity["user_id"]:
            user=self.store.get_user(identity["user_id"])
            if not user or not user.enabled:
                raise ApiError(403,"account_disabled","账号已停用")
            result=self._issue_session(user,identity["id"])
            result["return_to"]=attempt["return_to"]
            return result
        if identity["status"]=="revoked":
            raise ApiError(403,"identity_revoked","该身份已停用，请联系超管")
        return {"applicant_token":self.store.applicant_token(identity["id"]),"return_to":"/#/join"}

    def applicant_view(self, token):
        identity=self.store.applicant_identity(token)
        if not identity:
            raise ApiError(401,"applicant_expired","申请会话已过期，请重新使用飞书登录")
        return identity

    @staticmethod
    def admission_view(identity):
        return {k:identity[k] for k in ("id","name","status","request_note","review_note","requested_at","reviewed_at")}

    def submit_admission(self, token, payload):
        identity=self.applicant_view(token)
        self._check_feishu_member(identity)
        note=str(payload.get("note") or "").strip()
        if len(note)>300:
            raise ApiError(400,"validation_error","申请说明最多 300 字")
        return self.admission_view(self.store.request_admission(identity["id"],note))

    def complete_admission(self, token):
        identity=self.applicant_view(token)
        if identity["status"]!="approved" or not identity["user_id"]:
            raise ApiError(403,"admission_required","请等待云邮箱管理员批准加入")
        self._check_feishu_member(identity)
        user=self.store.get_user(identity["user_id"])
        if not user or not user.enabled:
            raise ApiError(403,"account_disabled","账号已停用")
        return self._issue_session(user,identity["id"])

    def review_admission(self, iid, payload, viewer):
        self.require_permission(viewer, "admissions.approve")
        identity=self.store.identity(iid)
        if (not identity or identity["app_id"]!=self.settings.feishu_app_id
                or identity["tenant_key"]!=self.settings.feishu_tenant_key):
            raise ApiError(404,"not_found","申请不存在")
        decision=payload.get("decision")
        if decision not in ("approve","reject"):
            raise ApiError(400,"validation_error","请选择批准或拒绝")
        approved=decision=="approve"
        aids=self._clean_account_ids(payload.get("account_ids")) if approved else ()
        note=str(payload.get("note") or "").strip()[:300]
        uid=payload.get("existing_user_id")
        if uid is not None and (type(uid) is not int or uid<=0):
            raise ApiError(400,"validation_error","关联成员无效")
        existing = self.store.get_user(uid) if uid is not None else None
        if existing and self.is_superadmin(existing):
            raise ApiError(403, "protected_superadmin", "不能通过加入审批关联超级管理员")
        role = self.clean_role(payload.get("role", existing.role if existing else "supervisor"))
        if approved:
            self.require_pilot_role(role)
            self._check_feishu_member(identity)
        try:
            row=self.store.review_admission(iid,viewer.id,approved,aids,note,uid,role)
        except ValueError as exc:
            raise ApiError(409,"admission_conflict",str(exc)) from None
        return self.admission_view(row)
