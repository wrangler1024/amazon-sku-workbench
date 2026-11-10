"""店铺邮件信号与团队处理记录的 SQLite 存储。"""

from __future__ import annotations

import json
import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from .risk import Assessment


RISK_SCHEMA = """
CREATE TABLE IF NOT EXISTS risk_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    signature TEXT NOT NULL,
    event_type TEXT NOT NULL,
    subtype TEXT NOT NULL,
    severity TEXT NOT NULL,
    action_required INTEGER NOT NULL,
    basis TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    source_trust TEXT NOT NULL,
    entity_type TEXT,
    entity_ref TEXT,
    finance_state TEXT,
    bank_receipt_status TEXT,
    amount_text TEXT,
    deadline_text TEXT,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    owner_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    due_at TEXT,
    due_source TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_risk_events_account ON risk_events(account_id, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_risk_events_active ON risk_events(archived, status, severity, due_at);
CREATE INDEX IF NOT EXISTS idx_risk_events_signature ON risk_events(signature, archived, status);
CREATE TABLE IF NOT EXISTS risk_event_messages (
    event_id INTEGER NOT NULL REFERENCES risk_events(id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
    PRIMARY KEY(event_id, message_id)
);
CREATE TABLE IF NOT EXISTS risk_assessed_messages (
    message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    event_id INTEGER REFERENCES risk_events(id) ON DELETE SET NULL,
    assessed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS risk_event_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES risk_events(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_risk_activity_event ON risk_event_activity(event_id, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_EVENT_SELECT = """
SELECT e.*, a.display_name AS account_display_name,
       a.chinese_name AS account_chinese_name, a.email AS account_email,
       a.site AS site, a.enabled AS account_enabled,
       a.last_sync_status AS account_sync_status, a.last_success_at AS account_last_success_at,
       u.username AS owner_name,
       COALESCE(NULLIF(u.full_name, ''), u.username) AS owner_display_name,
       (SELECT COUNT(*) FROM risk_event_messages em WHERE em.event_id=e.id) AS message_count,
       (SELECT em.message_id FROM risk_event_messages em WHERE em.event_id=e.id
        ORDER BY em.message_id DESC LIMIT 1) AS latest_message_id
