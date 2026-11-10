"""SQLite 存储层:账号、邮件、用户、API Key、规则配置。

线程模型:单连接 + 全局锁(HTTP 多线程 + 后台同步线程共用)。
授权码只存 Fernet 密文,明文仅在同步时经解密短暂存在于内存。

多用户模型(v0.3):
- users.password_hash 为空串：env 超管或仅使用飞书登录的成员
- account_users 决定子账号可见哪些邮箱;admin 角色不受限
- message_reads 是"每用户已读"的唯一事实;messages.unread 仅表示
  邮箱端 IMAP \\Seen 的物理状态,不再代表任何用户的阅读进度
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .crypto import SecretBox
from .risk_store import RiskStoreMixin, RISK_SCHEMA
from .identity_store import IdentityStoreMixin, IDENTITY_SCHEMA
from .roles import RoleStoreMixin, seed_roles, normalize_role, ROLE_NAMES
from .models import (
    Account, ApiKey, FetchedMail, ClassifiedMail, IMPORTANT_CATEGORIES, User,
)
logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT NOT NULL,
    chinese_name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL UNIQUE,
    imap_host TEXT NOT NULL DEFAULT 'imap.163.com',
    imap_port INTEGER NOT NULL DEFAULT 993,
    auth_code_enc TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_uid INTEGER NOT NULL DEFAULT 0,
    last_sync_at TEXT,
    last_success_at TEXT,
    last_sync_status TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    uid INTEGER NOT NULL,
    message_id TEXT,
    subject TEXT NOT NULL DEFAULT '',
    from_name TEXT DEFAULT '',
    from_addr TEXT DEFAULT '',
    to_addr TEXT DEFAULT '',
    internal_date TEXT,
    received_ts INTEGER NOT NULL,
    received_at TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    unread INTEGER NOT NULL DEFAULT 1,
    seen_synced INTEGER NOT NULL DEFAULT 0,
    is_amazon INTEGER NOT NULL DEFAULT 0,
    category TEXT NOT NULL DEFAULT '其他',
    priority TEXT NOT NULL DEFAULT '低',
    otp_code TEXT,
    otp_confidence REAL,
    amount_text TEXT,
    preview TEXT DEFAULT '',
    body_text TEXT DEFAULT '',
    body_html TEXT,
    fetched_at TEXT NOT NULL,
    UNIQUE(account_id, uid)
);

CREATE INDEX IF NOT EXISTS idx_messages_account_ts ON messages(account_id, received_ts DESC);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(received_ts DESC);
CREATE INDEX IF NOT EXISTS idx_messages_otp ON messages(otp_confidence) WHERE otp_code IS NOT NULL;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'user',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS account_users (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, account_id)
);

CREATE TABLE IF NOT EXISTS message_reads (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    read_at TEXT NOT NULL,
    UNIQUE(user_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_message_reads_msg ON message_reads(message_id);

CREATE TABLE IF NOT EXISTS message_flags (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    flag_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, message_id, flag_type)
);

CREATE INDEX IF NOT EXISTS idx_message_flags_msg ON message_flags(message_id);

CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1,
    rate_limit_per_min INTEGER NOT NULL DEFAULT 60,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    request_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

# 旧库增量列:v0.2 起 messages 增加 amount_text(回款金额)
MIGRATION_COLUMNS = {
    "messages": {"amount_text": "TEXT"},
    "accounts": {"site": "TEXT NOT NULL DEFAULT ''", "owner_user_id": "INTEGER",
                 "last_success_at": "TEXT", "chinese_name": "TEXT NOT NULL DEFAULT ''"},
    "users": {"full_name": "TEXT NOT NULL DEFAULT ''"},
    "risk_events": {"amount_text": "TEXT", "deadline_text": "TEXT",
                    "bank_receipt_status": "TEXT"},
}


def _migrate(conn: sqlite3.Connection) -> None:
    """对已存在的旧表补齐新增列(ALTER TABLE ADD COLUMN,幂等)。"""
    for table, columns in MIGRATION_COLUMNS.items():
        existing = {row[1] for row in conn.execute(
            f"PRAGMA table_info({table})").fetchall()}
        for column, decl in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
                logger.info("迁移:为 %s 表新增列 %s", table, column)
    # 旧库仅在最后一次同步成功时才有可信的成功时间；失败账号不猜测历史。
    conn.execute("UPDATE accounts SET last_success_at=last_sync_at "
                 "WHERE last_success_at IS NULL AND last_sync_status='ok'")
    conn.execute("UPDATE risk_events SET bank_receipt_status='待核对到账' "
                 "WHERE finance_state='平台已发起' AND bank_receipt_status IS NULL")
    conn.commit()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_account(row: sqlite3.Row) -> Account:
    return Account(
        id=row["id"],
        display_name=row["display_name"],
        email=row["email"],
        imap_host=row["imap_host"],
        imap_port=row["imap_port"],
        enabled=bool(row["enabled"]),
        last_uid=row["last_uid"],
        last_sync_at=row["last_sync_at"],
        last_sync_status=row["last_sync_status"],
        last_error=row["last_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        site=row["site"],
        owner_user_id=row["owner_user_id"],
        last_success_at=row["last_success_at"],
        chinese_name=row["chinese_name"],
    )


def _row_to_api_key(row: sqlite3.Row) -> ApiKey:
    return ApiKey(
        id=row["id"],
        name=row["name"],
        key_prefix=row["key_prefix"],
        key_hash=row["key_hash"],
        enabled=bool(row["enabled"]),
        rate_limit_per_min=row["rate_limit_per_min"],
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
        request_count=row["request_count"],
    )


# 不含 unread 的列表列;unread 由查询按用户动态计算
_MESSAGE_LIST_BASE = (
    "messages.id, account_id, uid, subject, from_name, from_addr, "
    "internal_date, received_ts, received_at, size_bytes, is_amazon, "
    "category, priority, amount_text, preview, "
    "(body_html IS NOT NULL) AS has_html"
)

# 每用户已读判定(m.user_id 占位参数在列里,先于 WHERE 参数出现)
_UNREAD_EXPR = (
    "CASE WHEN EXISTS(SELECT 1 FROM message_reads r "
    "WHERE r.message_id = messages.id AND r.user_id = ?) "
    "THEN 0 ELSE 1 END AS unread, messages.unread AS imap_seen"
)


class Store(RoleStoreMixin, IdentityStoreMixin, RiskStoreMixin):
    """SQLite 存储。进程内单例。"""

    def __init__(self, db_path: Path, secret_box: SecretBox):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._box = secret_box
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.executescript(RISK_SCHEMA)
            _migrate(self._conn)
            self._conn.executescript(IDENTITY_SCHEMA)
            seed_roles(self._conn)
            self.configure_feishu("", "")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- 用户 ----------

    def ensure_admin_user(self, username: str) -> User:
        """保证 env 超管在 users 表有对应行(密码哈希为空=env 托管)。"""
        user = self.get_user_by_username(username)
        if user is not None:
            return user
        with self._lock:
            self._conn.execute(
                "INSERT INTO users (username, password_hash, role, enabled, created_at) "
                "VALUES (?, '', 'admin', 1, ?)",
                (username, now_iso()))
            self._conn.commit()
        return self.get_user_by_username(username)

    def _attach_account_ids(self, user: User) -> User:
        with self._lock:
            rows = self._conn.execute(
                "SELECT account_id FROM account_users WHERE user_id = ? "
                "ORDER BY account_id", (user.id,)).fetchall()
        return User(
            id=user.id, username=user.username, role=user.role,
            enabled=user.enabled, password_hash=user.password_hash,
            created_at=user.created_at, last_login_at=user.last_login_at,
            account_ids=tuple(r["account_id"] for r in rows),
            full_name=user.full_name,
        )

    def get_user(self, user_id: int) -> Optional[User]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._attach_account_ids(_row_to_user(row)) if row else None

    def get_user_by_username(self, username: str) -> Optional[User]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                (username,)).fetchone()
        return self._attach_account_ids(_row_to_user(row)) if row else None

    def list_users(self) -> list[User]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM users ORDER BY id").fetchall()
        return [self._attach_account_ids(_row_to_user(r)) for r in rows]

    def create_user(self, username: str, password_hash: str,
                    role: str = "operator", account_ids: tuple = (),
                    full_name: str = "") -> User:
        role = normalize_role(role)
        if role not in ROLE_NAMES:
            raise ValueError("无效成员角色")
        now = now_iso()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO users (username, full_name, password_hash, role, enabled, created_at) "
                "VALUES (?, ?, ?, ?, 1, ?)",
                (username, full_name, password_hash, role, now))
            user_id = cur.lastrowid
            for account_id in account_ids:
                self._conn.execute(
                    "INSERT OR IGNORE INTO account_users "
                    "(user_id, account_id, created_at) VALUES (?, ?, ?)",
                    (user_id, account_id, now))
            self._conn.commit()
        return self.get_user(user_id)

    def update_user(self, user_id: int, *, password_hash: Optional[str] = None,
                    enabled: Optional[bool] = None,
                    account_ids: Optional[tuple] = None,
                    full_name: Optional[str] = None, role: Optional[str] = None,
                    actor_id: Optional[int] = None) -> Optional[User]:
        with self._lock:
            before = self.get_user(user_id)
            if before is None:
                return None
            if role is not None:
                if role not in ROLE_NAMES:
                    raise ValueError("无效成员角色")
                self._conn.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
                if role != before.role:
                    self._conn.execute("DELETE FROM web_sessions WHERE user_id=?", (user_id,))
            if full_name is not None:
                self._conn.execute(
                    "UPDATE users SET full_name = ? WHERE id = ?", (full_name, user_id))
            if password_hash:
                self._conn.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (password_hash, user_id))
            if enabled is not None:
                self._conn.execute(
                    "UPDATE users SET enabled = ? WHERE id = ?",
                    (1 if enabled else 0, user_id))
                if not enabled:
                    self._conn.execute("DELETE FROM web_sessions WHERE user_id=?", (user_id,))
            if password_hash:
                self._conn.execute("DELETE FROM web_sessions WHERE user_id=?", (user_id,))
            if account_ids is not None:
                self._conn.execute(
                    "DELETE FROM account_users WHERE user_id = ?", (user_id,))
                now = now_iso()
                for account_id in account_ids:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO account_users "
                        "(user_id, account_id, created_at) VALUES (?, ?, ?)",
                        (user_id, account_id, now))
            if actor_id is not None:
                after = self.get_user(user_id)
                self.audit_access(actor_id, "user", user_id, "member_updated",
                    {"role": before.role, "enabled": before.enabled, "account_ids": list(before.account_ids)},
                    {"role": after.role, "enabled": after.enabled, "account_ids": list(after.account_ids)})
            self._conn.commit()
        return self.get_user(user_id)

    def delete_user(self, user_id: int) -> bool:
        with self._lock:
            self._conn.execute(
                "UPDATE accounts SET owner_user_id=NULL WHERE owner_user_id=?",
                (user_id,))
            cur = self._conn.execute(
                "DELETE FROM users WHERE id = ?", (user_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def set_user_last_login(self, user_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?",
                (now_iso(), user_id))
            self._conn.commit()

    # ---------- 账号 ----------

    def create_account(self, display_name: str, email: str, auth_code: str,
                       imap_host: str, imap_port: int, enabled: bool = True,
                       site: str = "", owner_user_id: Optional[int] = None,
                       chinese_name: str = "") -> Account:
        now = now_iso()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO accounts "
                "(display_name, chinese_name, email, imap_host, imap_port, auth_code_enc, enabled, "
                "created_at, updated_at, site, owner_user_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (display_name, chinese_name, email, imap_host, imap_port,
                 self._box.encrypt(auth_code), 1 if enabled else 0, now, now,
                 site, owner_user_id),
            )
            self._conn.commit()
            return self.get_account(cur.lastrowid)

    def update_account(self, account_id: int, *, display_name: Optional[str] = None,
                       chinese_name: Optional[str] = None,
                       set_chinese_name: bool = False,
                       email: Optional[str] = None, auth_code: Optional[str] = None,
                       imap_host: Optional[str] = None, imap_port: Optional[int] = None,
                       enabled: Optional[bool] = None,
                       site: Optional[str] = None,
                       owner_user_id: Optional[int] = None,
                       set_owner: bool = False) -> Optional[Account]:
        sets: list[str] = []
        args: list[Any] = []
        if display_name is not None:
            sets.append("display_name = ?")
            args.append(display_name)
        if set_chinese_name:
            sets.append("chinese_name = ?")
            args.append(chinese_name or "")
        if email is not None:
            sets.append("email = ?")
            args.append(email)
        if auth_code is not None and auth_code != "":
            sets.append("auth_code_enc = ?")
            args.append(self._box.encrypt(auth_code))
        if imap_host is not None:
            sets.append("imap_host = ?")
            args.append(imap_host)
        if imap_port is not None:
            sets.append("imap_port = ?")
            args.append(imap_port)
        if enabled is not None:
            sets.append("enabled = ?")
            args.append(1 if enabled else 0)
        if site is not None:
            sets.append("site = ?")
            args.append(site)
        if set_owner:
            sets.append("owner_user_id = ?")
            args.append(owner_user_id)
        if not sets:
            return self.get_account(account_id)
        sets.append("updated_at = ?")
        args.append(now_iso())
        args.append(account_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE accounts SET {', '.join(sets)} WHERE id = ?", args)
            self._conn.commit()
            return self.get_account(account_id)

    def delete_account(self, account_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM accounts WHERE id = ?", (account_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def get_account(self, account_id: int) -> Optional[Account]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
            return _row_to_account(row) if row else None

    def list_accounts(self, enabled_only: bool = False) -> list[Account]:
        sql = "SELECT * FROM accounts"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY id"
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
            return [_row_to_account(r) for r in rows]

    def find_account(self, ref: str) -> Optional[Account]:
        """按 id 数字、邮箱地址或店铺名精确匹配。"""
        ref = (ref or "").strip()
        if not ref:
            return None
        with self._lock:
            if ref.isdigit():
                row = self._conn.execute(
                    "SELECT * FROM accounts WHERE id = ?", (int(ref),)).fetchone()
                if row:
                    return _row_to_account(row)
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE email = ? COLLATE NOCASE", (ref,)).fetchone()
            if row:
                return _row_to_account(row)
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE display_name = ?", (ref,)).fetchone()
            return _row_to_account(row) if row else None

    def get_auth_code(self, account_id: int) -> str:
        """解密授权码。仅同步/测试连接时调用,不得进日志或 API 返回。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT auth_code_enc FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not row:
            raise ValueError(f"账号不存在: {account_id}")
        return self._box.decrypt(row["auth_code_enc"])

    def set_sync_state(self, account_id: int, status: str,
                       error: Optional[str], last_uid: Optional[int] = None) -> None:
        now = now_iso()
        with self._lock:
            self._conn.execute(
                "UPDATE accounts SET last_sync_at = ?, last_sync_status = ?, last_error = ?, "
                "last_success_at = CASE WHEN ?='ok' THEN ? ELSE last_success_at END, "
                + ("last_uid = ?, " if last_uid is not None else "")
                + "updated_at = ? WHERE id = ?",
                (
                    (now, status, error, status, now)
                    + ((last_uid,) if last_uid is not None else ())
                    + (now, account_id)
                ),
            )
            self._conn.commit()

    # ---------- 邮件 ----------

    _MESSAGE_UPSERT_COLUMNS = (
        "message_id = ?, subject = ?, from_name = ?, from_addr = ?, to_addr = ?, "
        "internal_date = ?, received_ts = ?, received_at = ?, size_bytes = ?, "
        "is_amazon = ?, category = ?, priority = ?, otp_code = ?, "
        "otp_confidence = ?, amount_text = ?, preview = ?, body_text = ?, "
        "body_html = ?, fetched_at = ?"
    )

    def upsert_message(self, account_id: int, mail: FetchedMail,
                       classified: ClassifiedMail) -> bool:
        """写入一封邮件。已存在时刷新内容但保留阅读状态。返回是否新插入。

        注:sqlite 对 ON CONFLICT DO UPDATE 的 rowcount 恒为 1,
        无法区分插入/更新,所以拆成 DO NOTHING + 按需 UPDATE。
        """
        now = now_iso()
        received_at = (
            mail.internal_date
            or datetime.fromtimestamp(mail.received_ts, tz=timezone.utc).isoformat(timespec="seconds")
        )
        values = (
            account_id, mail.uid, mail.message_id, mail.subject,
            mail.from_name, mail.from_addr, mail.to_addr,
            mail.internal_date, mail.received_ts, received_at,
            mail.size_bytes, 1 if mail.unread else 0,
            1 if classified.is_amazon else 0,
            classified.category, classified.priority,
            None, None,  # 历史列仅为兼容旧数据库，新邮件不保存验证码识别结果
            classified.amount, mail.preview, mail.body_text, mail.body_html, now,
        )
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages ("
                "account_id, uid, message_id, subject, from_name, from_addr, to_addr, "
                "internal_date, received_ts, received_at, size_bytes, unread, "
                "is_amazon, category, priority, otp_code, otp_confidence, "
                "amount_text, preview, body_text, body_html, fetched_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(account_id, uid) DO NOTHING",
                values,
            )
            inserted = cur.rowcount > 0
            if inserted:
                self._enqueue_mail_notifications(cur.lastrowid, account_id, mail.received_ts)
            if not inserted:
                # values 布局:0=account_id 1=uid 2..10=头部 11=unread(保留) 12..=其余
                set_values = values[2:11] + values[12:]
                self._conn.execute(
                    "UPDATE messages SET " + self._MESSAGE_UPSERT_COLUMNS +
                    " WHERE account_id = ? AND uid = ?",
                    set_values + (account_id, mail.uid),
                )
            self._conn.commit()
            return inserted

    @staticmethod
    def _like(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    def query_messages(self, *, account_id: Optional[int] = None,
                       visible_account_ids: Optional[list] = None,
                       user_id: Optional[int] = None,
                       flag_type: Optional[str] = None,
                       q: Optional[str] = None, category: Optional[str] = None,
                       amazon_only: bool = False,
                       finance_only: bool = False,
                       shipment_only: bool = False,
                       unread_only: bool = False, important_only: bool = False,
                       since_ts: Optional[int] = None,
                       until_ts: Optional[int] = None, limit: int = 50,
                       offset: int = 0) -> tuple[int, list[dict]]:
        """组合条件查询。返回 (总数, 消息行列表,不含正文)。

        user_id 非空时 unread 列为该用户视角并附带 flags;visible_account_ids
        非空时限定可见邮箱(空列表=什么都看不到);flag_type 为 "any" 或具体
        标记类型时按该用户的标记过滤。条件与参数成对构建,顺序天然正确。
        """
        if visible_account_ids is not None and not visible_account_ids:
            return 0, []
        conds: list[tuple[str, list]] = []
        if visible_account_ids is not None:
            conds.append(("account_id IN (%s)"
                          % ",".join("?" * len(visible_account_ids)),
                          list(visible_account_ids)))
        if account_id is not None:
            conds.append(("account_id = ?", [account_id]))
        if q:
            like = self._like(q)
            conds.append((
                "(subject LIKE ? ESCAPE '\\' OR from_addr LIKE ? ESCAPE '\\' "
                "OR from_name LIKE ? ESCAPE '\\' OR preview LIKE ? ESCAPE '\\')",
                [like] * 4))
        if category:
            conds.append(("category = ?", [category]))
        if important_only:
            conds.append(("category IN (%s)"
                          % ",".join("?" * len(IMPORTANT_CATEGORIES)),
                          list(IMPORTANT_CATEGORIES)))
        if amazon_only:
            conds.append(("is_amazon = 1", []))
        topic_filters = []
        if finance_only:
            topic_filters.append(
                "(category IN ('扣款','回款') OR EXISTS("
                "SELECT 1 FROM risk_event_messages em "
                "JOIN risk_events e ON e.id=em.event_id "
                "WHERE em.message_id=messages.id AND e.event_type='finance'))")
        if shipment_only:
            topic_filters.append(
                "(category='货件' OR EXISTS("
                "SELECT 1 FROM risk_event_messages em "
                "JOIN risk_events e ON e.id=em.event_id "
                "WHERE em.message_id=messages.id AND e.event_type='fba'))")
        if topic_filters:
            # 两个主题勾选项为并集，再与账号/关键词/其他条件取交集。
            conds.append(("(" + " OR ".join(topic_filters) + ")", []))
        if unread_only:
            if user_id is not None:
                conds.append((
                    "NOT EXISTS(SELECT 1 FROM message_reads r "
                    "WHERE r.message_id = messages.id AND r.user_id = ?)",
                    [user_id]))
            else:
                conds.append(("messages.unread = 1", []))
        if flag_type:
            if flag_type == "any":
                cond_sql = ("EXISTS(SELECT 1 FROM message_flags f "
                            "WHERE f.message_id = messages.id AND f.user_id = ?)")
                cond_args = [user_id] if user_id is not None else None
            else:
                cond_sql = ("EXISTS(SELECT 1 FROM message_flags f "
                            "WHERE f.message_id = messages.id AND f.user_id = ? "
                            "AND f.flag_type = ?)")
                cond_args = ([user_id, flag_type]
                             if user_id is not None else None)
            if cond_args is None:
                # 未带用户上下文的标记过滤视为空结果
                return 0, []
            conds.append((cond_sql, cond_args))
        if since_ts is not None:
            conds.append(("received_ts >= ?", [since_ts]))
        if until_ts is not None:
            conds.append(("received_ts <= ?", [until_ts]))
        clause = ""
        where_args: list[Any] = []
        if conds:
            clause = " WHERE " + " AND ".join(sql for sql, _ in conds)
            where_args = [p for _, params in conds for p in params]
        columns = _MESSAGE_LIST_BASE
        select_args = list(where_args)
        if user_id is not None:
            # 列里的 _UNREAD_EXPR 参数先于 WHERE 参数出现
            columns = columns + ", " + _UNREAD_EXPR
            select_args = [user_id] + where_args
        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM messages{clause}", where_args).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT {columns} FROM messages{clause} "
                "ORDER BY received_ts DESC, id DESC LIMIT ? OFFSET ?",
                select_args + [limit, offset],
            ).fetchall()
        return total, self._attach_flags(user_id, [dict(r) for r in rows])

    def get_message(self, message_id: int, with_body: bool = False,
                    user_id: Optional[int] = None) -> Optional[dict]:
        base = _MESSAGE_LIST_BASE if not with_body else (
            "messages.id, account_id, uid, message_id, subject, from_name, "
            "from_addr, to_addr, internal_date, received_ts, received_at, "
            "size_bytes, seen_synced, is_amazon, category, priority, "
            "amount_text, preview, body_text, body_html"
        )
        args: list[Any] = []
        columns = base
        if user_id is not None:
            columns = base + ", " + _UNREAD_EXPR
            args.append(user_id)
        args.append(message_id)
        with self._lock:
            row = self._conn.execute(
                f"SELECT {columns} FROM messages WHERE id = ?", args).fetchone()
        if row is None:
            return None
        return self._attach_flags(user_id, [dict(row)])[0]

    # ---------- 每用户已读 ----------

    def mark_user_read(self, user_id: int, message_id: int) -> bool:
        """标记已读(幂等)。返回是否新标记。"""
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO message_reads (user_id, message_id, read_at) "
                "VALUES (?, ?, ?)", (user_id, message_id, now_iso()))
            self._conn.commit()
            return cur.rowcount > 0

    def mark_user_unread(self, user_id: int, message_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM message_reads WHERE user_id = ? AND message_id = ?",
                (user_id, message_id))
            self._conn.commit()
            return cur.rowcount > 0

    def mark_all_read(self, user_id: int,
                      visible_account_ids: Optional[list] = None) -> int:
        """把可见范围内的全部邮件标为已读。返回新标记数。"""
        if visible_account_ids is not None and not visible_account_ids:
            return 0
        clause = ""
        args: list[Any] = [user_id, user_id]
        if visible_account_ids is not None:
            clause = " AND account_id IN (%s)" % ",".join(
                "?" * len(visible_account_ids))
            args.extend(visible_account_ids)
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO message_reads (user_id, message_id, read_at) "
                "SELECT ?, id, ? FROM messages WHERE 1=1" + clause, args)
            self._conn.commit()
            return cur.rowcount

    def set_message_seen_synced(self, message_id: int, seen_synced: bool) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE messages SET seen_synced = ? WHERE id = ?",
                (1 if seen_synced else 0, message_id))
            self._conn.commit()

    # ---------- 每用户标记(收藏/红旗/信标) ----------

    def set_message_flag(self, user_id: int, message_id: int,
                         flag_type: str, on: bool) -> bool:
        """设置/取消标记(幂等)。返回是否有变化。"""
        with self._lock:
            if on:
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO message_flags "
                    "(user_id, message_id, flag_type, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (user_id, message_id, flag_type, now_iso()))
            else:
                cur = self._conn.execute(
                    "DELETE FROM message_flags "
                    "WHERE user_id = ? AND message_id = ? AND flag_type = ?",
                    (user_id, message_id, flag_type))
            self._conn.commit()
            return cur.rowcount > 0

    def _attach_flags(self, user_id: Optional[int],
                      rows: list[dict]) -> list[dict]:
        """给消息行批量附加该用户的 flags 列表(一次查询)。"""
        if user_id is None or not rows:
            for row in rows:
                row.setdefault("flags", [])
            return rows
        ids = [row["id"] for row in rows]
        placeholders = ",".join("?" * len(ids))
        with self._lock:
            flag_rows = self._conn.execute(
                "SELECT message_id, flag_type FROM message_flags "
                f"WHERE user_id = ? AND message_id IN ({placeholders})",
                [user_id, *ids]).fetchall()
        by_message: dict[int, list] = {}
        for row in flag_rows:
            by_message.setdefault(row["message_id"], []).append(row["flag_type"])
        for row in rows:
            row["flags"] = sorted(by_message.get(row["id"], []))
        return rows

    def account_summaries(self, user_id: Optional[int] = None) -> dict[int, dict]:
        """每账号的邮件数/未读数/最新邮件时间,供账号列表页(未读按用户)。"""
        unread_expr = "messages.unread"
        args: list[Any] = []
        if user_id is not None:
            unread_expr = ("CASE WHEN EXISTS(SELECT 1 FROM message_reads r "
                           "WHERE r.message_id = messages.id AND r.user_id = ?) "
                           "THEN 0 ELSE 1 END")
            args.append(user_id)
        with self._lock:
            rows = self._conn.execute(
                "SELECT account_id, COUNT(*) AS total, "
                f"SUM({unread_expr}) AS unread, MAX(received_at) AS latest "
                "FROM messages GROUP BY account_id",
                args,
            ).fetchall()
        return {
            r["account_id"]: {
                "total": r["total"],
                "unread": int(r["unread"] or 0),
                "latest": r["latest"],
            }
            for r in rows
        }

    def message_count(self, visible_account_ids: Optional[list] = None) -> int:
        clause = ""
        args: list[Any] = []
        if visible_account_ids is not None:
            if not visible_account_ids:
                return 0
            clause = " WHERE account_id IN (%s)" % ",".join(
                "?" * len(visible_account_ids))
            args.extend(visible_account_ids)
        with self._lock:
            return self._conn.execute(
                f"SELECT COUNT(*) FROM messages{clause}", args).fetchone()[0]

    # ---------- 看板统计与重新分类 ----------

    def dashboard_stats(self, visible_account_ids: Optional[list] = None,
                        user_id: Optional[int] = None) -> dict[int, dict]:
        """每账号的绩效/回款监控统计,供店铺看板(未读按用户)。"""
        if visible_account_ids is not None and not visible_account_ids:
            return {}
        stats: dict[int, dict] = {}
        unread_expr = "messages.unread = 1"
        unread_args: list[Any] = []
        if user_id is not None:
            unread_expr = ("NOT EXISTS(SELECT 1 FROM message_reads r "
                           "WHERE r.message_id = messages.id AND r.user_id = ?)")
        with self._lock:
            accounts = self._conn.execute(
                "SELECT id FROM accounts" +
                (" WHERE id IN (%s)" % ",".join("?" * len(visible_account_ids))
                 if visible_account_ids is not None else "") +
                " ORDER BY id",
                visible_account_ids or [],
            ).fetchall()
            for row in accounts:
                account_id = row["id"]
                alert = self._conn.execute(
                    "SELECT MAX(received_at) FROM messages "
                    "WHERE account_id = ? AND category IN ('告警','侵权')",
                    (account_id,)).fetchone()[0]
                perf = self._conn.execute(
                    "SELECT MAX(received_at) FROM messages "
                    "WHERE account_id = ? AND category = '绩效'",
                    (account_id,)).fetchone()[0]
                debit = self._conn.execute(
                    "SELECT received_at, subject FROM messages "
                    "WHERE account_id = ? AND category = '扣款' "
                    "ORDER BY received_ts DESC, id DESC LIMIT 1",
                    (account_id,)).fetchone()
                payout = self._conn.execute(
                    "SELECT received_at, amount_text, subject FROM messages "
                    "WHERE account_id = ? AND category = '回款' "
                    "ORDER BY received_ts DESC, id DESC LIMIT 1",
                    (account_id,)).fetchone()
                if user_id is not None:
                    unread_args = [user_id, account_id, *IMPORTANT_CATEGORIES]
                else:
                    unread_args = [account_id, *IMPORTANT_CATEGORIES]
                unread = self._conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE account_id = ? "
                    f"AND {unread_expr} AND category IN (%s)"
                    % ",".join("?" * len(IMPORTANT_CATEGORIES)),
                    unread_args).fetchone()[0]
                stats[account_id] = {
                    "last_alert_at": alert,
                    "last_perf_at": perf,
                    "last_debit_at": debit["received_at"] if debit else None,
                    "last_debit_subject": debit["subject"] if debit else None,
                    "last_payout_at": payout["received_at"] if payout else None,
                    "last_payout_amount": payout["amount_text"] if payout else None,
                    "last_payout_subject": payout["subject"] if payout else None,
                    "important_unread": int(unread or 0),
                }
        return stats

    def iter_messages_for_reclassify(self, batch_size: int = 200):
        """按批次产出全部消息的分类输入(id + 识别所需字段)。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, subject, from_name, from_addr, preview, body_text "
                "FROM messages ORDER BY id").fetchall()
        for start in range(0, len(rows), batch_size):
            yield [dict(r) for r in rows[start:start + batch_size]]

    def update_classification(self, message_id: int,
                              classified: ClassifiedMail) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE messages SET is_amazon = ?, category = ?, priority = ?, "
                "otp_code = ?, otp_confidence = ?, amount_text = ? WHERE id = ?",
                (1 if classified.is_amazon else 0, classified.category,
                 classified.priority, None, None, classified.amount, message_id))
            self._conn.commit()

    # ---------- API Key ----------

    def create_api_key(self, name: str, key_prefix: str, key_hash: str,
                       rate_limit_per_min: int) -> ApiKey:
        now = now_iso()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO api_keys (name, key_prefix, key_hash, rate_limit_per_min, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, key_prefix, key_hash, rate_limit_per_min, now),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM api_keys WHERE id = ?", (cur.lastrowid,)).fetchone()
            return _row_to_api_key(row)

    def list_api_keys(self) -> list[ApiKey]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM api_keys ORDER BY id").fetchall()
            return [_row_to_api_key(r) for r in rows]

    def get_api_key(self, key_id: int) -> Optional[ApiKey]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()
            return _row_to_api_key(row) if row else None

    def find_api_key_by_hash(self, key_hash: str) -> Optional[ApiKey]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM api_keys WHERE key_hash = ?", (key_hash,)).fetchone()
            return _row_to_api_key(row) if row else None

    def set_api_key_enabled(self, key_id: int, enabled: bool) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE api_keys SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, key_id))
            self._conn.commit()
            return cur.rowcount > 0

    def delete_api_key(self, key_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM api_keys WHERE id = ?", (key_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def record_api_key_use(self, key_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE api_keys SET last_used_at = ?, request_count = request_count + 1 "
                "WHERE id = ?", (now_iso(), key_id))
            self._conn.commit()

    # ---------- 规则配置 ----------

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        username=row["username"],
        role=row["role"],
        enabled=bool(row["enabled"]),
        password_hash=row["password_hash"],
        created_at=row["created_at"],
        last_login_at=row["last_login_at"],
        full_name=row["full_name"],
    )
