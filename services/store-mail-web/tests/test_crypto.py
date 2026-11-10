"""crypto 模块测试:加密盒、口令哈希、会话令牌。"""

from __future__ import annotations

import unittest

try:
    from cryptography.fernet import Fernet
    HAS_CRYPTO = True
except ImportError:  # pragma: no cover
    HAS_CRYPTO = False

from store_mail_web.crypto import (
    CryptoError, PasswordError, SecretBox, SessionTokens,
    generate_api_key, generate_master_key, hash_password, verify_password,
)


@unittest.skipUnless(HAS_CRYPTO, "cryptography 未安装")
class SecretBoxTest(unittest.TestCase):
    def test_roundtrip(self):
        box = SecretBox(generate_master_key())
        token = box.encrypt("我的163授权码ABC123")
        self.assertNotIn("ABC123", token)
        self.assertEqual(box.decrypt(token), "我的163授权码ABC123")

    def test_wrong_key_fails(self):
        token = SecretBox(generate_master_key()).encrypt("secret")
        with self.assertRaises(CryptoError):
            SecretBox(generate_master_key()).decrypt(token)

    def test_invalid_master_key(self):
        with self.assertRaises(CryptoError):
            SecretBox("not-a-key")
        with self.assertRaises(CryptoError):
            SecretBox("")
        with self.assertRaises(CryptoError):
            SecretBox(Fernet.generate_key().decode()[:20])

    def test_generated_key_usable(self):
        key = generate_master_key()
        self.assertEqual(len(key), 44)
        SecretBox(key)  # 不抛即合法


@unittest.skipUnless(HAS_CRYPTO, "cryptography 未安装")
class PasswordTest(unittest.TestCase):
    def test_hash_and_verify(self):
        stored = hash_password("正确密码123")
        self.assertNotIn("正确密码123", stored)
        self.assertTrue(verify_password("正确密码123", stored))
        self.assertFalse(verify_password("错误密码", stored))

    def test_empty_password_rejected(self):
        with self.assertRaises(PasswordError):
            hash_password("")

    def test_malformed_stored(self):
        self.assertFalse(verify_password("x", "not-a-hash"))
        self.assertFalse(verify_password("x", "scrypt$1$2$3$4$5"))
        self.assertFalse(verify_password("x", ""))


@unittest.skipUnless(HAS_CRYPTO, "cryptography 未安装")
class SessionTokenTest(unittest.TestCase):
    KEY = "x" * 43 + "="

    def setUp(self):
        self.key = generate_master_key()

    def test_issue_and_verify(self):
        tokens = SessionTokens(self.key, ttl_seconds=3600)
        token = tokens.issue("admin")
        payload = tokens.verify(token)
        self.assertEqual(payload["u"], "admin")
        self.assertGreater(payload["exp"], payload["iat"])

    def test_expired(self):
        tokens = SessionTokens(self.key, ttl_seconds=10)
        token = tokens.issue("admin", now=1000.0)
        with self.assertRaises(CryptoError):
            tokens.verify(token, now=2000.0)

    def test_tampered_payload(self):
        tokens = SessionTokens(self.key, ttl_seconds=3600)
        token = tokens.issue("admin")
        body, _, sig = token.rpartition(".")
        fake = body[:-1] + ("A" if body[-1] != "A" else "B")
        with self.assertRaises(CryptoError):
            tokens.verify(f"{fake}.{sig}")

    def test_wrong_key(self):
        token = SessionTokens(self.key, ttl_seconds=3600).issue("admin")
        with self.assertRaises(CryptoError):
            SessionTokens(generate_master_key(), ttl_seconds=3600).verify(token)


class ApiKeyGeneratorTest(unittest.TestCase):
    def test_format(self):
        key = generate_api_key()
        self.assertTrue(key.startswith("smw_"))
        self.assertEqual(len(key), len("smw_") + 32)


if __name__ == "__main__":
    unittest.main()
