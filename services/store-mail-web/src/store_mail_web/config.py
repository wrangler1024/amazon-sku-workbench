"""配置加载。自实现 .env 解析(不依赖 python-dotenv)，照 finepack_hub 范式。

环境变量前缀 STORE_MAIL_WEB_。敏感字段用 field(repr=False) 屏蔽打印。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------- 路径计算 ----------

def service_root() -> Path:
    """本模块所在服务的根目录(含 pyproject.toml)。"""
    return Path(__file__).resolve().parents[2]


# ---------- .env 加载 ----------

def load_local_env(path: Optional[Path] = None) -> None:
    """逐行解析 .env，用 setdefault 不覆盖已显式设置的环境变量。"""
    env_path = path or (service_root() / ".env")
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _env_str(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} 必须是整数,实际: {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{key} 必须 > 0,实际: {value}")
    return value


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{key} 必须是布尔值(1/0/true/false),实际: {raw!r}")


# ---------- Settings ----------

@dataclass(frozen=True)
class Settings:
    """全部运行配置。"""

    host: str = "127.0.0.1"
    port: int = 8789

    secret_key: str = field(default="", repr=False)   # 主密钥(Fernet 格式)

    admin_user: str = "admin"
    admin_password: str = field(default="", repr=False)
    admin_password_hash: str = field(default="", repr=False)
    session_ttl_hours: int = 12

    imap_host: str = "imap.163.com"
    imap_port: int = 993
    imap_timeout: int = 30
    mark_seen_on_open: bool = True

    sync_interval_seconds: int = 180
    sync_jitter_seconds: int = 40
    sync_backoff_max_seconds: int = 1800
    initial_days: int = 30
    initial_limit: int = 200
    per_run_limit: int = 100
    manual_sync_min_gap_seconds: int = 30

    api_rate_per_min: int = 60

    # 飞书群机器人 Webhook 推送(env 兜底;正式配置在设置页,存 DB)
    feishu_webhook_url: str = field(default="", repr=False)
    feishu_webhook_secret: str = field(default="", repr=False)
    public_url: str = ""
    feishu_app_id: str = ""
    feishu_app_secret: str = field(default="", repr=False)
    feishu_tenant_key: str = ""

    db_path: Path = field(default_factory=lambda: service_root() / "var" / "store_mail_web.sqlite3")

    @classmethod
    def from_env(cls) -> "Settings":
        load_local_env()
        db_raw = _env_str("STORE_MAIL_WEB_DB_PATH")
        db_path = (
            Path(db_raw).expanduser().resolve()
            if db_raw
            else service_root() / "var" / "store_mail_web.sqlite3"
        )
        return cls(
            host=_env_str("STORE_MAIL_WEB_HOST", "127.0.0.1") or "127.0.0.1",
            port=_env_int("STORE_MAIL_WEB_PORT", 8789),
            secret_key=_env_str("STORE_MAIL_WEB_SECRET_KEY"),
            admin_user=_env_str("STORE_MAIL_WEB_ADMIN_USER", "admin") or "admin",
            admin_password=_env_str("STORE_MAIL_WEB_ADMIN_PASSWORD"),
            admin_password_hash=_env_str("STORE_MAIL_WEB_ADMIN_PASSWORD_HASH"),
            session_ttl_hours=_env_int("STORE_MAIL_WEB_SESSION_TTL_HOURS", 12),
            imap_host=_env_str("STORE_MAIL_WEB_IMAP_HOST", "imap.163.com") or "imap.163.com",
            imap_port=_env_int("STORE_MAIL_WEB_IMAP_PORT", 993),
            imap_timeout=_env_int("STORE_MAIL_WEB_IMAP_TIMEOUT", 30),
            mark_seen_on_open=_env_bool("STORE_MAIL_WEB_MARK_SEEN_ON_OPEN", True),
            sync_interval_seconds=_env_int("STORE_MAIL_WEB_SYNC_INTERVAL_SECONDS", 180),
            sync_jitter_seconds=_env_int("STORE_MAIL_WEB_SYNC_JITTER_SECONDS", 40),
            sync_backoff_max_seconds=_env_int("STORE_MAIL_WEB_SYNC_BACKOFF_MAX_SECONDS", 1800),
            initial_days=_env_int("STORE_MAIL_WEB_INITIAL_DAYS", 30),
            initial_limit=_env_int("STORE_MAIL_WEB_INITIAL_LIMIT", 200),
            per_run_limit=_env_int("STORE_MAIL_WEB_PER_RUN_LIMIT", 100),
            manual_sync_min_gap_seconds=_env_int("STORE_MAIL_WEB_MANUAL_SYNC_MIN_GAP_SECONDS", 30),
            api_rate_per_min=_env_int("STORE_MAIL_WEB_API_RATE_PER_MIN", 60),
            feishu_webhook_url=_env_str("STORE_MAIL_WEB_FEISHU_WEBHOOK_URL"),
            feishu_webhook_secret=_env_str("STORE_MAIL_WEB_FEISHU_WEBHOOK_SECRET"),
            public_url=_env_str("STORE_MAIL_WEB_PUBLIC_URL"),
            feishu_app_id=_env_str("STORE_MAIL_WEB_FEISHU_APP_ID"),
            feishu_app_secret=_env_str("STORE_MAIL_WEB_FEISHU_APP_SECRET"),
            feishu_tenant_key=_env_str("STORE_MAIL_WEB_FEISHU_TENANT_KEY"),
            db_path=db_path,
        )

    # ---------- 完整性检查 ----------

    def admin_password_configured(self) -> bool:
        return bool(self.admin_password or self.admin_password_hash)

    def require_secret_key(self) -> None:
        if not self.secret_key:
            raise ValueError(
                "缺少 STORE_MAIL_WEB_SECRET_KEY(主密钥)。"
                "运行 store-mail-web genkey 生成后写入 .env"
            )
