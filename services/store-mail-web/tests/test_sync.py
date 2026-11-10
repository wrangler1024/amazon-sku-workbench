"""sync 模块测试:增量拉取、去重、授权失败、退避、已读回写。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from store_mail_web.config import Settings
from store_mail_web.crypto import SecretBox, generate_master_key
from store_mail_web.imap_client import ImapAuthError
from store_mail_web.models import FetchedMail
from store_mail_web.risk import assess_mail
from store_mail_web.state import Store
from store_mail_web.sync import SyncEngine

BASE_TS = 1756900000


def make_mail(uid: int, subject: str = "邮件", from_addr: str = "a@b.com",
              body: str = "Your verification code is 482913, thanks.") -> FetchedMail:
    return FetchedMail(
        uid=uid, message_id=f"<m{uid}@x>", subject=subject,
        from_name="发件人", from_addr=from_addr, to_addr="store@example.invalid",
        internal_date=None, received_ts=BASE_TS + uid, size_bytes=100,
        unread=True, body_text=body, body_html=None, preview=body[:200],
    )


class FakeSession:
    """按 mailbox 脚本化 IMAP 会话。"""

    # mailbox -> {uid: FetchedMail};enter_failure -> (mailbox, exc)
    mailboxes: dict[str, dict[int, FetchedMail]] = {}
    enter_failures: dict[str, Exception] = {}
    fetch_failures: set[int] = set()
    marked_seen: list[tuple[str, int]] = []

    def __init__(self, host, port, mailbox, auth_code, timeout=30):
        self.mailbox = mailbox
        self.enter_error = FakeSession.enter_failures.get(mailbox)

    def __enter__(self):
        if self.enter_error is not None:
            raise self.enter_error
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def exists_count(self):
        return len(FakeSession.mailboxes.get(self.mailbox, {}))

    def search_uids_after(self, last_uid):
        return sorted(u for u in FakeSession.mailboxes.get(self.mailbox, {})
                      if u > last_uid)

    def search_uids_since_days(self, days):
        return sorted(FakeSession.mailboxes.get(self.mailbox, {}))

    def fetch_mail(self, uid):
        if uid in FakeSession.fetch_failures:
            return None
        return FakeSession.mailboxes.get(self.mailbox, {}).get(uid)

    def mark_seen(self, uid):
        FakeSession.marked_seen.append((self.mailbox, uid))
        return True


class SyncTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        FakeSession.mailboxes = {}
        FakeSession.enter_failures = {}
        FakeSession.fetch_failures = set()
        FakeSession.marked_seen = []
        self.settings = Settings(
            secret_key=generate_master_key(),
            db_path=Path(self.tmp.name) / "sync.sqlite3",
            initial_days=30, initial_limit=3, per_run_limit=10,
            sync_interval_seconds=120, sync_jitter_seconds=10,
            sync_backoff_max_seconds=600, manual_sync_min_gap_seconds=30,
            imap_timeout=5, mark_seen_on_open=True,
        )
        self.box = SecretBox(self.settings.secret_key)
        self.store = Store(self.settings.db_path, self.box)
        self.engine = SyncEngine(self.store, self.settings,
                                 session_factory=FakeSession)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def add_account(self, email="store@example.invalid", enabled=True):
        return self.store.create_account("店铺", email, "AUTH",
                                         "imap.163.com", 993, enabled=enabled)


class IncrementalSyncTest(SyncTestBase):
    def test_initial_pull_respects_limit(self):
        FakeSession.mailboxes["store@example.invalid"] = {
            u: make_mail(u) for u in (1, 2, 3, 4, 5)}
        account = self.add_account()
        result = self.engine.sync_account(account.id, force=True)
        self.assertEqual(result.status, "ok")
        # initial_limit=3 → 只取最新 3 封(3,4,5)
        self.assertEqual(result.fetched, 3)
        self.assertEqual(self.store.get_account(account.id).last_uid, 5)
        total, _ = self.store.query_messages()
        self.assertEqual(total, 3)

    def test_payout_amount_stored(self):
        FakeSession.mailboxes["store@example.invalid"] = {
            1: make_mail(1, "Your disbursement has been initiated",
                         "payments@amazon.com",
                         body="A disbursement of $1,234.56 was sent to your bank."),
        }
        account = self.add_account()
        self.engine.sync_account(account.id, force=True)
        _, rows = self.store.query_messages()
        self.assertEqual(rows[0]["category"], "回款")
        self.assertEqual(rows[0]["amount_text"], "$1,234.56")

    def test_incremental_only_new(self):
        FakeSession.mailboxes["store@example.invalid"] = {1: make_mail(1), 2: make_mail(2)}
        account = self.add_account()
        self.engine.sync_account(account.id, force=True)
        # 新到一封
        FakeSession.mailboxes["store@example.invalid"][3] = make_mail(3, "Amazon verification code")
        result = self.engine.sync_account(account.id, force=True)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.new_messages, 1)
        total, rows = self.store.query_messages()
        self.assertEqual(total, 3)

    def test_large_backlog_is_drained_without_skipping(self):
        FakeSession.mailboxes["store@example.invalid"] = {1: make_mail(1)}
        account = self.add_account()
        self.engine.sync_account(account.id, force=True)
        FakeSession.mailboxes["store@example.invalid"].update(
            {uid: make_mail(uid) for uid in range(2, 23)})
        first = self.engine.sync_account(account.id, force=True)
        self.assertEqual(first.fetched, 10)
        self.assertEqual(self.store.get_account(account.id).last_uid, 11)
        self.engine.sync_account(account.id, force=True)
        self.engine.sync_account(account.id, force=True)
        self.assertEqual(self.store.get_account(account.id).last_uid, 22)
        self.assertEqual(self.store.message_count(), 22)

    def test_failed_fetch_does_not_skip_uid(self):
        FakeSession.mailboxes["store@example.invalid"] = {1: make_mail(1)}
        account = self.add_account()
        self.engine.sync_account(account.id, force=True)
        FakeSession.mailboxes["store@example.invalid"].update({2: make_mail(2), 3: make_mail(3)})
        FakeSession.fetch_failures.add(2)
        failed = self.engine.sync_account(account.id, force=True)
        self.assertEqual(failed.status, "error")
        self.assertEqual(self.store.get_account(account.id).last_uid, 1)
        FakeSession.fetch_failures.clear()
        recovered = self.engine.sync_account(account.id, force=True)
        self.assertEqual(recovered.new_messages, 2)
        self.assertEqual(self.store.get_account(account.id).last_uid, 3)

    def test_no_new_messages(self):
        FakeSession.mailboxes["store@example.invalid"] = {1: make_mail(1)}
        account = self.add_account()
        self.engine.sync_account(account.id, force=True)
        result = self.engine.sync_account(account.id, force=True)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.new_messages, 0)

    def test_manual_gap_guard(self):
        FakeSession.mailboxes["store@example.invalid"] = {1: make_mail(1)}
        account = self.add_account()
        self.engine.sync_account(account.id, manual=True, force=True)
        result = self.engine.sync_account(account.id, manual=True, force=False)
        self.assertEqual(result.status, "skipped")
        self.assertIn("刚同步过", result.error)

    def test_disabled_account_skipped(self):
        account = self.add_account(enabled=False)
        result = self.engine.sync_account(account.id, force=True)
        self.assertEqual(result.status, "skipped")

    def test_classification_persisted(self):
        FakeSession.mailboxes["store@example.invalid"] = {
            1: make_mail(1, "Verify your account", "account@amazon.com"),
            2: make_mail(2, "Weekly Newsletter - deals inside", "promo@shop.com",
                         body="Buy now! Sale sale sale."),
        }
        account = self.add_account()
        self.engine.sync_account(account.id, force=True)
        _, rows = self.store.query_messages()
        amazon_row = next(r for r in rows if r["uid"] == 1)
        promo_row = next(r for r in rows if r["uid"] == 2)
        self.assertEqual(amazon_row["is_amazon"], 1)
        self.assertNotIn("otp_code", amazon_row)
        self.assertEqual(amazon_row["category"], "其他")  # 验证码邮件未必命中告警关键词
        self.assertEqual(promo_row["is_amazon"], 0)
        self.assertNotIn("otp_code", promo_row)
        self.assertEqual(promo_row["category"], "广告")


class FailureHandlingTest(SyncTestBase):
    def test_auth_error_marks_account(self):
        FakeSession.enter_failures["store@example.invalid"] = ImapAuthError(
            "登录失败(授权码可能失效): store@example.invalid")
        account = self.add_account()
        result = self.engine.sync_account(account.id, force=True)
        self.assertEqual(result.status, "auth_error")
        fresh = self.store.get_account(account.id)
        self.assertEqual(fresh.last_sync_status, "auth_error")
        self.assertIn("授权码", fresh.last_error)
        # 退避生效
        self.assertGreater(self.engine._backoff_wait(account.id), 0)

    def test_backoff_grows_and_resets(self):
        FakeSession.enter_failures["store@example.invalid"] = ImapAuthError("授权失败")
        account = self.add_account()
        first = self.engine._backoff_wait(account.id)
        self.engine.sync_account(account.id, force=True)
        second = self.engine._backoff_wait(account.id)
        self.engine.sync_account(account.id, force=True)
        third = self.engine._backoff_wait(account.id)
        self.assertEqual(first, 0)
        self.assertLessEqual(second, third)
        # 恢复后清零
        FakeSession.enter_failures.pop("store@example.invalid")
        FakeSession.mailboxes["store@example.invalid"] = {1: make_mail(1)}
        self.engine.sync_account(account.id, force=True)
        self.assertEqual(self.engine._backoff_wait(account.id), 0)

    def test_mark_seen_writeback(self):
        FakeSession.mailboxes["store@example.invalid"] = {1: make_mail(1)}
        account = self.add_account()
        self.engine.sync_account(account.id, force=True)
        ok = self.engine.mark_seen(account.id, 1)
        self.assertTrue(ok)
        self.assertEqual(FakeSession.marked_seen, [("store@example.invalid", 1)])


class PushEnqueueTest(SyncTestBase):
    def setUp(self):
        super().setUp()
        self.pushed = []
        self.system_pushed = []

        class FakeNotifier:
            def enqueue(_self, item):
                self.pushed.append(item)
                return True

            def enqueue_system(_self, text):
                self.system_pushed.append(text)
                return True

        self.engine.set_notifier(FakeNotifier())
        def assess(account_id, uid):
            row = self.store.risk_message_by_uid(account_id, uid)
            finding = assess_mail(row["subject"], row["from_addr"], row["body_text"],
                                  is_amazon=bool(row["is_amazon"]),
                                  legacy_category=row["category"])
            return self.store.risk_record_message(row["id"], finding)
        self.engine.set_risk_callback(assess)

    def test_new_messages_enqueued_once(self):
        FakeSession.mailboxes["store@example.invalid"] = {
            1: make_mail(1, "Your seller account is at risk of deactivation",
                         "alert@amazon.com", body="Seller account action required."),
            2: make_mail(2, "Weekly Newsletter - deals inside", "promo@shop.com",
                         body="Buy now! Sale sale sale."),
            3: make_mail(3, "Listing suppressed for ASIN B012345678",
                         "notice@amazon.com", body="Listing suppressed."),
        }
        account = self.add_account()
        self.engine.sync_account(account.id, force=True)
        self.assertEqual(len(self.pushed), 2)
        FakeSession.mailboxes["store@example.invalid"][4] = make_mail(
            4, "Listing suppressed for ASIN B012345678",
            "notice@amazon.com", body="Repeated listing notification.")
        self.engine.sync_account(account.id, force=True)
        self.assertEqual(len(self.pushed), 2)  # 同一事件的重复邮件不刷群
        self.assertEqual([item["severity"] for item in self.pushed], ["P0", "P1"])
        self.assertIn("account is at risk", self.pushed[0]["subject"])
        self.assertEqual(self.pushed[1]["subtype"], "Listing 不可售或被抑制")
        # 重复同步不重复推送
        self.engine.sync_account(account.id, force=True)
        self.assertEqual(len(self.pushed), 2)

    def test_payout_is_saved_for_reconciliation_but_not_pushed(self):
        FakeSession.mailboxes["store@example.invalid"] = {
            1: make_mail(1, "Your disbursement has been initiated",
                         "payments@amazon.com",
                         body="A disbursement of $1,234.56 was sent."),
        }
        account = self.add_account()
        self.engine.sync_account(account.id, force=True)
        self.assertEqual(self.pushed, [])
        event = self.store.risk_list_events()[0]
        self.assertEqual(event["bank_receipt_status"], "待核对到账")

    def test_monitoring_interruption_notifies_once_per_failure_episode(self):
        account = self.add_account()
        FakeSession.enter_failures["store@example.invalid"] = ImapAuthError("授权失败")
        self.engine.sync_account(account.id, force=True)
        self.engine.sync_account(account.id, force=True)
        self.assertEqual(len(self.system_pushed), 1)
        self.assertIn("监控中断", self.system_pushed[0])
        FakeSession.enter_failures.clear()
        FakeSession.mailboxes["store@example.invalid"] = {1: make_mail(1)}
        self.engine.sync_account(account.id, force=True)
        FakeSession.enter_failures["store@example.invalid"] = ImapAuthError("授权失败")
        self.engine.sync_account(account.id, force=True)
        self.assertEqual(len(self.system_pushed), 2)


if __name__ == "__main__":
    unittest.main()