FROM risk_events e
JOIN accounts a ON a.id=e.account_id
LEFT JOIN users u ON u.id=e.owner_user_id
"""


class RiskStoreMixin:
    def risk_message_by_uid(self, account_id: int, uid: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id,subject,from_addr,body_text,is_amazon,category "
                "FROM messages WHERE account_id=? AND uid=?", (account_id, uid)
            ).fetchone()
        return dict(row) if row else None

    def risk_unassessed_messages(self, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.id, m.account_id, m.subject, m.from_addr, m.body_text, "
                "m.is_amazon, m.category, m.received_ts, m.received_at, "
                "m.fetched_at FROM messages m LEFT JOIN risk_assessed_messages r "
                "ON r.message_id=m.id WHERE r.message_id IS NULL "
                "ORDER BY m.id LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def risk_record_message(self, message_id: int,
                            assessment: Optional[Assessment], *,
                            historical: bool = False) -> Optional[int]:
        """一封邮件只评估一次；有相同实体的未结事件合并成一个处理项。"""
        now = _now()
        with self._lock:
            existing_assessment = self._conn.execute(
                "SELECT event_id FROM risk_assessed_messages WHERE message_id=?",
                (message_id,)).fetchone()
            if existing_assessment is not None:
                return existing_assessment["event_id"]
            mail = self._conn.execute(
                "SELECT id, account_id, subject, received_at, received_ts FROM messages "
                "WHERE id=?", (message_id,)).fetchone()
            if mail is None:
                return None
            event_id: Optional[int] = None
            if assessment is not None:
                dedupe_by_subject = not assessment.entity_ref and assessment.action_required
                if assessment.entity_ref:
                    signature = (f"{mail['account_id']}:{assessment.event_type}:"
                                 f"{assessment.subtype}:{assessment.entity_ref}")
                elif dedupe_by_subject:
                    # 无实体编号时只合并同店铺、同类型、同主题的近期重复通知。
                    normalized = re.sub(r"\s+", " ", mail["subject"].casefold()).strip()
                    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]
                    signature = (f"{mail['account_id']}:{assessment.event_type}:"
                                 f"{assessment.subtype}:subject:{digest}")
                else:
                    # 无转账编号的普通回款通知逐封保留，避免不同批次被误合并。
                    signature = f"message:{message_id}"
                window = ""
                args = [signature, int(historical)]
                if dedupe_by_subject:
                    received = datetime.fromtimestamp(mail["received_ts"], timezone.utc)
                    window = " AND last_seen_at BETWEEN ? AND ?"
                    args.extend(((received - timedelta(days=7)).isoformat(timespec="seconds"),
                                 (received + timedelta(days=7)).isoformat(timespec="seconds")))
                row = self._conn.execute(
                    "SELECT id FROM risk_events WHERE signature=? AND archived=? "
                    "AND status IN ('待处理','处理中','待核对')" + window +
                    " ORDER BY id DESC LIMIT 1", args).fetchone()
                if row:
                    event_id = row["id"]
                    self._conn.execute(
                        "UPDATE risk_events SET first_seen_at=MIN(first_seen_at, ?), "
                        "last_seen_at=MAX(last_seen_at, ?), updated_at=?, "
                        "version=version+1 WHERE id=?",
                        (mail["received_at"], mail["received_at"], now, event_id))
                else:
                    status = "待核对" if assessment.event_type == "review" else "待处理"
                    account = self._conn.execute(
                        "SELECT owner_user_id FROM accounts WHERE id=?",
                        (mail["account_id"],)).fetchone()
                    owner_id = account["owner_user_id"] if account else None
                    cur = self._conn.execute(
                        "INSERT INTO risk_events "
                        "(account_id, signature, event_type, subtype, severity, "
                        "action_required, basis, tags_json, source_trust, entity_type, "
                        "entity_ref, finance_state, bank_receipt_status, amount_text, deadline_text, title, status, owner_user_id, "
                        "due_at, due_source, first_seen_at, last_seen_at, archived, "
                        "created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (mail["account_id"], signature, assessment.event_type,
                         assessment.subtype, assessment.severity,
                         int(assessment.action_required), assessment.basis,
                         json.dumps(assessment.tags, ensure_ascii=False),
                         assessment.source_trust, assessment.entity_type,
                         assessment.entity_ref, assessment.finance_state,
                         "待核对到账" if assessment.finance_state == "平台已发起" else None,
                         assessment.amount_text,
                         assessment.deadline_text,
                         mail["subject"] or assessment.subtype, status, owner_id,
                         assessment.target_due_at(mail["received_ts"]),
                         "内部跟进目标" if assessment.action_required else None,
                         mail["received_at"], mail["received_at"],
                         int(historical), now, now))
                    event_id = int(cur.lastrowid)
                    self._conn.execute(
                        "INSERT INTO risk_event_activity "
                        "(event_id,user_id,action,detail,created_at) VALUES (?,NULL,?,?,?)",
                        (event_id, "邮件识别", assessment.basis, now))
                self._conn.execute(
                    "INSERT INTO risk_event_messages(event_id,message_id) VALUES (?,?)",
                    (event_id, message_id))
            self._conn.execute(
                "INSERT INTO risk_assessed_messages(message_id,event_id,assessed_at) "
                "VALUES (?,?,?)", (message_id, event_id, now))
            self._conn.commit()
            return event_id

    def risk_list_events(self, *, visible_account_ids: Optional[list] = None,
                         account_id: Optional[int] = None,
                         active_only: bool = False,
                         include_archived: bool = False,
                         limit: int = 1000) -> list[dict]:
        if visible_account_ids is not None and not visible_account_ids:
            return []
        conds, args = [], []
        if visible_account_ids is not None:
            conds.append("e.account_id IN (%s)" % ",".join("?" * len(visible_account_ids)))
            args.extend(visible_account_ids)
        if account_id is not None:
            conds.append("e.account_id=?")
            args.append(account_id)
        if active_only:
            conds.append("e.status IN ('待处理','处理中','待核对') AND e.action_required=1")
        if not include_archived:
            conds.append("e.archived=0")
        clause = " WHERE " + " AND ".join(conds) if conds else ""
        with self._lock:
            rows = self._conn.execute(
                _EVENT_SELECT + clause + " ORDER BY e.last_seen_at DESC, e.id DESC LIMIT ?",
                args + [limit]).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            item["tags"] = json.loads(item.pop("tags_json") or "[]")
            item["action_required"] = bool(item["action_required"])
            item["archived"] = bool(item["archived"])
        return result

    def risk_get_event(self, event_id: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(_EVENT_SELECT + " WHERE e.id=?", (event_id,)).fetchone()
            if row is None:
                return None
            messages = self._conn.execute(
                "SELECT m.id, m.subject, m.received_at, m.from_addr "
                "FROM risk_event_messages em JOIN messages m ON m.id=em.message_id "
                "WHERE em.event_id=? ORDER BY m.received_ts DESC, m.id DESC",
                (event_id,)).fetchall()
            activity = self._conn.execute(
                "SELECT h.id,h.user_id,h.action,h.detail,h.created_at,u.username,u.full_name "
                "FROM risk_event_activity h LEFT JOIN users u ON u.id=h.user_id "
                "WHERE h.event_id=? ORDER BY h.id DESC", (event_id,)).fetchall()
        item = dict(row)
        item["tags"] = json.loads(item.pop("tags_json") or "[]")
        item["action_required"] = bool(item["action_required"])
        item["archived"] = bool(item["archived"])
        item["messages"] = [dict(m) for m in messages]
        item["activity"] = [dict(a) for a in activity]
        return item

    def risk_update_event(self, event_id: int, *, user_id: int,
                          status: Optional[str] = None,
                          owner_user_id: Optional[int] = None,
                          set_owner: bool = False,
                          bank_receipt_status: Optional[str] = None,
                          note: Optional[str] = None,
                          expected_version: Optional[int] = None) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT status,owner_user_id,bank_receipt_status,version "
                "FROM risk_events WHERE id=?",
                (event_id,)).fetchone()
            if row is None:
                return None
            if expected_version is not None and row["version"] != expected_version:
                raise ValueError("事件已由其他人更新，请刷新后重试")
            sets, args = [], []
            changes = []
            if status is not None and status != row["status"]:
                sets.append("status=?")
                args.append(status)
                changes.append(f"状态 {row['status']} → {status}")
            if set_owner and owner_user_id != row["owner_user_id"]:
                sets.append("owner_user_id=?")
                args.append(owner_user_id)
                changes.append(f"负责人 {row['owner_user_id'] or '未分配'} → {owner_user_id or '未分配'}")
            if bank_receipt_status is not None and bank_receipt_status != row["bank_receipt_status"]:
                sets.append("bank_receipt_status=?")
                args.append(bank_receipt_status)
                changes.append(f"银行到账核对 {row['bank_receipt_status'] or '未记录'} → {bank_receipt_status}")
            now = _now()
            if sets:
                self._conn.execute(
                    f"UPDATE risk_events SET {', '.join(sets)}, version=version+1, "
                    "updated_at=? WHERE id=?", args + [now, event_id])
            if changes or note:
                self._conn.execute(
                    "INSERT INTO risk_event_activity "
                    "(event_id,user_id,action,detail,created_at) VALUES (?,?,?,?,?)",
                    (event_id, user_id, "处理更新" if changes else "备注",
                     "；".join(changes + ([note] if note else [])), now))
            self._conn.commit()
        return self.risk_get_event(event_id)
