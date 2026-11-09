"""Secrets are encrypted; the master key lives in a separate 0600 service-private file."""

import os

import httpx
from cryptography.fernet import Fernet

from .domain import event, fail, has


def cipher(settings):
    private = settings.storage_root.parent / "private"
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = private / "provider-master.key"
    if not path.exists():
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as out:
                out.write(Fernet.generate_key())
        except FileExistsError:
            pass
    return Fernet(path.read_bytes())


def status(c):
    rows = {
        r["provider"]: r
        for r in c.execute("SELECT provider,model,enabled,daily_limit,updated_at FROM provider_configs").fetchall()
    }
    checks = {
        r["provider"]: r["detail"]
        for r in c.execute(
            "SELECT DISTINCT ON (detail->>'provider') detail->>'provider' AS provider,detail FROM audit_events WHERE action='provider.check' ORDER BY detail->>'provider',created_at DESC"
        ).fetchall()
    }
    return [
        {
            **rows.get(p, {"provider": p, "model": "", "enabled": False, "daily_limit": 0}),
            "configured": p in rows,
            "last_check": checks.get(p),
        }
        for p in ("openai", "deepseek")
    ]


def check_connection(c, user, settings, provider):
    if not has(user, "admin"):
        fail("仅管理员可验证模型服务", 403)
    if provider not in ("openai", "deepseek"):
        fail("未知供应商", 422)
    c.execute("SELECT pg_advisory_xact_lock(58017006)")
    recent = c.execute(
        "SELECT 1 FROM audit_events WHERE action='provider.check' AND detail->>'provider'=%s AND created_at>now()-interval '10 seconds'",
        (provider,),
    ).fetchone()
    if recent:
        fail("请稍后再验证", 429)
    configured = runtime(c, settings, provider)
    key = getattr(configured, provider + "_api_key")
    model = getattr(configured, provider + "_model")
    if not key or not model:
        fail("请先保存密钥和模型名称", 422)
    url = (
        configured.openai_base_url.rstrip("/") + "/v1/models"
        if provider == "openai"
        else "https://api.deepseek.com/models"
    )
    try:
        response = httpx.get(url, headers={"Authorization": "Bearer " + key}, timeout=20)
        ok = response.status_code == 200 and model in [x.get("id") for x in response.json().get("data", [])]
        message = (
            "认证成功，所填模型可用；实际生成请在商品页验证"
            if ok
            else (
                "密钥有效，但模型名称不在可用模型列表中"
                if response.status_code == 200
                else "模型服务返回 HTTP " + str(response.status_code)
            )
        )
    except (httpx.HTTPError, ValueError):
        ok, message = False, "连接失败，请检查服务网络或供应商状态"
    result = {"provider": provider, "model": model, "ok": ok, "message": message}
    event(c, None, user, "provider.check", result)
    return result


def save(c, user, settings, provider, data):
    if not has(user, "admin"):
        fail("仅管理员可配置模型服务", 403)
    if provider not in ("openai", "deepseek"):
        fail("未知供应商", 422)
    old = c.execute("SELECT * FROM provider_configs WHERE provider=%s FOR UPDATE", (provider,)).fetchone()
    model = str(data.get("model", "")).strip()
    key = str(data.get("api_key", "")).strip()
    limit = data.get("daily_limit", old["daily_limit"] if old else 0)
    if not model or len(model) > 200 or type(limit) is not int or not 0 <= limit <= 10000:
        fail("请填写模型名称；每日调用上限为0（不限制）或1—10000", 422)
    if len(key) > 1000 or "\n" in key:
        fail("密钥格式无效", 422)
    if not key and not old:
        fail("首次配置需要填写API密钥", 422)
    encrypted = cipher(settings).encrypt(key.encode()).decode() if key else old["key_ciphertext"]
    c.execute(
        "INSERT INTO provider_configs(provider,key_ciphertext,model,enabled,daily_limit,updated_by) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(provider) DO UPDATE SET key_ciphertext=excluded.key_ciphertext,model=excluded.model,enabled=excluded.enabled,daily_limit=excluded.daily_limit,updated_by=excluded.updated_by,updated_at=now()",
        (provider, encrypted, model, bool(data.get("enabled")), limit, user["id"]),
    )
    event(
        c,
        None,
        user,
        "provider.configure",
        {
            "provider": provider,
            "model": model,
            "enabled": bool(data.get("enabled")),
            "key_changed": bool(key),
            "daily_limit": limit,
        },
    )
    return status(c)


def runtime(c, settings, provider):
    row = c.execute("SELECT * FROM provider_configs WHERE provider=%s", (provider,)).fetchone()
    if not row:
        return settings
    updated = settings.model_copy()
    updated.model_enabled = row["enabled"]
    setattr(updated, provider + "_api_key", cipher(settings).decrypt(row["key_ciphertext"].encode()).decode())
    setattr(updated, provider + "_model", row["model"])
    updated.model_daily_limit = row["daily_limit"]
    return updated
