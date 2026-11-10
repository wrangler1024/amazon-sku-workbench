"""加密与凭据工具。

三类职责:
1. SecretBox —— 授权码等敏感字段加密存储(Fernet,来自 cryptography 包)
2. 口令哈希  —— 管理员密码 scrypt 哈希(stdlib hashlib)
3. 会话令牌  —— HMAC 签名的自包含 token(stdlib hmac)

主密钥只有一个(STORE_MAIL_WEB_SECRET_KEY,Fernet 格式 base64 32 字节),
派生出两个用途子密钥,避免同密钥跨用途复用。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Optional


class CryptoError(Exception):
    """加密/解密/签名失败。"""


class PasswordError(Exception):
    """口令格式或校验失败。"""


def generate_master_key() -> str:
    """生成 Fernet 兼容的主密钥(urlsafe base64 的 32 字节)。"""
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def _master_key_bytes(master_key: str) -> bytes:
    try:
        raw = base64.urlsafe_b64decode(master_key.encode("ascii"))
    except Exception as exc:
        raise CryptoError("主密钥不是合法的 base64") from exc
    if len(raw) != 32:
        raise CryptoError("主密钥必须解码为 32 字节(用 genkey 生成)")
    return raw


def _derive(master_key: str, purpose: bytes) -> bytes:
    """从主密钥派生用途子密钥(简单 HKDF-Extract/Expand 形态)。"""
    prk = hmac.new(_master_key_bytes(master_key), purpose, hashlib.sha256).digest()
    return prk


class SecretBox:
    """授权码加密盒。一个实例绑定一个主密钥。"""

    def __init__(self, master_key: str):
        if not master_key:
            raise CryptoError("主密钥为空")
        # 校验格式,顺便确认 cryptography 可用
        _master_key_bytes(master_key)
        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:  # pragma: no cover - 环境问题
            raise CryptoError(
                "缺少 cryptography 包。安装:pip install cryptography "
                "或 apt install python3-cryptography"
            ) from exc
        self._fernet = Fernet(master_key.encode("ascii"))

    def encrypt(self, plaintext: str) -> str:
        if not isinstance(plaintext, str):
            raise CryptoError("待加密内容必须是字符串")
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        from cryptography.fernet import InvalidToken
        try:
            return self._fernet.decrypt(token.encode("ascii"), ttl=None).decode("utf-8")
        except InvalidToken as exc:
            raise CryptoError("解密失败:密文损坏或主密钥不匹配") from exc


# ---------- 口令哈希(pbkdf2_sha256,stdlib 全平台可用) ----------

PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    """生成口令哈希,格式 pbkdf2_sha256$iterations$salt_b64$hash_b64。

    用 pbkdf2 而不是 scrypt:macOS 系统 Python 的 OpenSSL 可能不带
    scrypt 支持,pbkdf2_sha256 在所有 stdlib 上可用。
    """
    if not password:
        raise PasswordError("口令不能为空")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt,
        PBKDF2_ITERATIONS, dklen=32,
    )
    return "$".join([
        "pbkdf2_sha256", str(PBKDF2_ITERATIONS),
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    ])


def verify_password(password: str, stored: str) -> bool:
    """恒定时间比较校验口令。stored 非法格式直接返回 False。"""
    try:
        scheme, iters_raw, salt_b64, hash_b64 = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iters_raw)
        if not (1 <= iterations <= 10_000_000):
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt,
        iterations, dklen=len(expected),
    )
    return hmac.compare_digest(digest, expected)


# ---------- 会话令牌(HMAC 签名) ----------

class SessionTokens:
    """自包含会话令牌:payload_b64.hmac_b64。

    payload 为 JSON {u: 用户名, uid: 用户ID, role: 角色, iat, exp}。
    """

    def __init__(self, master_key: str, ttl_seconds: int):
        self._key = _derive(master_key, b"store_mail_web/session/v1")
        self._ttl = ttl_seconds

    def issue(self, username: str, now: Optional[float] = None,
              uid: int = 0, role: str = "admin") -> str:
        issued = int(now if now is not None else time.time())
        payload = json.dumps(
            {"u": username, "uid": uid, "role": role,
             "iat": issued, "exp": issued + self._ttl, "nonce": secrets.token_urlsafe(16)},
            separators=(",", ":"),
        ).encode("utf-8")
        body = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        sig = base64.urlsafe_b64encode(
            hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest()
        ).decode("ascii").rstrip("=")
        return f"{body}.{sig}"

    def verify(self, token: str, now: Optional[float] = None) -> dict[str, Any]:
        """校验签名与有效期。失败抛 CryptoError。"""
        if not token or "." not in token:
            raise CryptoError("会话令牌格式非法")
        body, _, sig = token.rpartition(".")
        expected = base64.urlsafe_b64encode(
            hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest()
        ).decode("ascii").rstrip("=")
        if not hmac.compare_digest(sig, expected):
            raise CryptoError("会话令牌签名不匹配")
        try:
            padded = body + "=" * (-len(body) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        except Exception as exc:
            raise CryptoError("会话令牌载荷解析失败") from exc
        current = int(now if now is not None else time.time())
        if not isinstance(payload.get("exp"), int) or payload["exp"] < current:
            raise CryptoError("会话已过期")
        if not isinstance(payload.get("u"), str) or not payload["u"]:
            raise CryptoError("会话载荷非法")
        if not isinstance(payload.get("uid"), int):
            raise CryptoError("会话载荷缺少用户标识")
        return payload


# ---------- 随机凭据 ----------

def generate_api_key() -> str:
    """生成 Agent API Key:smw_ + 24 字节随机 urlsafe。"""
    return "smw_" + secrets.token_urlsafe(24)
