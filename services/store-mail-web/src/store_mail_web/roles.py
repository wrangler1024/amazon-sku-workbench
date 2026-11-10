"""Role-based function permissions, independent from per-mailbox data grants."""
from __future__ import annotations

import json
import time
from .errors import ApiError

ROLE_NAMES = {"operator": "运营专员", "supervisor": "运营主管", "director": "运营总监", "admin": "管理员"}
PERMISSIONS = {
    "mail.read": ("邮件查看与个人标记", "查看原邮件、搜索、已读和收藏", False),
    "mail.sync": ("手动同步邮箱", "运营角色仅可同步已授权邮箱", False),
    "notifications.receive": ("个人收件提醒", "接收已授权邮箱的新邮件飞书私聊提醒", False),
    "dashboard.read": ("店铺看板与事件查看", "查看授权邮箱的风险、资金和处理轨迹", False),
    "events.update": ("待办处理与备注", "更新处理状态、填写备注和认领待办", False),
    "events.assign": ("待办改派", "在授权邮箱内调整事件负责人", False),
    "finance.confirm": ("银行到账核对", "记录到账核对结果及依据", False),
    "accounts.manage": ("邮箱账号管理", "新增、修改、启停、删除和测试邮箱", True),
    "members.manage": ("成员角色与邮箱授权", "管理成员角色、启停和邮箱授权", True),
    "admissions.approve": ("加入申请审批", "批准或拒绝申请，关联成员并分配角色和邮箱", True),
    "roles.manage": ("角色功能权限设置", "配置四类角色的功能权限", True),
    "settings.manage": ("通知与识别规则设置", "配置全局个人提醒、识别规则和重新分类", True),
    "api_keys.manage": ("Agent 接口管理", "管理可访问全量邮箱的 Agent API Key", True),
}
CORE_ADMIN = {"members.manage", "admissions.approve", "roles.manage"}
DEPENDENCIES = {
    "mail.sync": {"mail.read"}, "notifications.receive": {"mail.read"}, "dashboard.read": {"mail.read"},
    "events.update": {"dashboard.read"}, "events.assign": {"events.update"},
    "finance.confirm": {"events.update"},
}
_BASIC = {"mail.read", "mail.sync", "notifications.receive", "dashboard.read", "events.update"}
DEFAULT_PERMISSIONS = {
    "operator": _BASIC,
    "supervisor": _BASIC | {"events.assign"},
    "director": _BASIC | {"events.assign", "finance.confirm"},
    "admin": set(PERMISSIONS),
}
ROLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS role_definitions (
 role TEXT PRIMARY KEY, permissions_json TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 updated_by INTEGER, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS access_audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id INTEGER, target_type TEXT NOT NULL,
 target_id TEXT NOT NULL, action TEXT NOT NULL, before_json TEXT NOT NULL,
 after_json TEXT NOT NULL, created_at REAL NOT NULL
);
"""


def normalize_role(role):
    return "operator" if role == "user" else role


def seed_roles(conn):
    conn.executescript(ROLE_SCHEMA)
    for role, permissions in DEFAULT_PERMISSIONS.items():
        conn.execute("INSERT OR IGNORE INTO role_definitions(role,permissions_json,updated_at) VALUES (?,?,?)",
                     (role,json.dumps(sorted(permissions)),time.time()))
    conn.execute("UPDATE users SET role='operator' WHERE role='user'")


class RoleStoreMixin:
    def configure_superadmin(self, user_id):
        self._superadmin_id = user_id

    def recipient_permissions(self, user_id, role):
        if self.get_setting("supervisor_access_only") == "1" and role not in {"supervisor", "director", "admin"}:
            return set()
        if user_id == getattr(self, "_superadmin_id", None):
            return set(PERMISSIONS)
        return self.role_permissions(role)

    def role_permissions(self, role):
        role=normalize_role(role)
        with self._lock:
            row=self._conn.execute("SELECT permissions_json FROM role_definitions WHERE role=?",(role,)).fetchone()
        return set(json.loads(row[0])) if row else set()

    def role_definitions(self):
        with self._lock:
            rows={r['role']:dict(r) for r in self._conn.execute('SELECT * FROM role_definitions')}
        return [{"role":r,"name":name,"permissions":json.loads(rows[r]['permissions_json']),
                 "version":rows[r]['version'],"updated_at":rows[r]['updated_at']} for r,name in ROLE_NAMES.items()]

    def audit_access(self, actor_id, target_type, target_id, action, before, after):
        self._conn.execute("INSERT INTO access_audit(actor_id,target_type,target_id,action,before_json,after_json,created_at) "
                           "VALUES (?,?,?,?,?,?,?)",(actor_id,target_type,str(target_id),action,
                           json.dumps(before,ensure_ascii=False,sort_keys=True),json.dumps(after,ensure_ascii=False,sort_keys=True),time.time()))

    def save_role_permissions(self, role, permissions, actor_id, version):
        with self._lock:
            before=self.role_permissions(role)
            cur=self._conn.execute("UPDATE role_definitions SET permissions_json=?,version=version+1,updated_by=?,updated_at=? "
                                   "WHERE role=? AND version=?",(json.dumps(sorted(permissions)),actor_id,time.time(),role,version))
            if not cur.rowcount:
                raise ValueError('角色配置已更新，请刷新后重试')
            self.audit_access(actor_id,'role',role,'permissions_changed',sorted(before),sorted(permissions))
            self._conn.commit()


class RoleServiceMixin:
    def is_superadmin(self, viewer):
        return bool(viewer and viewer.role=='admin' and viewer.username==self.settings.admin_user)

    def permissions_for(self, viewer):
        if self.is_superadmin(viewer):
            return set(PERMISSIONS)
        # Re-read definitions for every request; never trust stale session permission claims.
        return self.store.role_permissions(viewer.role) if viewer and viewer.enabled else set()

    def has_permission(self, viewer, permission):
        return viewer is None or permission in self.permissions_for(viewer)

    def require_permission(self, viewer, permission):
        if not self.has_permission(viewer,permission):
            raise ApiError(403,'function_forbidden','当前角色无权使用：'+PERMISSIONS[permission][0])

    def clean_role(self, value):
        if value not in ROLE_NAMES:
            raise ApiError(400,'validation_error','请选择运营专员、运营主管、运营总监或管理员')
        return value

    def roles_view(self, viewer):
        self.require_permission(viewer,'roles.manage')
        return {"roles":self.store.role_definitions(),
                "permissions":[{"key":k,"name":v[0],"description":v[1],"admin_only":v[2],
                                "locked_admin":k in CORE_ADMIN,"requires":sorted(DEPENDENCIES.get(k,set()))}
                               for k,v in PERMISSIONS.items()]}

    def update_role(self, role, payload, viewer):
        self.require_permission(viewer,'roles.manage')
        self.clean_role(role)
        permissions=payload.get('permissions')
        if not isinstance(permissions,list) or any(not isinstance(p,str) or p not in PERMISSIONS for p in permissions):
            raise ApiError(400,'validation_error','功能权限列表无效')
        selected=set(permissions)
        if role!='admin' and any(PERMISSIONS[p][2] for p in selected):
            raise ApiError(400,'validation_error','全局管理功能仅能授予管理员角色')
        if role=='admin' and not CORE_ADMIN.issubset(selected):
            raise ApiError(400,'validation_error','管理员必须保留成员管理、加入审批和角色设置权限')
        for key in selected:
            if not DEPENDENCIES.get(key,set()).issubset(selected):
                raise ApiError(400,'validation_error',PERMISSIONS[key][0]+'缺少前置功能权限')
        version=payload.get('version')
        if type(version) is not int or version<1:
            raise ApiError(400,'validation_error','缺少有效的角色配置版本')
        try:
            self.store.save_role_permissions(role,selected,viewer.id,version)
        except ValueError as exc:
            raise ApiError(409,'role_conflict',str(exc)) from None
        return self.roles_view(viewer)
