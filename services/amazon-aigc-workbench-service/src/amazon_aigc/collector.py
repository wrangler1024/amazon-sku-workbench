"""Anonymous site collection. Extracted page content is untrusted reference material."""

import asyncio
import hashlib
import io
import ipaddress
import json
import re
import socket
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx
from bs4 import BeautifulSoup
from PIL import Image

PARSER_VERSION = "1688-amazon-0.2"

AMAZON = [
    "amazon.com",
    "amazon.ca",
    "amazon.co.uk",
    "amazon.com.au",
    "amazon.com.mx",
    "amazon.ae",
    "amazon.sa",
    "amazon.co.jp",
]
PAGE_DOMAINS = ["1688.com", *AMAZON]
RESOURCE_DOMAINS = [
    *PAGE_DOMAINS,
    "alicdn.com",
    "alibaba.com",
    "aliexpress.com",
    "a.aliimg.com",
    "ssl-images-amazon.com",
    "media-amazon.com",
    "amazon-adsystem.com",
]


class CollectionError(ValueError):
    def __init__(self, message, diagnostic=None):
        super().__init__(message)
        self.diagnostic = diagnostic or {}


def validate_url(url, resource=False, resolve=True):
    p = urlsplit(url)
    host = (p.hostname or "").lower()
    if p.scheme != "https" or p.username or p.password or p.port not in (None, 443):
        raise CollectionError("只支持HTTPS商品链接")
    allowed = RESOURCE_DOMAINS if resource else PAGE_DOMAINS
    if not any(host == d or host.endswith("." + d) for d in allowed):
        raise CollectionError("不支持的货源或竞品域名")
    if resolve:
        addresses = {r[4][0] for r in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
            raise CollectionError("目标网络地址不可用")
    return url


async def fetch_bytes(url, resource=False, limit=3_000_000):
    async with httpx.AsyncClient(timeout=25, follow_redirects=False, trust_env=False) as client:
        for hop in range(5):
            try:
                await asyncio.to_thread(validate_url, url, resource)
            except CollectionError:
                if hop:
                    host = (urlsplit(url).hostname or "未知域名")[:100]
                    raise CollectionError("网页跳转到受限域名 " + host + "，无法匿名读取，请手动补充") from None
                raise
            async with client.stream(
                "GET",
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; AmazonWorkbench/0.1)",
                    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.7",
                },
            ) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, r.headers.get("location", ""))
                    continue
                if r.status_code in (401, 403, 429):
                    raise CollectionError("页面访问受限或请求过于频繁")
                if r.status_code >= 400:
                    raise CollectionError("页面返回 HTTP " + str(r.status_code) + "，请检查链接或手动补充")
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > limit:
                        raise CollectionError("来源文件超出大小限制")
                return bytes(buf), str(r.url), r.headers.get("content-type", "")
        raise CollectionError("页面重定向次数过多")


async def render(url):
    from playwright.async_api import async_playwright

    await asyncio.to_thread(validate_url, url)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        try:
            context = await browser.new_context(service_workers="block", accept_downloads=False)

            async def route(request_route):
                try:
                    if request_route.request.resource_type in ("font", "media"):
                        return await request_route.abort()
                    await asyncio.to_thread(validate_url, request_route.request.url, True)
                    await request_route.continue_()
                except Exception:  # noqa: BLE001 -- Fail closed for untrusted external resources.
                    await request_route.abort()

            await context.route("**/*", route)
            page = await context.new_page()
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if response and response.status in (401, 403, 429):
                raise CollectionError("动态页面访问受限")
            try:
                await page.wait_for_selector("#productTitle, h1, [class*=title]", timeout=6000)
            except Exception:  # noqa: BLE001, S110 -- Missing title is an expected partial page.
                pass
            html = await page.content()
            if len(html) > 3_000_000:
                raise CollectionError("动态页面超出大小限制")
            try:
                await asyncio.to_thread(validate_url, page.url)
            except CollectionError:
                host = (urlsplit(page.url).hostname or "未知域名")[:100]
                raise CollectionError("动态网页跳转到受限域名 " + host + "，无法匿名读取，请手动补充") from None
            return html, page.url
        finally:
            await browser.close()


