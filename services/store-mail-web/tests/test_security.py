"""security 模块测试:限流与登录防爆破。"""

from __future__ import annotations

import unittest

from store_mail_web.security import (
    LoginThrottle, RateLimiter, generate_api_key, key_fingerprint,
)


class KeyFingerprintTest(unittest.TestCase):
    def test_stable_and_opaque(self):
        key = generate_api_key()
        self.assertEqual(key_fingerprint(key), key_fingerprint(key))
        self.assertNotIn(key, key_fingerprint(key))
        self.assertEqual(len(key_fingerprint(key)), 64)


class RateLimiterTest(unittest.TestCase):
    def test_allows_up_to_limit_then_denies(self):
        limiter = RateLimiter()
        for i in range(3):
            allowed, _ = limiter.allow(1, 3, now=1000.0)
            self.assertTrue(allowed, f"第 {i + 1} 次应放行")
        allowed, retry = limiter.allow(1, 3, now=1000.5)
        self.assertFalse(allowed)
        self.assertGreater(retry, 0)

    def test_window_slides(self):
        limiter = RateLimiter()
        for _ in range(3):
            limiter.allow(1, 3, now=1000.0)
        self.assertFalse(limiter.allow(1, 3, now=1001.0)[0])
        # 窗口(60s)过后恢复
        self.assertTrue(limiter.allow(1, 3, now=1061.0)[0])

    def test_keys_isolated(self):
        limiter = RateLimiter()
        for _ in range(3):
            limiter.allow(1, 3, now=1000.0)
        self.assertTrue(limiter.allow(2, 3, now=1000.0)[0])


class LoginThrottleTest(unittest.TestCase):
    def test_blocks_after_failures(self):
        throttle = LoginThrottle()
        ip, user = "1.2.3.4", "admin"
        for _ in range(5):
            self.assertFalse(throttle.blocked(ip, user, now=1000.0))
            throttle.record_failure(ip, user, now=1000.0)
        self.assertTrue(throttle.blocked(ip, user, now=1001.0))

    def test_reset(self):
        throttle = LoginThrottle()
        for _ in range(5):
            throttle.record_failure("1.1.1.1", "admin", now=1000.0)
        throttle.reset("1.1.1.1", "admin")
        self.assertFalse(throttle.blocked("1.1.1.1", "admin", now=1000.0))

    def test_other_ip_not_affected(self):
        throttle = LoginThrottle()
        for _ in range(5):
            throttle.record_failure("1.1.1.1", "admin", now=1000.0)
        self.assertFalse(throttle.blocked("2.2.2.2", "admin", now=1000.0))


if __name__ == "__main__":
    unittest.main()
