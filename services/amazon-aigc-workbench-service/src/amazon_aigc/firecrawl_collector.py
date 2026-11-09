"""Firecrawl Cloud v2 adapter. No model extraction or website logins are requested."""

import asyncio
import re
import time
from urllib.parse import urlsplit

import httpx

from .collector import CollectionError, parse_html, persist_result, safe_error, validate_url

PARSER_VERSION = "firecrawl-v2-amazon-0.1"
API_URL = "https://api.firecrawl.dev/v2/scrape"


def api_error(status):
    return {
        401: "Firecrawl认证失败，请管理员检查API Key",
        402: "Firecrawl额度不足，请管理员检查账户余额",
        403: "Firecrawl拒绝此页面的采集，请手动补充",
        408: "Firecrawl采集超时，请重试或手动补充",
        429: "Firecrawl请求限流，请稍后重试",
    }.get(status, "Firecrawl服务返回 HTTP " + str(status) + "，请重试或手动补充")


def parse_response(data, url):
    meta = data.get("metadata") or {}
    code = meta.get("statusCode")
    if isinstance(code, int) and code >= 400:
        raise CollectionError("来源网站返回 HTTP " + str(code) + "，请手动补充")
    final = str(meta.get("url") or meta.get("sourceURL") or url)
    validate_url(final, resolve=False)

    def identity(link):
        match = re.search(r"/offer/(\d+)\.html|/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)", link)
        return next((x for x in match.groups() if x), "") if match else ""

    if identity(url) and identity(final) != identity(url):
        raise CollectionError("网页跳转后的商品编号不一致，请检查链接")
    if "amazon." in (urlsplit(url).hostname or "") and urlsplit(url).hostname != urlsplit(final).hostname:
        raise CollectionError("网页跳转后的Amazon站点不一致，请检查链接")
    html = str(data.get("rawHtml") or data.get("html") or "")
    markdown = str(data.get("markdown") or "")
    result = parse_html(html, final)
    # A challenge/login shell must not become an apparently successful source.
    plain = " ".join([str(meta.get("title", "")), result["title"], markdown[:1500]])
    if re.search(
        r"captcha|robot check|access denied|just a moment|验证后继续访问|验证码|安全验证|请完成验证|访问被拒绝|登录后继续|请先登录",
        plain,
        re.IGNORECASE,
    ):
        raise CollectionError("来源网页要求登录或人工验证，Firecrawl未取得商品正文，请手动补充")
    if not result["title"]:
        result["title"] = str(meta.get("title") or "")[:500]
    if result["title"].strip().lower() in ("1688.com", "阿里巴巴", "login", "sign in", "amazon sign-in"):
        raise CollectionError("仅取得网站入口或登录页面，缺少商品正文，请手动补充")
    result["source_text"] = markdown[:16000]
    if not result["description"] and markdown:
        result["description"] = markdown[:12000]
    # Only page-discovered images; storage retains the existing CDN/network validation.
    images = data.get("images") or []
    result["images"] = list(dict.fromkeys(result["images"] + [x for x in images if isinstance(x, str)]))[:24]
    if not result["title"] and not (result["bullets"] or result["specifications"]):
        raise CollectionError("Firecrawl未读取到可识别的商品资料，请手动补充")
    result["status"] = "complete" if result["title"] and result["specifications"] and result["images"] else "partial"
    result["parser_version"] = PARSER_VERSION
    result["collector"] = "firecrawl"
    result["cache_mode"] = "fresh"
    return result, html


async def collect(url, storage, api_key):
    started = time.monotonic()
    diagnostic = {"collector": "firecrawl", "method": "firecrawl", "parser_version": PARSER_VERSION}
    try:
        validate_url(url, resolve=False)
        if not api_key:
            raise CollectionError("Firecrawl尚未配置服务端密钥")
        payload = {
            "url": url,
            "formats": ["rawHtml", "markdown", "images"],
            "onlyMainContent": True,
            "maxAge": 0,
            "timeout": 60000,
            "proxy": "auto",
        }
        # Locale matches each marketplace; the actual confirmed product facts remain separate.
        host = urlsplit(url).hostname or ""
        if host.endswith("amazon.ca"):
            payload["location"] = {"country": "CA", "languages": ["en-CA"]}
        elif host.endswith("amazon.com.au"):
            payload["location"] = {"country": "AU", "languages": ["en-AU"]}
        async with asyncio.timeout(100), httpx.AsyncClient(timeout=90, follow_redirects=False) as client:
            response = await client.post(API_URL, headers={"Authorization": "Bearer " + api_key}, json=payload)
        diagnostic["page_ms"] = round((time.monotonic() - started) * 1000)
        diagnostic["http_status"] = response.status_code
        request_id = response.headers.get("x-request-id", "")
        if re.fullmatch(r"[A-Za-z0-9_-]{1,200}", request_id):
            diagnostic["request_id"] = request_id
        if response.status_code != 200:
            raise CollectionError(api_error(response.status_code))
        if len(response.content) > 12_000_000:
            raise CollectionError("Firecrawl返回资料过大，请手动补充")
        body = response.json()
        if body.get("success") is not True or not isinstance(body.get("data"), dict):
            raise CollectionError("Firecrawl未成功返回商品资料，请重试或手动补充")
        result, html = parse_response(body["data"], url)
        result.update(diagnostic)
        async with asyncio.timeout(90):
            result = await persist_result(result, html, storage, "firecrawl")
        result["total_ms"] = round((time.monotonic() - started) * 1000)
        return result
    except Exception as e:  # noqa: BLE001 -- Never persist provider bodies, auth headers or keys.
        diagnostic["total_ms"] = round((time.monotonic() - started) * 1000)
        raise CollectionError(safe_error(e), diagnostic) from None
