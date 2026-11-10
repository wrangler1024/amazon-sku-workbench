"""店铺风险看板：真实持久化、分类、去重、权限和处理状态。"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from store_mail_web.config import Settings
from store_mail_web.crypto import generate_master_key, hash_password
from store_mail_web.models import ClassifiedMail, FetchedMail
from store_mail_web.risk import assess_mail
from store_mail_web.service import ApiError, Service


SCENARIOS = (
    ("Your seller account is at risk of deactivation", "account", "P0"),
    ("Listing suppressed for ASIN B012345678", "listing", "P1"),
    ("Stranded inventory for ASIN B012345678", "fba", "P1"),
    ("Shipment discrepancy for FBA123456789", "fba", "P1"),
    ("Charge was declined for your seller account", "finance", "P1"),
    ("Payment has been initiated", "finance", "P2"),
    ("New buyer message about order 123-1234567-1234567", "customer", "P1"),
    ("Provide compliance documents for ASIN B012345678", "compliance", "P1"),
)


class RiskClassifierTest(unittest.TestCase):
    def test_business_scenarios(self):
        for title, kind, severity in SCENARIOS:
            with self.subTest(title=title):
                finding = assess_mail(title, "notice@amazon.com", is_amazon=True)
                self.assertIsNotNone(finding)
                self.assertEqual((finding.event_type, finding.severity),
                                 (kind, severity))

    def test_ordinary_and_login_code_mail_do_not_crowd_dashboard(self):
        self.assertIsNone(assess_mail("September promotion newsletter",
                                      "news@amazon.com", is_amazon=True))
        self.assertIsNone(assess_mail("Your verification code is 123456",
                                      "notice@amazon.com", is_amazon=True))

    def test_spoofed_subject_requires_review(self):
        finding = assess_mail("Amazon account suspended", "x@fakeamazon.com",
                              is_amazon=True)
        self.assertEqual(finding.event_type, "review")
        self.assertEqual(finding.severity, "P2")

    def test_finance_currency_and_mail_deadline_are_not_inferred_as_bank_receipt(self):
        payout = assess_mail("Payment has been initiated: USD 1,241.50",
                             "payments@amazon.com", is_amazon=True)
        self.assertEqual(payout.amount_text, "USD1,241.50")
        self.assertEqual(payout.finance_state, "平台已发起")
        self.assertFalse(payout.action_required)
        compliance = assess_mail("Provide compliance documents by 2026-10-01",
                                 "notice@amazon.com", is_amazon=True)
        self.assertEqual(compliance.deadline_text, "by 2026-10-01")


class RiskDashboardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = Service.build(Settings(
            secret_key=generate_master_key(), admin_password="test-password",
            db_path=Path(self.tmp.name) / "risk.sqlite3"))
        self.admin = self.service.store.get_user_by_username("admin")
        self.owner = self.service.store.create_user(
            "operator", hash_password("test-password"), account_ids=())
        self.other = self.service.store.create_user(
            "outsider", hash_password("test-password"), account_ids=())
        self.account = self.service.store.create_account(
            "加拿大店", "canada@example.invalid", "AUTH", "imap.163.com", 993,
            site="CA", owner_user_id=self.owner.id)
        self.service._grant_owner_account_access(self.account.id, self.owner.id)
        self.now = int(time.time())

    def tearDown(self):
        self.service.store.close()
        self.tmp.cleanup()

    def add_mail(self, uid: int, subject: str, *, account_id=None,
                 sender="notice@amazon.com") -> int:
        account_id = account_id or self.account.id
        mail = FetchedMail(
            uid=uid, message_id=f"risk-{uid}@amazon.com", subject=subject,
            from_name="Amazon", from_addr=sender, to_addr="canada@example.invalid",
            internal_date=None, received_ts=self.now + uid,
            size_bytes=400, unread=True, body_text=subject,
            body_html=None, preview=subject)
        self.service.store.upsert_message(account_id, mail, ClassifiedMail(
            True, "其他", "低"))
        self.service.assess_synced_message(account_id, uid)
        row = self.service.store.risk_message_by_uid(account_id, uid)
        return row["id"]

    def test_account_matrix_queue_and_finance(self):
        for uid, (title, _, _) in enumerate(SCENARIOS, 1):
            self.add_mail(uid, title)
        self.add_mail(9, "September promotion newsletter")
        self.service.store.set_sync_state(self.account.id, "ok", None)
        data = self.service.dashboard(self.admin)
        self.assertEqual(data["summary"]["critical_open"], 1)
        self.assertEqual(data["summary"]["listing_open"], 1)
        self.assertEqual(data["summary"]["fba_open"], 2)
        self.assertEqual(data["summary"]["finance_open"], 1)
        self.assertEqual(data["matrix"][0]["account"]["site"], "CA")
        self.assertEqual(data["matrix"][0]["listing"]["state"], "warning")
        self.assertEqual(data["queue"][0]["severity"], "P0")
        self.assertEqual(len(data["finance"]), 2)
        payout = next(e for e in data["finance"] if e["finance_state"] == "平台已发起")
        self.assertFalse(payout["action_required"])
        self.assertNotIn(payout["id"], {e["id"] for e in data["queue"]})

    def test_duplicate_listing_mail_groups_but_preserves_sources(self):
        first = self.add_mail(1, "Listing suppressed for ASIN B012345678")
        second = self.add_mail(2, "Listing suppressed for ASIN B012345678")
        events = self.service.store.risk_list_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["message_count"], 2)
        detail = self.service.risk_event(events[0]["id"], self.admin)
        self.assertEqual({m["id"] for m in detail["messages"]}, {first, second})

    def test_same_person_names_keep_distinct_owner_ids_and_store_names(self):
        self.service.store.update_user(self.owner.id, full_name="张三")
        second = self.service.store.create_user(
            "other_ops", hash_password("test-password"),
            account_ids=(self.account.id,), full_name="张三")
        self.service.store.update_account(
            self.account.id, chinese_name="枫叶一店", set_chinese_name=True)
        self.add_mail(1, "Listing suppressed for ASIN B012345678")
        event = self.service.store.risk_list_events()[0]
        self.assertEqual(event["account_display_name"], "加拿大店")
        self.assertEqual(event["account_chinese_name"], "枫叶一店")
        self.assertEqual((event["owner_user_id"], event["owner_name"],
                          event["owner_display_name"]),
                         (self.owner.id, "operator", "张三"))
        eligible = self.service.risk_eligible_users(self.account.id, self.admin)
        self.assertEqual({u["id"] for u in eligible if u["full_name"] == "张三"},
                         {self.owner.id, second.id})
        updated = self.service.update_risk_event(event["id"], {
            "owner_user_id": second.id, "version": event["version"],
        }, self.admin)
        self.assertEqual(updated["owner_user_id"], second.id)
        self.assertEqual(updated["owner_display_name"], "张三")

    def test_repeated_subject_without_entity_groups_only_within_seven_days(self):
        first = self.add_mail(1, "Charge was declined for your seller account")
        second = self.add_mail(2, "Charge was declined for your seller account")
        events = self.service.store.risk_list_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["message_count"], 2)
        self.assertEqual({m["id"] for m in self.service.risk_event(events[0]["id"], self.admin)["messages"]},
                         {first, second})
        self.now += 8 * 24 * 3600
        self.add_mail(3, "Charge was declined for your seller account")
        self.assertEqual(len(self.service.store.risk_list_events()), 2)

    def test_payout_receipt_requires_manual_evidence_and_is_audited(self):
        self.service.store.update_user(self.owner.id, role="director")
        self.add_mail(1, "Payment has been initiated: USD 1,241.50")
        event = self.service.store.risk_list_events()[0]
        self.assertEqual(event["bank_receipt_status"], "待核对到账")
        owner = self.service.store.get_user(self.owner.id)
        with self.assertRaises(ApiError) as missing_evidence:
            self.service.update_risk_event(event["id"], {
                "bank_receipt_status": "已核对到账", "version": event["version"],
            }, owner)
        self.assertEqual(missing_evidence.exception.status, 400)
        updated = self.service.update_risk_event(event["id"], {
            "bank_receipt_status": "已核对到账", "note": "已核对银行流水 09-29",
            "version": event["version"],
        }, owner)
        self.assertEqual(updated["bank_receipt_status"], "已核对到账")
        self.assertIn("已核对银行流水", updated["activity"][0]["detail"])
        self.assertEqual(self.service.dashboard(self.admin)["finance"][0]["bank_receipt_status"],
                         "已核对到账")

    def test_identical_payout_subjects_without_transfer_id_remain_separate(self):
        self.add_mail(1, "Payment has been initiated")
        self.add_mail(2, "Payment has been initiated")
        payouts = [e for e in self.service.store.risk_list_events()
                   if e["finance_state"] == "平台已发起"]
        self.assertEqual(len(payouts), 2)

    def test_failed_sync_keeps_last_success_time_and_member_status_is_filtered(self):
        self.service.store.set_sync_state(self.account.id, "ok", None)
        success = self.service.store.get_account(self.account.id).last_success_at
        self.service.store.set_sync_state(self.account.id, "auth_error", "授权失效")
        account = self.service.store.get_account(self.account.id)
        self.assertEqual(account.last_success_at, success)
        self.assertTrue(self.service.dashboard(self.admin)["matrix"][0]["sync_interrupted"])
        owner = self.service.store.get_user(self.owner.id)
        self.assertEqual([a["account_id"] for a in self.service.sync_status(owner)["accounts"]],
                         [self.account.id])
        self.assertEqual(self.service.sync_status(owner)["accounts"][0]["last_success_at"], success)

    def test_update_audit_and_visibility(self):
        self.add_mail(1, "Listing suppressed for ASIN B012345678")
        event = self.service.store.risk_list_events()[0]
        owner = self.service.store.get_user(self.owner.id)
        outsider = self.service.store.get_user(self.other.id)
        with self.assertRaises(ApiError) as denied:
            self.service.risk_event(event["id"], outsider)
        self.assertEqual(denied.exception.status, 404)
        changed = self.service.update_risk_event(event["id"], {
            "status": "处理中", "note": "已在卖家后台核实", "version": event["version"],
        }, owner)
        self.assertEqual(changed["status"], "处理中")
        self.assertTrue(any("已在卖家后台核实" in row["detail"]
                            for row in changed["activity"]))
        with self.assertRaises(ApiError) as conflict:
            self.service.update_risk_event(event["id"], {
                "status": "已完成", "version": event["version"],
            }, owner)
        self.assertEqual(conflict.exception.status, 409)

    def test_member_can_claim_unassigned_but_cannot_steal_assignment(self):
        self.add_mail(1, "Listing suppressed for ASIN B012345678")
        event = self.service.store.risk_list_events()[0]
        second = self.service.store.create_user(
            "second_ops", hash_password("test-password"),
            account_ids=(self.account.id,))
        with self.assertRaises(ApiError) as denied:
            self.service.update_risk_event(event["id"], {
                "owner_user_id": second.id,
            }, self.service.store.get_user(second.id))
        self.assertEqual(denied.exception.status, 403)
        self.service.store.risk_update_event(
            event["id"], user_id=self.admin.id, owner_user_id=None, set_owner=True)
        claimed = self.service.update_risk_event(event["id"], {
            "owner_user_id": second.id,
        }, self.service.store.get_user(second.id))
        self.assertEqual(claimed["owner_user_id"], second.id)

    def test_history_not_misreported_as_current_risk(self):
        self.add_mail(1, "Listing suppressed for ASIN B012345678")
        event = self.service.store.risk_list_events()[0]
        self.service.store.risk_update_event(event["id"], user_id=self.admin.id,
                                             status="已完成")
        self.add_mail(2, "Listing suppressed for ASIN B012345678")
        self.assertEqual(len(self.service.store.risk_list_events()), 2)
        self.service.store.set_sync_state(self.account.id, "auth_error", "授权失败")
        data = self.service.dashboard(self.admin)
        self.assertEqual(data["summary"]["sync_interrupted"], 1)
        self.assertEqual(data["matrix"][0]["listing"]["state"], "unknown")


if __name__ == "__main__":
    unittest.main()
