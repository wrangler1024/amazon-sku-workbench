"""Persistent identities, admission, sessions and transactional notification outbox."""
from __future__ import annotations

import hashlib
import secrets
import time


def fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest()


IDENTITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS feishu_identities (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 app_id TEXT NOT NULL, tenant_key TEXT NOT NULL, open_id TEXT NOT NULL,
 name TEXT NOT NULL, user_id INTEGER UNIQUE REFERENCES users(id) ON DELETE SET NULL,
 status TEXT NOT NULL DEFAULT 'new', request_note TEXT NOT NULL DEFAULT '',
 requested_at REAL, reviewed_at REAL, reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
 review_note TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
 UNIQUE(app_id, tenant_key, open_id)
);
CREATE TABLE IF NOT EXISTS oauth_attempts (
 state_hash TEXT PRIMARY KEY, browser_hash TEXT NOT NULL, verifier_enc TEXT NOT NULL,
 return_to TEXT NOT NULL, binding_session_hash TEXT, expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS applicant_sessions (
 token_hash TEXT PRIMARY KEY, identity_id INTEGER NOT NULL REFERENCES feishu_identities(id) ON DELETE CASCADE,
 expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS web_sessions (
 token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 identity_id INTEGER REFERENCES feishu_identities(id) ON DELETE CASCADE,
 expires_at REAL NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS admission_audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, identity_id INTEGER NOT NULL,
 actor_id INTEGER, action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS notification_outbox (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 identity_id INTEGER REFERENCES feishu_identities(id) ON DELETE SET NULL,
 app_id TEXT NOT NULL, tenant_key TEXT NOT NULL, open_id TEXT NOT NULL DEFAULT '',
 uuid TEXT NOT NULL UNIQUE, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt REAL NOT NULL, lease_until REAL, first_attempt REAL,
 feishu_message_id TEXT, last_error TEXT, created_at REAL NOT NULL, sent_at REAL,
 UNIQUE(message_id, user_id, app_id)
);
CREATE INDEX IF NOT EXISTS idx_notify_due ON notification_outbox(status,next_attempt);
"""


class IdentityStoreMixin:
    def configure_feishu(self, app_id, tenant_key):
        self._feishu_app_id = app_id
        self._feishu_tenant_key = tenant_key

    def save_oauth_attempt(self, state, browser, verifier, return_to, binding_token=None):
        with self._lock:
            self._conn.execute("DELETE FROM oauth_attempts WHERE expires_at<?", (time.time(),))
            self._conn.execute("INSERT INTO oauth_attempts VALUES (?,?,?,?,?,?)", (
                fingerprint(state), fingerprint(browser), self._box.encrypt(verifier), return_to,
                fingerprint(binding_token) if binding_token else None, time.time()+300))
            self._conn.commit()

    def consume_oauth_attempt(self, state, browser):
        with self._lock:
            row = self._conn.execute("SELECT * FROM oauth_attempts WHERE state_hash=? AND browser_hash=? AND expires_at>?",
                                     (fingerprint(state), fingerprint(browser), time.time())).fetchone()
            if not row:
                return None
            self._conn.execute("DELETE FROM oauth_attempts WHERE state_hash=?", (fingerprint(state),))
            self._conn.commit()
            result = dict(row)
            result["verifier"] = self._box.decrypt(result.pop("verifier_enc"))
            return result

    def touch_identity(self, app_id, profile):
        with self._lock:
            self._conn.execute("INSERT INTO feishu_identities (app_id,tenant_key,open_id,name,created_at) "
                               "VALUES (?,?,?,?,?) ON CONFLICT(app_id,tenant_key,open_id) DO UPDATE SET name=excluded.name",
                               (app_id, profile["tenant_key"], profile["open_id"], profile["name"], time.time()))
            self._conn.commit()
            return dict(self._conn.execute("SELECT * FROM feishu_identities WHERE app_id=? AND tenant_key=? AND open_id=?",
                                           (app_id, profile["tenant_key"], profile["open_id"])).fetchone())

    def identity(self, identity_id):
        with self._lock:
            row = self._conn.execute("SELECT * FROM feishu_identities WHERE id=?", (identity_id,)).fetchone()
            return dict(row) if row else None

    def identity_for_user(self, user_id):
        with self._lock:
            row = self._conn.execute("SELECT * FROM feishu_identities WHERE user_id=? AND app_id=? AND tenant_key=?",
                                     (user_id, self._feishu_app_id, self._feishu_tenant_key)).fetchone()
            return dict(row) if row else None

    def applicant_token(self, identity_id):
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._conn.execute("DELETE FROM applicant_sessions WHERE expires_at<?", (time.time(),))
            self._conn.execute("INSERT INTO applicant_sessions VALUES (?,?,?)", (fingerprint(token), identity_id, time.time()+3600))
            self._conn.commit()
        return token

    def applicant_identity(self, token):
        with self._lock:
            row = self._conn.execute("SELECT i.* FROM applicant_sessions s JOIN feishu_identities i ON i.id=s.identity_id "
                                     "WHERE s.token_hash=? AND s.expires_at>? AND i.app_id=? AND i.tenant_key=?",
                                     (fingerprint(token), time.time(), self._feishu_app_id, self._feishu_tenant_key)).fetchone()
            return dict(row) if row else None

    def request_admission(self, identity_id, note):
        with self._lock:
            cur = self._conn.execute("UPDATE feishu_identities SET status='pending', request_note=?, requested_at=?, "
                                     "review_note='' WHERE id=? AND user_id IS NULL AND status IN ('new','rejected')",
                                     (note, time.time(), identity_id))
            if cur.rowcount:
                self._audit(identity_id, None, "requested", note)
            self._conn.commit()
        return self.identity(identity_id)

    def list_admissions(self):
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT id,name,status,request_note,requested_at,review_note,reviewed_at,user_id "
                "FROM feishu_identities WHERE app_id=? AND tenant_key=? AND status IN ('pending','rejected') "
                "ORDER BY requested_at DESC LIMIT 200", (self._feishu_app_id, self._feishu_tenant_key))]

    def _audit(self, identity_id, actor_id, action, detail=""):
        self._conn.execute("INSERT INTO admission_audit(identity_id,actor_id,action,detail,created_at) VALUES (?,?,?,?,?)",
                           (identity_id, actor_id, action, detail, time.time()))

    def bind_identity(self, identity_id, user_id, actor_id):
        with self._lock:
            current = self.identity(identity_id)
            other = self._conn.execute("SELECT id FROM feishu_identities WHERE user_id=?", (user_id,)).fetchone()
            if (not current or current["user_id"] not in (None, user_id)
                    or (other and other["id"] != identity_id) or current["status"] == "revoked"):
                raise ValueError("该飞书身份或本地账号已绑定，请由超管处理")
            self._conn.execute("UPDATE feishu_identities SET user_id=?,status='approved',reviewed_at=?,reviewed_by=? WHERE id=?",
                               (user_id, time.time(), actor_id, identity_id))
            self._audit(identity_id, actor_id, "bound", str(user_id))
            self._conn.commit()
        return self.identity(identity_id)

    def review_admission(self, identity_id, reviewer_id, approved, account_ids, note, existing_user_id=None, role="operator"):
        with self._lock:
            row = self.identity(identity_id)
            if not row or row["status"] != "pending" or row["user_id"] is not None:
                raise ValueError("申请已被处理，请刷新")
            try:
                if approved:
                    if existing_user_id is not None:
                        user = self.get_user(existing_user_id)
                        if not user or not user.enabled:
                            raise ValueError("仅可关联已启用的成员")
                        if self._conn.execute("SELECT 1 FROM feishu_identities WHERE user_id=?", (user.id,)).fetchone():
                            raise ValueError("该成员已绑定飞书")
                        uid = user.id
                        self._conn.execute("UPDATE users SET role=? WHERE id=?", (role,uid))
                        self._conn.execute("DELETE FROM web_sessions WHERE user_id=?", (uid,))
                        # Explicit administrator review replaces the selected member's grants.
                        self._conn.execute("DELETE FROM account_users WHERE user_id=?", (uid,))
                    else:
                        cur = self._conn.execute("INSERT INTO users(username,full_name,password_hash,role,enabled,created_at) "
                                                 "VALUES (?,?,'',?,1,datetime('now'))", ("fs_"+str(identity_id), row["name"], role))
                        uid = cur.lastrowid
                    for aid in account_ids:
                        self._conn.execute("INSERT INTO account_users(user_id,account_id,created_at) VALUES (?,?,datetime('now'))", (uid,aid))
                    self._conn.execute("UPDATE feishu_identities SET user_id=?,status='approved',reviewed_at=?,reviewed_by=?,review_note=? WHERE id=?",
                                       (uid,time.time(),reviewer_id,note,identity_id))
                else:
                    self._conn.execute("UPDATE feishu_identities SET status='rejected',reviewed_at=?,reviewed_by=?,review_note=? WHERE id=?",
                                       (time.time(),reviewer_id,note,identity_id))
                self._audit(identity_id,reviewer_id,"approved" if approved else "rejected",note)
                if approved:
                    self.audit_access(reviewer_id, "user", uid, "admission_approved", {},
                                      {"role": role, "account_ids": list(account_ids), "identity_id": identity_id})
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self.identity(identity_id)

    def save_web_session(self, token, user_id, identity_id, expires_at):
        with self._lock:
            self._conn.execute("DELETE FROM web_sessions WHERE expires_at<?", (time.time(),))
            self._conn.execute("INSERT INTO web_sessions VALUES (?,?,?,?,?)",
                               (fingerprint(token), user_id, identity_id, expires_at, time.time()))
            self._conn.commit()

    def web_session(self, token=None, token_hash=None):
        with self._lock:
            row = self._conn.execute("SELECT * FROM web_sessions WHERE token_hash=? AND expires_at>?",
                                     (token_hash or fingerprint(token or ""),time.time())).fetchone()
            return dict(row) if row else None

    def revoke_sessions(self, user_id):
        with self._lock:
            self._conn.execute("DELETE FROM web_sessions WHERE user_id=?", (user_id,))
            self._conn.commit()

    def logout_session(self, token, applicant_token=""):
        with self._lock:
            self._conn.execute("DELETE FROM web_sessions WHERE token_hash=?", (fingerprint(token),))
            self._conn.execute("DELETE FROM applicant_sessions WHERE token_hash=?", (fingerprint(applicant_token),))
            self._conn.commit()

    def _enqueue_mail_notifications(self, message_id, account_id, received_ts):
        """Called inside message INSERT transaction. Never backfills on bind/restart."""
        app_id = getattr(self, "_feishu_app_id", "")
        tenant = getattr(self, "_feishu_tenant_key", "")
        cfg = dict(self._conn.execute("SELECT key,value FROM settings WHERE key IN ('feishu_dm_enabled','feishu_dm_epoch')"))
        if not app_id or cfg.get("feishu_dm_enabled") != "1" or received_ts < float(cfg.get("feishu_dm_epoch") or time.time()):
            return
        account = self._conn.execute("SELECT enabled FROM accounts WHERE id=?", (account_id,)).fetchone()
        if not account or not account["enabled"]:
            return
        rows = self._conn.execute("SELECT u.id AS user_id,u.role,i.id AS identity_id,i.open_id,i.status FROM account_users a "
                                  "JOIN users u ON u.id=a.user_id LEFT JOIN feishu_identities i "
                                  "ON i.user_id=u.id AND i.app_id=? AND i.tenant_key=? "
                                  "WHERE a.account_id=? AND u.enabled=1", (app_id,tenant,account_id)).fetchall()
        for r in rows:
            if "notifications.receive" not in self.recipient_permissions(r["user_id"], r["role"]):
                continue
            ready = r["identity_id"] and r["status"] == "approved"
            self._conn.execute("INSERT OR IGNORE INTO notification_outbox "
                               "(message_id,user_id,identity_id,app_id,tenant_key,open_id,uuid,status,next_attempt,last_error,created_at) "
                               "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (message_id,r["user_id"],r["identity_id"],app_id,tenant,r["open_id"] or "",
                               fingerprint(f'{app_id}:{message_id}:{r["user_id"]}')[:48],"pending" if ready else "blocked",
                               time.time(),None if ready else "尚未绑定有效飞书身份",time.time()))

    def claim_notification(self):
        with self._lock:
            now = time.time()
            row = self._conn.execute("SELECT * FROM notification_outbox WHERE "
                                     "(status IN ('pending','retry') AND next_attempt<=?) OR (status='sending' AND lease_until<?) "
                                     "ORDER BY id LIMIT 1",(now,now)).fetchone()
            if not row:
                return None
            self._conn.execute("UPDATE notification_outbox SET status='sending',lease_until=?,attempts=attempts+1,"
                               "first_attempt=COALESCE(first_attempt,?) WHERE id=?",(now+120,now,row["id"]))
            self._conn.commit()
            item = dict(self._conn.execute("SELECT * FROM notification_outbox WHERE id=?",(row["id"],)).fetchone())
            return item

    def notification_context(self, item):
        with self._lock:
            if self.get_setting("feishu_dm_enabled") != "1":
                return None
            row = self._conn.execute("SELECT m.*,a.display_name,a.chinese_name,a.email,a.site,u.role AS recipient_role FROM messages m "
                "JOIN accounts a ON a.id=m.account_id JOIN account_users g ON g.account_id=a.id "
                "JOIN users u ON u.id=g.user_id JOIN feishu_identities i ON i.user_id=u.id "
                "WHERE m.id=? AND u.id=? AND i.id=? AND i.open_id=? AND i.app_id=? AND i.tenant_key=? "
                "AND a.enabled=1 AND u.enabled=1 AND i.status='approved'",
                (item["message_id"],item["user_id"],item["identity_id"],item["open_id"],
                 self._feishu_app_id,self._feishu_tenant_key)).fetchone()
            if row and "notifications.receive" not in self.recipient_permissions(item["user_id"], row["recipient_role"]):
                return None
            return dict(row) if row else None

    def finish_notification(self, oid, status, error=None, message_id=None, delay=0):
        with self._lock:
            self._conn.execute("UPDATE notification_outbox SET status=?,last_error=?,feishu_message_id=?,next_attempt=?,"
                               "lease_until=NULL,sent_at=? WHERE id=?",(status,error,message_id,time.time()+delay,
                               time.time() if status=="sent" else None,oid))
            self._conn.commit()

    def notification_status(self):
        with self._lock:
            counts = dict(self._conn.execute("SELECT status,count(*) FROM notification_outbox GROUP BY status"))
            items = [dict(r) for r in self._conn.execute("SELECT o.id,o.message_id,o.status,o.attempts,o.last_error,o.sent_at,"
                "o.created_at,u.full_name,u.username,a.display_name FROM notification_outbox o "
                "JOIN users u ON u.id=o.user_id JOIN messages m ON m.id=o.message_id JOIN accounts a ON a.id=m.account_id "
                "ORDER BY o.id DESC LIMIT 50")]
            unbound = [dict(r) for r in self._conn.execute("SELECT DISTINCT u.id,u.full_name,u.username FROM users u "
                "JOIN account_users a ON a.user_id=u.id LEFT JOIN feishu_identities i ON i.user_id=u.id AND i.app_id=? AND i.tenant_key=? "
                "WHERE u.enabled=1 AND (i.id IS NULL OR i.status<>'approved')",(self._feishu_app_id,self._feishu_tenant_key))]
            return {"counts":counts,"deliveries":items,"unbound_users":unbound}
