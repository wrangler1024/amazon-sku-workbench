"""state 模块测试:账号加密存储、消息查询和 API Key。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from store_mail_web.crypto import SecretBox, generate_master_key
from store_mail_web.models import ClassifiedMail, FetchedMail
from store_mail_web.risk import assess_mail
from store_mail_web.state import Store


def make_mail(uid: int, subject: str = "主题", from_addr: str = "a@b.com",
              body: str = "正文" * 20, received_ts: int = 1756900000,
              unread: bool = True) -> FetchedMail:
    return FetchedMail(
        uid=uid, message_id=f"<m{uid}@x>", subject=subject,
        from_name="发件人", from_addr=from_addr, to_addr="store@example.invalid",
        internal_date=None, received_ts=received_ts, size_bytes=100,
        unread=unread, body_text=body, body_html=None, preview=body[:200],
    )


PLAIN = ClassifiedMail(False, "其他", "低")
AMAZON_ALERT = ClassifiedMail(True, "告警", "高")
PAYOUT = ClassifiedMail(True, "回款", "中", "$1,234.56")


class StoreTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.box = SecretBox(generate_master_key())
        self.store = Store(Path(self.tmp.name) / "test.sqlite3", self.box)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()


class AccountTest(StoreTestBase):
    def test_create_and_encrypt(self):
        account = self.store.create_account("店铺A", "a@example.invalid", "AUTHCODE!",
                                            "imap.163.com", 993)
        self.assertEqual(account.display_name, "店铺A")
        # 库里没有明文授权码
        raw = Path(self.tmp.name, "test.sqlite3").read_bytes()
        self.assertNotIn(b"AUTHCODE!", raw)
        # 解密一致
        self.assertEqual(self.store.get_auth_code(account.id), "AUTHCODE!")

    def test_find_account_variants(self):
        self.store.create_account("店铺A", "A@example.invalid", "code",
                                  "imap.163.com", 993)
        self.assertIsNotNone(self.store.find_account("a@example.invalid"))
        self.assertIsNotNone(self.store.find_account("店铺A"))
        self.assertIsNone(self.store.find_account("不存在"))

    def test_duplicate_email(self):
        import sqlite3
        self.store.create_account("A", "a@example.invalid", "c", "imap.163.com", 993)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_account("B", "a@example.invalid", "c", "imap.163.com", 993)

    def test_update_and_rotate_auth_code(self):
        account = self.store.create_account("A", "a@example.invalid", "old",
                                            "imap.163.com", 993)
        updated = self.store.update_account(account.id, auth_code="new",
                                            display_name="改名")
        self.assertEqual(updated.display_name, "改名")
        self.assertEqual(self.store.get_auth_code(account.id), "new")

    def test_platform_and_chinese_names_are_independent(self):
        account = self.store.create_account("DEMO-STORE-US", "a@example.invalid", "c",
                                            "imap.163.com", 993, chinese_name="美西一店")
        self.assertEqual((account.display_name, account.chinese_name),
                         ("DEMO-STORE-US", "美西一店"))
        changed = self.store.update_account(account.id, chinese_name="", set_chinese_name=True)
        self.assertEqual((changed.display_name, changed.chinese_name), ("DEMO-STORE-US", ""))

    def test_sync_state_persists(self):
        account = self.store.create_account("A", "a@example.invalid", "c",
                                            "imap.163.com", 993)
        self.store.set_sync_state(account.id, "ok", None, last_uid=42)
        fresh = self.store.get_account(account.id)
        self.assertEqual(fresh.last_uid, 42)
        self.assertEqual(fresh.last_sync_status, "ok")


class MessageTest(StoreTestBase):
    def setUp(self):
        super().setUp()
        self.account = self.store.create_account("店铺A", "a@example.invalid", "c",
                                                 "imap.163.com", 993)
        self.other = self.store.create_account("店铺B", "b@example.invalid", "c",
                                               "imap.163.com", 993)

    def test_upsert_dedup(self):
        mail = make_mail(101)
        self.assertTrue(self.store.upsert_message(self.account.id, mail, PLAIN))
        self.assertFalse(self.store.upsert_message(self.account.id, mail, PLAIN))
        total, rows = self.store.query_messages()
        self.assertEqual(total, 1)

    def test_query_filters(self):
        self.store.upsert_message(
            self.account.id,
            make_mail(1, "Amazon verification code", "amazon@amazon.com"),
            AMAZON_ALERT)
        self.store.upsert_message(
            self.account.id,
            make_mail(2, "普通 newsletter", "news@x.com", received_ts=1756900600),
            PLAIN)
        self.store.upsert_message(self.other.id,
                                  make_mail(3, "别家的信", "z@z.com"), PLAIN)

        total, rows = self.store.query_messages()
        self.assertEqual(total, 3)

        total, _ = self.store.query_messages(account_id=self.account.id)
        self.assertEqual(total, 2)

        total, rows = self.store.query_messages(q="verification")  # 命中主题
        self.assertEqual(total, 1)
        self.assertNotIn("otp_code", rows[0])

        total, _ = self.store.query_messages(amazon_only=True)
        self.assertEqual(total, 1)

        total, _ = self.store.query_messages(since_ts=1756900500)
        self.assertEqual(total, 1)

        total, rows = self.store.query_messages(limit=2)
        self.assertEqual(total, 3)
        self.assertEqual(len(rows), 2)

    def test_finance_and_shipment_checkboxes_match_legacy_and_risk_events(self):
        cases = (
            (1, "Card charge declined", "扣款", "finance"),
            (2, "FBA shipment checked in", "货件", "fba"),
            (3, "Funds on hold", "其他", "finance"),
            (4, "Stranded inventory for ASIN B012345678", "其他", "fba"),
        )
        for uid, subject, category, risk_type in cases:
            self.store.upsert_message(
                self.account.id, make_mail(uid, subject, "notice@amazon.com"),
                ClassifiedMail(True, category, "中"))
            if category == "其他":
                message_id = self.store.risk_message_by_uid(self.account.id, uid)["id"]
                finding = assess_mail(subject, "notice@amazon.com", is_amazon=True)
                self.assertEqual(finding.event_type, risk_type)
                self.store.risk_record_message(message_id, finding)
        self.store.upsert_message(self.other.id, make_mail(5, "Funds on hold"), PLAIN)
        visible = [self.account.id]
        total, _ = self.store.query_messages(
            finance_only=True, visible_account_ids=visible)
        self.assertEqual(total, 2)
        total, _ = self.store.query_messages(
            shipment_only=True, visible_account_ids=visible)
        self.assertEqual(total, 2)
        total, rows = self.store.query_messages(
            finance_only=True, shipment_only=True,
            visible_account_ids=visible)
        self.assertEqual(total, 4)  # 两个主题勾选是并集
        self.assertEqual({row["uid"] for row in rows}, {1, 2, 3, 4})

    def test_like_escapes_wildcards(self):
        self.store.upsert_message(self.account.id,
                                  make_mail(1, "100% off sale"), PLAIN)
        total, _ = self.store.query_messages(q="100% off")
        self.assertEqual(total, 1)

    def test_important_only_filter(self):
        self.store.upsert_message(
            self.account.id,
            make_mail(1, "Account Health warning", "alert@amazon.com"),
            AMAZON_ALERT)   # 告警
        self.store.upsert_message(
            self.account.id, make_mail(2, "Newsletter"), PLAIN)  # 其他
        total, rows = self.store.query_messages(important_only=True)
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["category"], "告警")
        # 回款也算重要
        self.store.upsert_message(
            self.account.id,
            make_mail(3, "Disbursement initiated", "pay@amazon.com",
                      received_ts=1756900900),
            PAYOUT)
        total, rows = self.store.query_messages(important_only=True)
        self.assertEqual(total, 2)
        self.assertEqual(rows[0]["amount_text"], "$1,234.56")

    def test_read_state(self):
        """已读状态按用户独立:unread 列在传 user_id 时为用户视角。"""
        self.store.upsert_message(self.account.id, make_mail(1), PLAIN)
        user_a = self.store.create_user("alice", "hash-a", "user",
                                        (self.account.id,))
        user_b = self.store.create_user("bob", "hash-b", "user",
                                        (self.account.id,))
        _, rows = self.store.query_messages(user_id=user_a.id)
        message_id = rows[0]["id"]
        self.assertEqual(rows[0]["unread"], 1)
        # A 标已读:B 视角仍为未读(物理 IMAP 状态也不变)
        self.assertTrue(self.store.mark_user_read(user_a.id, message_id))
        self.assertFalse(self.store.mark_user_read(user_a.id, message_id))  # 幂等
        row_b = self.store.get_message(message_id, with_body=True,
                                       user_id=user_b.id)
        self.assertEqual(row_b["unread"], 1)
        row_a = self.store.get_message(message_id, with_body=True,
                                       user_id=user_a.id)
        self.assertEqual(row_a["unread"], 0)
        self.assertEqual(row_a["imap_seen"], 1)   # 物理 \Seen 未被用户操作改变
        # B 取消已读 → 无变化;A 取消已读 → 恢复未读
        self.assertFalse(self.store.mark_user_unread(user_b.id, message_id))
        self.assertTrue(self.store.mark_user_unread(user_a.id, message_id))
        _, rows = self.store.query_messages(user_id=user_a.id)
        self.assertEqual(rows[0]["unread"], 1)
        # 回写标记独立于用户
        self.store.set_message_seen_synced(message_id, True)
        self.assertEqual(
            self.store.get_message(message_id, with_body=True)["seen_synced"], 1)

    def test_unread_only_per_user(self):
        self.store.upsert_message(self.account.id, make_mail(1), PLAIN)
        self.store.upsert_message(self.account.id, make_mail(2), PLAIN)
        user = self.store.create_user("carol", "hash-c", "user",
                                      (self.account.id,))
        total, _ = self.store.query_messages(unread_only=True, user_id=user.id)
        self.assertEqual(total, 2)
        _, rows = self.store.query_messages(user_id=user.id)
        self.store.mark_user_read(user.id, rows[-1]["id"])
        total, rows = self.store.query_messages(unread_only=True, user_id=user.id)
        self.assertEqual(total, 1)

    def test_visibility_filter(self):
        self.store.upsert_message(self.account.id, make_mail(1), PLAIN)
        self.store.upsert_message(self.other.id, make_mail(2), PLAIN)
        user = self.store.create_user("dave", "hash-d", "user",
                                      (self.account.id,))   # 只可见第一个邮箱
        total, rows = self.store.query_messages(
            visible_account_ids=list(user.account_ids), user_id=user.id)
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["account_id"], self.account.id)
        # 空可见集 = 什么都看不到
        total, _ = self.store.query_messages(visible_account_ids=[], user_id=1)
        self.assertEqual(total, 0)
        self.assertEqual(self.store.message_count(
            visible_account_ids=list(user.account_ids)), 1)

    def test_mark_all_read(self):
        self.store.upsert_message(self.account.id, make_mail(1), PLAIN)
        self.store.upsert_message(self.account.id, make_mail(2), PLAIN)
        self.store.upsert_message(self.other.id, make_mail(3), PLAIN)
        user = self.store.create_user("erin", "hash-e", "user",
                                      (self.account.id,))
        marked = self.store.mark_all_read(user.id, list(user.account_ids))
        self.assertEqual(marked, 2)
        total, _ = self.store.query_messages(
            unread_only=True, user_id=user.id,
            visible_account_ids=list(user.account_ids))
        self.assertEqual(total, 0)

    def test_message_flags(self):
        """标记按用户独立:筛选与列表附带只反映自己的标记。"""
        self.store.upsert_message(self.account.id, make_mail(1), PLAIN)
        self.store.upsert_message(self.account.id, make_mail(2), PLAIN)
        user_a = self.store.create_user("gina", "hash-g", "user",
                                        (self.account.id,))
        user_b = self.store.create_user("hank", "hash-h", "user",
                                        (self.account.id,))
        _, rows = self.store.query_messages(user_id=user_a.id)
        first, second = rows[0], rows[1]
        self.assertEqual(first["flags"], [])

        self.assertTrue(self.store.set_message_flag(
            user_a.id, first["id"], "star", True))
        self.assertFalse(self.store.set_message_flag(
            user_a.id, first["id"], "star", True))     # 幂等
        self.store.set_message_flag(user_a.id, first["id"], "flag", True)
        self.store.set_message_flag(user_a.id, second["id"], "pin", True)

        # 列表附带:A 看得到自己的标记,B 看不到
        _, rows_a = self.store.query_messages(user_id=user_a.id)
        row = next(r for r in rows_a if r["id"] == first["id"])
        self.assertEqual(row["flags"], ["flag", "star"])
        row_b = self.store.get_message(first["id"], user_id=user_b.id)
        self.assertEqual(row_b["flags"], [])

        # 筛选:按类型 / 任意 / 取消后回到无
        total, _ = self.store.query_messages(
            flag_type="star", user_id=user_a.id)
        self.assertEqual(total, 1)
        total, _ = self.store.query_messages(
            flag_type="any", user_id=user_a.id)
        self.assertEqual(total, 2)
        total, _ = self.store.query_messages(
            flag_type="star", user_id=user_b.id)
        self.assertEqual(total, 0)
        self.store.set_message_flag(user_a.id, first["id"], "star", False)
        total, _ = self.store.query_messages(
            flag_type="star", user_id=user_a.id)
        self.assertEqual(total, 0)
        row = self.store.get_message(first["id"], user_id=user_a.id)
        self.assertEqual(row["flags"], ["flag"])

    def test_summaries(self):
        self.store.upsert_message(self.account.id, make_mail(1), PLAIN)
        self.store.upsert_message(
            self.account.id, make_mail(2, "code", received_ts=1756900600),
            AMAZON_ALERT)
        summaries = self.store.account_summaries()
        self.assertEqual(summaries[self.account.id]["total"], 2)
        self.assertEqual(summaries[self.account.id]["unread"], 2)
        self.assertNotIn("latest_otp_at", summaries[self.account.id])
        self.assertNotIn(self.other.id, summaries)

    def test_dashboard_stats(self):
        user = self.store.create_user("frank", "hash-f", "user",
                                      (self.account.id,))
        self.store.upsert_message(
            self.account.id,
            make_mail(1, "Account Health warning", "a@amazon.com"),
            AMAZON_ALERT)                                  # 告警,未读
        _, rows = self.store.query_messages(user_id=user.id)
        self.store.mark_user_read(user.id, rows[0]["id"])
        self.store.upsert_message(
            self.account.id,
            make_mail(2, "Performance Notification", "p@amazon.com",
                      received_ts=1756900600),
            ClassifiedMail(True, "绩效", "中"))
        self.store.upsert_message(
            self.account.id,
            make_mail(3, "Disbursement initiated", "pay@amazon.com",
                      received_ts=1756900900),
            PAYOUT)
        self.store.upsert_message(
            self.other.id, make_mail(9, "别家普通邮件"), PLAIN)
        stats = self.store.dashboard_stats(user_id=user.id,
                                           visible_account_ids=[self.account.id])
        mine = stats[self.account.id]
        self.assertIsNotNone(mine["last_alert_at"])
        self.assertIsNotNone(mine["last_perf_at"])
        self.assertEqual(mine["last_payout_amount"], "$1,234.56")
        self.assertEqual(mine["last_payout_subject"], "Disbursement initiated")
        self.assertEqual(mine["important_unread"], 2)   # 绩效+回款未读,告警已读
        self.assertNotIn(self.other.id, stats)          # 不可见邮箱不进看板

    def test_reclassify_roundtrip(self):
        self.store.upsert_message(self.account.id,
                                  make_mail(1, "普通推广"), PLAIN)
        batches = list(self.store.iter_messages_for_reclassify())
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0][0]["subject"], "普通推广")
        self.store.update_classification(
            batches[0][0]["id"], AMAZON_ALERT)
        row = self.store.get_message(batches[0][0]["id"], with_body=True)
        self.assertEqual(row["category"], "告警")
        self.assertNotIn("otp_code", row)


class ApiKeyStoreTest(StoreTestBase):
    def test_crud_and_use(self):
        key = self.store.create_api_key("Agent", "smw_ABCDEFG", "hash1", 30)
        self.assertTrue(key.enabled)
        found = self.store.find_api_key_by_hash("hash1")
        self.assertEqual(found.id, key.id)
        self.store.record_api_key_use(key.id)
        self.assertEqual(self.store.get_api_key(key.id).request_count, 1)
        self.store.set_api_key_enabled(key.id, False)
        self.assertFalse(self.store.get_api_key(key.id).enabled)
        self.assertTrue(self.store.delete_api_key(key.id))
        self.assertIsNone(self.store.find_api_key_by_hash("hash1"))


class MigrationTest(unittest.TestCase):
    def test_old_schema_gets_amount_column(self):
        import sqlite3

        from store_mail_web import state as state_mod
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / "old.sqlite3"
        # 模拟 v0.1 旧库:messages 没有 amount_text 列
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            state_mod.SCHEMA.replace("amount_text TEXT,\n    ", ""))
        conn.commit()
        columns = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
        conn.close()
        self.assertNotIn("amount_text", columns)
        # Store 打开时自动补列
        store = Store(db_path, SecretBox(generate_master_key()))
        try:
            account = store.create_account("A", "a@example.invalid", "c",
                                           "imap.163.com", 993)
            self.assertTrue(store.upsert_message(
                account.id, make_mail(1), PAYOUT))
            _, rows = store.query_messages()
            self.assertEqual(rows[0]["amount_text"], "$1,234.56")
        finally:
            store.close()

    def test_old_accounts_get_only_verifiable_success_time(self):
        import sqlite3

        from store_mail_web import state as state_mod
        from store_mail_web.risk_store import RISK_SCHEMA
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / "old-sync.sqlite3"
        conn = sqlite3.connect(str(db_path))
        old_schema = (state_mod.SCHEMA
                      .replace("    last_success_at TEXT,\n", "")
                      .replace("    chinese_name TEXT NOT NULL DEFAULT '',\n", "")
                      .replace("    full_name TEXT NOT NULL DEFAULT '',\n", ""))
        conn.executescript(old_schema)
        conn.executescript(RISK_SCHEMA.replace("    bank_receipt_status TEXT,\n", ""))
        conn.execute("INSERT INTO users(username,password_hash,role,created_at) "
                     "VALUES ('admin','','admin','2026-09-28T00:00:00+00:00')")
        for status, email in (("ok", "ok@example.invalid"), ("auth_error", "failed@example.invalid")):
            conn.execute(
                "INSERT INTO accounts(display_name,email,auth_code_enc,last_sync_at,"
                "last_sync_status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (email, email, "encrypted", "2026-09-28T08:00:00+00:00",
                 status, "2026-09-28T00:00:00+00:00", "2026-09-28T08:00:00+00:00"))
        conn.commit()
        conn.close()
        store = Store(db_path, SecretBox(generate_master_key()))
        try:
            self.assertEqual(store.find_account("ok@example.invalid").last_success_at,
                             "2026-09-28T08:00:00+00:00")
            self.assertIsNone(store.find_account("failed@example.invalid").last_success_at)
            self.assertEqual(store.find_account("ok@example.invalid").chinese_name, "")
            self.assertEqual(store.get_user_by_username("admin").full_name, "")
        finally:
            store.close()
        reopened = Store(db_path, SecretBox(generate_master_key()))
        try:
            self.assertEqual(len(reopened.list_accounts()), 2)
            self.assertEqual(reopened.get_user_by_username("admin").full_name, "")
        finally:
            reopened.close()


class SettingsTest(StoreTestBase):
    def test_roundtrip(self):
        self.assertIsNone(self.store.get_setting("k"))
        self.assertEqual(self.store.get_setting("k", "d"), "d")
        self.store.set_setting("k", '{"a": 1}')
        self.assertEqual(self.store.get_setting("k"), '{"a": 1}')
        self.store.set_setting("k", "v2")
        self.assertEqual(self.store.get_setting("k"), "v2")


if __name__ == "__main__":
    unittest.main()