def parse_html(html, url):
    soup = BeautifulSoup(html, "html.parser")
    plain = soup.get_text(" ", strip=True)
    if re.search(r"enter the characters you see|sorry.*robot|验证后继续访问|验证码|访问被拒绝", plain, re.IGNORECASE):
        raise CollectionError("需要验证码或人工验证")
    if ("login.1688.com" in url or "/ap/signin" in url) and not soup.select_one("#productTitle"):
        raise CollectionError("需要登录后读取")
    product = {}
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            obj = json.loads(script.string or script.get_text())
            objs = obj if isinstance(obj, list) else [obj]
            for o in list(objs):
                if isinstance(o, dict):
                    objs.extend(o.get("@graph", []))
            product = next(
                (
                    o
                    for o in objs
                    if isinstance(o, dict)
                    and "Product" in ([o.get("@type")] if isinstance(o.get("@type"), str) else o.get("@type", []))
                ),
                product,
            )
        except (ValueError, TypeError):
            pass
    title = soup.select_one("#productTitle") or soup.select_one("h1")
    title = str(product.get("name") or (title.get_text(" ", strip=True) if title else ""))[:500]
    specs = [
        x.get_text(" ", strip=True)
        for x in soup.select(
            "#productDetails_techSpec_section_1 tr, #productDetails_detailBullets_sections1 tr, #detailBullets_feature_div li, .offer-attr-item, [class*=specification]"
        )
    ][:60]
    bullets = [x.get_text(" ", strip=True) for x in soup.select("#feature-bullets li") if x.get_text(strip=True)][:10]
    images = []
    for x in product.get("image", []) if isinstance(product.get("image"), list) else [product.get("image")]:
        if isinstance(x, str):
            images.append(x)
    for img in soup.select("#landingImage, #imgTagWrapperId img, [class*=gallery] img, [class*=detail] img"):
        src = img.get("data-old-hires") or img.get("data-src") or img.get("src")
        if src:
            images.append(urljoin(url, src))
        try:
            images.extend(json.loads(img.get("data-a-dynamic-image", "{}")).keys())
        except (ValueError, AttributeError):
            pass
    meta = soup.select_one('meta[property="og:image"]')
    if meta and meta.get("content"):
        images.append(urljoin(url, meta["content"]))
    description = product.get("description") or ""
    if not description:
        el = soup.select_one("#productDescription")
        description = el.get_text(" ", strip=True) if el else ""
    return {
        "title": title,
        "specifications": specs,
        "bullets": bullets,
        "description": str(description)[:20000],
        "images": list(dict.fromkeys(images))[:24],
        "source_url": url,
        "variant_verified": False,
        "status": "complete" if title and specs and images else "partial",
        "parser_version": PARSER_VERSION,
    }


def safe_error(error):
    if isinstance(error, CollectionError):
        return str(error)[:200]
    if isinstance(error, (TimeoutError, httpx.TimeoutException)) or "Timeout" in type(error).__name__:
        return "读取超时，请重试或手动补充"
    if isinstance(error, (httpx.ConnectError, socket.gaierror)):
        return "无法连接来源网站，请稍后重试或手动补充"
    return "网页读取失败（" + type(error).__name__ + "），请手动补充"


async def collect(url, storage: Path, render_enabled=True):
    started = time.monotonic()
    try:
        async with asyncio.timeout(150):
            result = await _collect(url, storage, render_enabled)
            result["collector"] = "native"
            result["total_ms"] = round((time.monotonic() - started) * 1000)
            return result
    except Exception as e:  # noqa: BLE001 -- Sanitize all external site and browser failures.
        raise CollectionError(
            safe_error(e), {"collector": "native", "total_ms": round((time.monotonic() - started) * 1000)}
        ) from None


async def _collect(url, storage: Path, render_enabled=True):
    started = time.monotonic()
    raw, final, _ = await fetch_bytes(url)
    html = raw.decode("utf-8", errors="replace")
    method = "http"
    result = parse_html(html, final)
    if render_enabled and result["status"] != "complete":
        try:
            html, final = await render(url)
            result = parse_html(html, final)
            method = "browser"
        except Exception as e:  # noqa: BLE001 -- Isolate job/resource failures and return a sanitized result.
            result["render_error"] = safe_error(e)
    if not any(result.get(k) for k in ("title", "specifications", "description", "images")):
        raise CollectionError(result.get("render_error") or "未读取到有效商品资料，请手动录入规格、数量和包含物")
    result["page_ms"] = round((time.monotonic() - started) * 1000)
    return await persist_result(result, html, storage, method)


async def persist_result(result, html, storage, method, filename="page.html"):
    started = time.monotonic()
    folder = storage / "sources" / str(uuid4())
    folder.mkdir(parents=True, exist_ok=True)
    snapshot = folder / filename
    snapshot.write_text(html)
    downloads = []
    image_deadline = asyncio.get_running_loop().time() + 45
    for image_url in result["images"][:12]:
        if asyncio.get_running_loop().time() >= image_deadline:
            result["render_error"] = "部分图片读取超时，已保留取得的文字资料"
            break
        try:
            data, image_final, _ = await fetch_bytes(image_url, True, 20_000_000)
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                mime = Image.MIME.get(image.format, "")
            if mime not in ("image/jpeg", "image/png", "image/webp"):
                raise CollectionError("不支持的图片格式")
            key = "sources/" + folder.name + "/" + str(uuid4())
            (storage / key).write_bytes(data)
            downloads.append(
                {
                    "key": key,
                    "url": image_final,
                    "width": width,
                    "height": height,
                    "mime": mime,
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "quality": "low" if min(width, height) < 500 else "available",
                }
            )
        except Exception as e:  # noqa: BLE001 -- Isolate job/resource failures and return a sanitized result.
            downloads.append({"url": image_url, "error": safe_error(e)})
    result["downloads"] = downloads
    if not any(x.get("key") for x in downloads):
        result["status"] = "partial"
    result["method"] = method
    result["snapshot_key"] = str(snapshot.relative_to(storage))
    result["download_ms"] = round((time.monotonic() - started) * 1000)
    return result
