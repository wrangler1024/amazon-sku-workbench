"""API Key 指纹与限流、登录防爆破。全部进程内存态(单实例部署)。"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import defaultdict, deque
from typing import Optional

from .crypto import generate_api_key


def key_fingerprint(plaintext_key: str) -> str:
    """API Key 的存储指纹(sha256 hex)。库被拖也无法还原 Key 明文。"""
    return hashlib.sha256(plaintext_key.encode("utf-8")).hexdigest()


class RateLimiter:
    """滑动窗口限流。按 key_id 隔离,窗口=60 秒。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, key_id: int, limit_per_min: int,
              now: Optional[float] = None) -> tuple[bool, int]:
        """返回 (是否放行, 建议重试等待秒数)。"""
        current = now if now is not None else time.time()
        with self._lock:
            hits = self._hits[key_id]
            while hits and hits[0] <= current - 60:
                hits.popleft()
            if len(hits) >= limit_per_min:
                retry_after = max(1, int(hits[0] + 60 - current) + 1)
                return False, retry_after
            hits.append(current)
            # 顺手清理:防止废弃 key 的字典无限膨胀
            if len(self._hits) > 4096:
                stale = [kid for kid, dq in self._hits.items()
                         if not dq or dq[-1] <= current - 600]
                for kid in stale:
                    del self._hits[kid]
            return True, 0


class LoginThrottle:
    """登录失败防爆破:同一 (ip, 用户名) 10 分钟内失败 5 次则临时封禁。"""

    WINDOW_SECONDS = 600
    MAX_FAILURES = 5

    def __init__(self):
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _key(self, ip: str, username: str) -> str:
        return f"{ip}|{username}"

    def blocked(self, ip: str, username: str,
                now: Optional[float] = None) -> bool:
        current = now if now is not None else time.time()
        with self._lock:
            hits = self._failures[self._key(ip, username)]
            while hits and hits[0] <= current - self.WINDOW_SECONDS:
                hits.popleft()
            return len(hits) >= self.MAX_FAILURES

    def record_failure(self, ip: str, username: str,
                       now: Optional[float] = None) -> None:
        current = now if now is not None else time.time()
        with self._lock:
            self._failures[self._key(ip, username)].append(current)

    def reset(self, ip: str, username: str) -> None:
        with self._lock:
            self._failures.pop(self._key(ip, username), None)


__all__ = [
    "generate_api_key",
    "key_fingerprint",
    "RateLimiter",
    "LoginThrottle",
]
