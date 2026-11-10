"""notify 模块测试:推送文本、签名、配置门控、直发与后台队列。"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from store_mail_web import notify
from store_mail_web.crypto import SecretBox, generate_master_key
from store_mail_web.notify import PushNotifier, feishu_sign, format_push_text
from store_mail_web.state import Store

VALID_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/test-token"


class FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


class FormatAndSignTest(unittest.TestCase):
    def test_format_contains_fields(self):
        text = format_push_text({
            "category": "回款", "priority": "中",
            "display_name": "店铺A-US", "email": "a@example.invalid",
            "subject": "Disbursement initiated",
            "from_name": "Amazon", "from_addr": "no-reply@amazon.com",
            "amount": "$1,234.56", "otp_code": None,
            "preview": "Your disbursement", "received_at": "2026-09-03T10:00:00+00:00",
        })
        self.assertIn("[回款] 店铺A-US (a@example.invalid)", text)
        self.assertIn("Disbursement initiated", text)
        self.assertIn("金额: $1,234.56", text)
        self.assertNotIn("验证码", text)

    def test_chinese_store_name_keeps_platform_name(self):
        text = format_push_text({
            "severity": "P1", "subtype": "Listing 异常", "display_name": "美西一店",
            "platform_name": "DEMO-STORE-US", "email": "a@example.invalid",
            "owner_name": "张三", "subject": "Listing suppressed",
        })
        self.assertIn("美西一店 / DEMO-STORE-US", text)
        self.assertIn("负责人: 张三", text)

    def test_format_hides_otp(self):
        text = format_push_text({"category": "告警",
                                 "subject": "Code 482913 needs attention",
                                 "otp_code": "482913",
                                 "preview": "The code 482913 is private"})
        self.assertNotIn("482913", text)
        self.assertNotIn("The code", text)

    def test_format_risk_action_and_redact_code_in_mixed_subject(self):
        text = format_push_text({
            "severity": "P0", "subtype": "账号停用风险", "category": "告警",
            "display_name": "店铺A", "subject": "Account suspended, verification code 482913",
            "owner_name": "ops", "due_at": "2026-09-29T10:00:00+00:00",
        })
        self.assertIn("[P0 · 账号停用风险]", text)
        self.assertIn("负责人: ops", text)
        self.assertNotIn("482913", text)

    def test_sign_stable_and_distinct(self):
        sign1 = feishu_sign("secret", 1756900000)
        self.assertEqual(sign1, feishu_sign("secret", 1756900000))
        self.assertNotEqual(sign1, feishu_sign("other", 1756900000))
        self.assertNotEqual(sign1, feishu_sign("secret", 1756900001))


class NotifierConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "n.sqlite3",
                           SecretBox(generate_master_key()))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_default_categories_when_unset(self):
        notifier = PushNotifier(self.store)
        self.store.set_setting(notify.SETTING_URL, VALID_URL)
        config = notifier.config()
        self.assertEqual(config["url"], VALID_URL)
        self.assertEqual(config["push_categories"],
                         ("告警", "侵权", "绩效", "扣款", "回款", "货件", "客户", "广告", "其他"))

    def test_env_fallback_and_db_override(self):
        notifier = PushNotifier(self.store, env_url=VALID_URL, env_secret="s1")
        self.assertEqual(notifier.config()["url"], VALID_URL)
        self.assertEqual(notifier.config()["secret"], "s1")
        # DB 配置优先于 env
        self.store.set_setting(notify.SETTING_URL,
                               VALID_URL + "-db")
        self.assertEqual(notifier.config()["url"], VALID_URL + "-db")

    def test_enqueue_gating(self):
        notifier = PushNotifier(self.store)
        item_alert = {"category": "告警", "subject": "a", "severity": "P0",
                      "action_required": True}
        item_ad = {"category": "广告", "subject": "b", "severity": "P1",
                   "action_required": True}
        # 未配置 URL → 不入队
        self.assertFalse(notifier.enqueue(item_alert))
        self.store.set_setting(notify.SETTING_URL, VALID_URL)
        self.assertTrue(notifier.enqueue(item_alert))
        self.assertTrue(notifier.enqueue(item_ad))
        self.assertFalse(notifier.enqueue({"category": "其他", "subject": "普通推广"}))
        # 自定义推送集
        self.store.set_setting(notify.SETTING_PUSH_CATEGORIES, "回款")
        self.assertTrue(notifier.enqueue({"category": "回款", "severity": "P1",
                                          "action_required": True}))
        self.assertTrue(notifier.enqueue(item_alert))  # P0 始终提醒
        self.assertFalse(notifier.enqueue(item_ad))


class SendNowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "n.sqlite3",
                           SecretBox(generate_master_key()))
        self.notifier = PushNotifier(self.store)
        self.store.set_setting(notify.SETTING_URL, VALID_URL)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _patch_urlopen(self, fake):
        original = urllib.request.urlopen
        urllib.request.urlopen = fake
        self.addCleanup(setattr, urllib.request, "urlopen", original)

    def test_send_now_ok_without_secret(self):
        captured = []
        self._patch_urlopen(
            lambda req, timeout=None: (
                captured.append((req.full_url, req.data)),
                FakeResponse(b'{"code":0,"msg":"success"}'))[1])
        result = self.notifier.send_now("hello")
        self.assertTrue(result["ok"])
        url, body = captured[0]
        self.assertEqual(url, VALID_URL)
        payload = json.loads(body)
        self.assertEqual(payload["msg_type"], "text")
        self.assertNotIn("sign", payload)

    def test_send_now_with_secret_signs(self):
        captured = []
        self._patch_urlopen(
            lambda req, timeout=None: (
                captured.append(req.data), FakeResponse(b'{"code":0}'))[1])
        self.store.set_setting(notify.SETTING_SECRET, "my-secret")
        result = self.notifier.send_now("hello")
        self.assertTrue(result["ok"])
        payload = json.loads(captured[0])
        self.assertIn("sign", payload)
        self.assertIn("timestamp", payload)
        expected = feishu_sign("my-secret", int(payload["timestamp"]))
        self.assertEqual(payload["sign"], expected)

    def test_send_now_error_code(self):
        self._patch_urlopen(lambda req, timeout=None: FakeResponse(
            b'{"code":19021,"msg":"sign match fail"}'))
        result = self.notifier.send_now("hello")
        self.assertFalse(result["ok"])
        self.assertIn("19021", result["error"])

    def test_send_now_network_error(self):
        def broken(req, timeout=None):
            raise OSError("connection refused")
        self._patch_urlopen(broken)
        result = self.notifier.send_now("hello")
        self.assertFalse(result["ok"])

    def test_send_now_without_url(self):
        self.store.set_setting(notify.SETTING_URL, "")
        result = PushNotifier(self.store).send_now("hello")
        self.assertFalse(result["ok"])
        self.assertIn("未配置", result["error"])


class WorkerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "n.sqlite3",
                           SecretBox(generate_master_key()))
        self.notifier = PushNotifier(self.store)
        self.store.set_setting(notify.SETTING_URL, VALID_URL)

    def tearDown(self):
        self.notifier.stop()
        self.store.close()
        self.tmp.cleanup()

    def test_worker_delivers_queued_item(self):
        captured = []
        original = urllib.request.urlopen
        urllib.request.urlopen = lambda req, timeout=None: (
            captured.append(json.loads(req.data)),
            FakeResponse(b'{"code":0}'))[1]
        try:
            self.notifier.start()
            ok = self.notifier.enqueue({
                "category": "告警", "display_name": "店铺A", "email": "a@example.invalid",
                "subject": "Account Health warning", "severity": "P0",
                "action_required": True, "subtype": "账号停用风险"})
            self.assertTrue(ok)
            deadline = time.time() + 5
            while not captured and time.time() < deadline:
                time.sleep(0.05)
        finally:
            urllib.request.urlopen = original
        self.assertEqual(len(captured), 1)
        self.assertIn("Account Health warning",
                      captured[0]["content"]["text"])


if __name__ == "__main__":
    unittest.main()
