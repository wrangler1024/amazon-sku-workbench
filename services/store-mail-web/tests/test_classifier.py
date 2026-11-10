"""classifier 模块测试:亚马逊识别、业务分类与金额提取。"""

from __future__ import annotations

import unittest

from store_mail_web.classifier import (
    DEFAULT_AMAZON_RULES, DEFAULT_CATEGORY_RULES, classify, extract_amount,
    is_amazon_mail, load_amazon_rules, load_category_rules,
)


class AmazonDetectTest(unittest.TestCase):
    def test_sender_domains(self):
        cases = [
            ("account@amazon.com", "Whatever", True),
            ("no-reply@sellercentral.amazon.co.uk", "x", True),
            ("alerts@sellingpartner.amazon.de", "x", True),
            ("phisher@evil-amazon-login.com", "Your account", True),  # 含 amazon 即命中,宽松设计
            ("foo@example.invalid", "Hello Amazon Seller", True),            # 主题兜底
            ("foo@example.invalid", "普通邮件", False),
        ]
        for sender, subject, expected in cases:
            self.assertEqual(
                is_amazon_mail(sender, subject), expected,
                f"sender={sender} subject={subject}")

    def test_custom_rules(self):
        rules = {"senders": ["only-this-domain.com"], "subject_keywords": []}
        self.assertTrue(is_amazon_mail("a@only-this-domain.com", "x", rules))
        self.assertFalse(is_amazon_mail("a@amazon.com", "x", rules))

    def test_load_amazon_rules_fallback(self):
        parsed = load_amazon_rules('{"senders": ["a"], "subject_keywords": ["b"]}')
        self.assertEqual(parsed, {"senders": ["a"], "subject_keywords": ["b"]})
        self.assertEqual(load_amazon_rules("not json"),
                         DEFAULT_AMAZON_RULES)
        self.assertEqual(load_amazon_rules(None), DEFAULT_AMAZON_RULES)


class ClassifyTest(unittest.TestCase):
    def test_categories(self):
        self.assertEqual(classify("Account Health warning", "", ""), ("告警", "高"))
        self.assertEqual(classify("侵权投诉 Intellectual Property", "", ""), ("侵权", "高"))
        self.assertEqual(classify("Performance Notification", "", ""), ("绩效", "中"))
        self.assertEqual(
            classify("Pay your outstanding seller account balance",
                     "donotreply@amazon.com",
                     "We tried to charge your credit card, charge was declined"),
            ("扣款", "中"))
        self.assertEqual(classify("信用卡扣费失败通知", "", "余额欠款待处理"),
                         ("扣款", "中"))
        self.assertEqual(classify("Your disbursement has been initiated",
                                  "payments@amazon.com", ""), ("回款", "中"))
        self.assertEqual(classify("结算回款到账通知", "", ""), ("回款", "中"))
        self.assertEqual(classify("", "shipment@fba", ""), ("货件", "中"))
        self.assertEqual(classify("Stranded inventory for ASIN B012345678", "", ""),
                         ("货件", "中"))
        self.assertEqual(classify("Weekly Newsletter - unsubscribe", "", ""), ("广告", "低"))
        self.assertEqual(classify("普通来信", "friend@x.com", "你好"), ("其他", "低"))

    def test_debit_before_payout(self):
        # 扣款排在回款前:同一封邮件同时含 charge 与 Disbursement 时归扣款
        category, _ = classify(
            "Pay your outstanding seller account balance", "",
            "charge was declined. Disbursements will be suspended")
        self.assertEqual(category, "扣款")

    def test_priority_order(self):
        # 告警优先于广告
        category, priority = classify("Warning: Account Health", "", "unsubscribe")
        self.assertEqual((category, priority), ("告警", "高"))
        # 绩效优先于回款(同一封邮件同时命中时)
        category, _ = classify("Performance Notification for disbursement", "", "")
        self.assertEqual(category, "绩效")

    def test_load_category_rules(self):
        parsed = load_category_rules('{"告警": ["XYZ"]}')
        self.assertEqual(parsed["告警"], ["XYZ"])
        # 自定义规则生效
        self.assertEqual(classify("XYZ happened", "", "", parsed), ("告警", "高"))
        # 保存的旧配置缺「回款」时自动回退默认关键词(合并语义)
        self.assertIn("回款", parsed)
        self.assertEqual(parsed["回款"], DEFAULT_CATEGORY_RULES["回款"])
        # 非法 JSON 回退默认(默认规则不含 XYZ)
        self.assertEqual(load_category_rules("bad json"),
                         load_category_rules(None))
        self.assertEqual(classify("XYZ happened", "", ""), ("其他", "低"))


class AmountExtractionTest(unittest.TestCase):
    def test_dollar(self):
        self.assertEqual(
            extract_amount("Your disbursement of $1,234.56 is on the way", ""),
            "$1,234.56")

    def test_currency_code(self):
        self.assertEqual(extract_amount("", "A disbursement of EUR 987.00 was sent"),
                         "EUR987.00")

    def test_subject_preferred(self):
        self.assertEqual(extract_amount("Settlement of GBP 12.50", "other 99.00 USD"),
                         "GBP12.50")

    def test_chinese_currency(self):
        self.assertEqual(extract_amount("", "回款 1,234.56 美元已到账"),
                         "1,234.56美元")

    def test_none(self):
        self.assertIsNone(extract_amount("普通主题", "没有金额"))
        self.assertIsNone(extract_amount("", ""))


if __name__ == "__main__":
    unittest.main()
