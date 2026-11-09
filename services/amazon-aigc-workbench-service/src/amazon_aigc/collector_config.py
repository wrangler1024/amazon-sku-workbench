"""Collection providers share a server-only encryption and daily reservation boundary."""

import httpx

from .collector import CollectionError
from .domain import event, fail, has
from .provider_config import cipher

PROVIDERS = {"tmapi": "TMAPI", "firecrawl": "Firecrawl"}


def status(c, provider=None):
    if provider is not None and provider not in PROVIDERS:
        fail("未知采集服务", 422)
    if provider is None:
        latest = c.execute(
            "SELECT provider FROM collector_configs ORDER BY enabled DESC,updated_at DESC LIMIT 1"
        ).fetchone()
        provider = latest["provider"] if latest else "tmapi"
    row = c.execute(
        "SELECT enabled,daily_limit,updated_at FROM collector_configs WHERE provider=%s", (provider,)
    ).fetchone()
    check = c.execute(
        "SELECT detail FROM audit_events WHERE action='collector.check' AND coalesce(detail->>'provider','firecrawl')=%s ORDER BY created_at DESC LIMIT 1",
        (provider,),
    ).fetchone()
    return {
        "configured": bool(row),
        "config_provider": provider,
        "daily_limit": row["daily_limit"] if row else 100,
        "enabled": bool(row and row["enabled"]),
        "provider": provider if row and row["enabled"] else "native",
        "updated_at": row["updated_at"] if row else None,
        "last_check": check["detail"] if check else None,
    }


def key(c, settings, provider="firecrawl"):
    if provider not in PROVIDERS:
        raise CollectionError("未知采集服务")
    row = c.execute("SELECT key_ciphertext FROM collector_configs WHERE provider=%s", (provider,)).fetchone()
    if not row:
        raise CollectionError(PROVIDERS[provider] + "尚未配置服务端密钥")
    return cipher(settings).decrypt(row["key_ciphertext"].encode()).decode()


def save(c, user, settings, data):
    if not has(user, "admin"):
        fail("仅管理员可配置采集服务", 403)
    provider = data.get("provider", "firecrawl")
    if provider not in PROVIDERS:
        fail("未知采集服务", 422)
    c.execute("SELECT pg_advisory_xact_lock(58017007)")
    limit = data.get("daily_limit", 100)
    enabled = data.get("enabled", False)
    if type(limit) is not int or not 1 <= limit <= 10000 or type(enabled) is not bool:
        fail("采集上限须为1—10000的整数", 422)
    old = c.execute("SELECT * FROM collector_configs WHERE provider=%s FOR UPDATE", (provider,)).fetchone()
    value = str(data.get("api_key", "")).strip()
    if not value and not old:
        fail("首次配置需要填写API Key", 422)
    if value and (
        len(value) > 1000
        or len(value) < 8
        or any(x.isspace() for x in value)
        or (provider == "firecrawl" and not value.startswith("fc-"))
    ):
        fail("采集密钥格式无效", 422)
    encrypted = cipher(settings).encrypt(value.encode()).decode() if value else old["key_ciphertext"]
    if enabled:
        c.execute("UPDATE collector_configs SET enabled=false WHERE provider<>%s", (provider,))
    c.execute(
        "INSERT INTO collector_configs(provider,key_ciphertext,enabled,daily_limit,updated_by) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(provider) DO UPDATE SET key_ciphertext=excluded.key_ciphertext,enabled=excluded.enabled,daily_limit=excluded.daily_limit,updated_by=excluded.updated_by,updated_at=now()",
        (provider, encrypted, enabled, limit, user["id"]),
    )
    event(
        c,
        None,
        user,
        "collector.configure",
        {"provider": provider, "enabled": enabled, "key_changed": bool(value), "daily_limit": limit},
    )
    return status(c, provider)


def check(c, user, settings, provider=None):
    if not has(user, "admin"):
        fail("仅管理员可验证采集服务", 403)
    provider = provider or status(c)["config_provider"]
    value = key(c, settings, provider)
    name = PROVIDERS[provider]
    result = {"provider": provider, "ok": False, "message": name + "连接失败，请检查服务网络"}
    try:
        if provider == "tmapi":
            response = httpx.get(
                "https://api.tmapi.top/1688/item_detail",
                params={"item_id": "900000000002", "language": "zh"},
                headers={"apikey": value},
                timeout=30,
                follow_redirects=False,
            )
            body = response.json() if response.status_code == 200 else {}
            ok = (
                response.status_code == 200
                and body.get("code") == 200
                and str((body.get("data") or {}).get("item_id")) == "900000000002"
            )
            result.update(
                ok=ok,
                message="TMAPI认证及1688测试读取成功"
                if ok
                else "TMAPI验证失败，HTTP " + str(response.status_code) + "；请检查密钥及套餐",
            )
        else:
            response = httpx.get(
                "https://api.firecrawl.dev/v2/team/credit-usage",
                headers={"Authorization": "Bearer " + value},
                timeout=20,
                follow_redirects=False,
            )
            body = response.json() if response.status_code == 200 else {}
            ok = response.status_code == 200 and body.get("success") is True
            balance = (body.get("data") or {}).get("remainingCredits")
            result.update(
                ok=ok,
                message="Firecrawl认证成功" if ok else "Firecrawl验证失败，HTTP " + str(response.status_code),
                remaining_credits=balance if isinstance(balance, (int, float)) else None,
            )
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        pass
    event(c, None, user, "collector.check", result)
    return result


def reserve(c, settings, job):
    provider = job.get("payload", {}).get("collector", "firecrawl")
    if provider not in PROVIDERS:
        raise CollectionError("未知采集服务")
    c.execute("SELECT pg_advisory_xact_lock(58017007)")
    row = c.execute("SELECT enabled,daily_limit FROM collector_configs WHERE provider=%s", (provider,)).fetchone()
    if not row or not row["enabled"]:
        raise CollectionError(PROVIDERS[provider] + "未启用，请管理员检查采集配置")
    units = 2 if provider == "tmapi" and "1688.com" in job.get("payload", {}).get("url", "") else 1
    count = c.execute(
        "SELECT coalesce(sum(coalesce((detail->>'units')::int,1)),0) AS n FROM audit_events WHERE action='collector.request' AND coalesce(detail->>'provider','firecrawl')=%s AND created_at >= (date_trunc('day',now() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai')",
        (provider,),
    ).fetchone()["n"]
    if count + units > row["daily_limit"]:
        raise CollectionError("今日" + PROVIDERS[provider] + "采集请求已达上限，请管理员调整或手动补充")
    value = key(c, settings, provider)
    event(
        c,
        job["work_item_id"],
        {"id": job["actor_id"]},
        "collector.request",
        {"provider": provider, "job_id": str(job["id"]), "units": units},
    )
    return value
