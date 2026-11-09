"""TMAPI structured product acquisition. Source variants remain unconfirmed references."""

import asyncio
import html as html_lib
import json
import re
import time
from urllib.parse import parse_qs, urlsplit

import httpx
from bs4 import BeautifulSoup

from .collector import CollectionError, persist_result, safe_error, validate_url
from .source_measurements import measurement_projection

PARSER_VERSION = "tmapi-1688-amazon-3"
API_ROOT = "https://api.tmapi.top"


def identify(url):
    validate_url(url, resolve=False)
    host = (urlsplit(url).hostname or "").lower()
    path = urlsplit(url).path
    if host == "1688.com" or host.endswith(".1688.com"):
        match = re.search(r"/offer/(\d+)\.html", path)
        platform = "1688"
    else:
        match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:/|$)", path, re.IGNORECASE)
        platform = "amazon"
    if not match:
        raise CollectionError("请填写含商品编号的1688或Amazon商品详情链接")
    return platform, match[1].upper()


def clean(value, limit=20000):
    return BeautifulSoup(str(value or ""), "html.parser").get_text(" ", strip=True)[:limit]


def image_urls(values):
    result = []
    for value in values or []:
        if not isinstance(value, str):
            continue
        value = html_lib.unescape(value)
        if value.startswith("//"):
            value = "https:" + value
        elif value.startswith("http://"):
            value = "https://" + value[7:]
        try:
            validate_url(value, resource=True, resolve=False)
        except ValueError:
            continue
        if value not in result:
            result.append(value)
    return result


def properties(values):
    result = []
    for value in values or []:
        if not isinstance(value, dict):
            continue
        pairs = [(value["name"], value.get("value", ""))] if "name" in value else list(value.items())
        for k, v in pairs:
            if v not in ("", None):
                result.append(clean(k, 100) + ": " + clean(v, 1000))
    return result[:80]


def normalize(data, url, description=None, description_error=""):
    platform, expected = identify(url)
    if not isinstance(data, dict):
        raise CollectionError("TMAPI未返回有效商品对象")
    actual = str(data.get("item_id") or data.get("asin") or "").upper()
    if actual != expected:
        raise CollectionError("TMAPI返回的商品编号与请求不一致，请检查链接")
    if platform == "amazon" and data.get("asin") and str(data["asin"]).upper() != expected:
        raise CollectionError("TMAPI返回的ASIN与请求不一致")
    returned = data.get("product_url") or data.get("url")
    if returned:
        returned = re.sub(r"^http:", "https:", str(returned))
        if identify(returned) != (platform, expected):
            raise CollectionError("TMAPI返回的链接与请求商品不一致")
        if platform == "amazon" and (urlsplit(returned).hostname or "").removeprefix("www.") != (
            urlsplit(url).hostname or ""
        ).removeprefix("www."):
            raise CollectionError("TMAPI返回的Amazon站点与请求不一致")
    title = clean(data.get("title"), 500)
    if not title:
        raise CollectionError("TMAPI商品标题为空，请重试或手动补充")
    props = properties(data.get("product_props"))
    bullets = [clean(x, 3000) for x in (data.get("highlights") or []) if isinstance(x, str) and x.strip()][:10]
    desc = clean(data.get("desc"))
    all_main = image_urls(data.get("main_imgs"))
    variants = []
    variant_images = []
    value_images = {}
    for prop in data.get("sku_props") or []:
        for value in prop.get("values") or []:
            imgs = image_urls([value.get("imageUrl", "")])
            variant_images += imgs
            value_images[str(prop.get("pid")) + ":" + str(value.get("vid"))] = imgs
    for sku in data.get("skus") or []:
        imgs = []
        for token in str(sku.get("props_ids", "")).split(";"):
            imgs += value_images.get(token, [])
        variants.append(
            {
                "sku_id": str(sku.get("skuid") or ""),
                "reference_price": str(sku.get("sale_price") or ""),
                "currency": str(data.get("currency") or ("CNY" if platform == "1688" else "")),
                "stock": sku.get("stock") if isinstance(sku.get("stock"), (int, float)) else None,
                "specification": clean(sku.get("props_names"), 1200),
                "images": list(dict.fromkeys(imgs)),
            }
        )
    warnings = []
    selected = []
    detail_images = []
    if platform == "1688":
        query = parse_qs(urlsplit(url).query)
        requested = next((query[k][0] for k in ("skuId", "sku_id") if query.get(k)), None)
        if requested:
            selected = [v for v in variants if v["sku_id"] == requested]
            if not selected:
                raise CollectionError("货源链接中的SKU编号不在返回规格中，请重新核对")
        # Do not mix every colour/style image into the product's reference pack.
        main = [u for u in all_main if u not in set(variant_images)]
        images = [u for v in selected for u in v["images"]] + main
        if isinstance(description, dict):
            if str(description.get("item_id", "")) != expected:
                raise CollectionError("详情图商品编号与货源不一致")
            detail_images = image_urls(description.get("detail_imgs"))
            desc = clean(description.get("detail_html"))
        images = image_urls(images + detail_images)
        if variants and not selected:
            warnings.append(
                f"货源含{len(variants)}个SKU，尚未绑定具体供应规格；已剔除各规格专属图片，通用参考及详情图仍需核对"
            )
        if description_error:
            warnings.append("详情图读取失败：" + description_error)
        if not images:
            warnings.append("未取得可安全归类的通用图片，请在原货源核对具体规格素材")
    else:
        images = all_main
        if not desc:
            warnings.append("竞品未返回长描述，已保留标题、属性和五点")
    result = {
        "title": title,
        "specifications": props,
        "bullets": bullets,
        "description": desc,
        "source_text": "\n".join([title, *props, *bullets, desc])[:20000],
        "images": images[:24],
        "source_url": url,
        "variant_verified": False,
        "variants": variants,
        "variant_count": len(variants),
        "offer_unit": str(data.get("offer_unit") or "件"),
        "selected_variants": selected,
        "variant_images_excluded": len(set(variant_images)) if platform == "1688" and not selected else 0,
        "main_image_count": len(all_main),
        "detail_image_count": len(detail_images),
        "status": "partial" if description_error or not images or (platform == "amazon" and not desc) else "complete",
        "parser_version": PARSER_VERSION,
        "collector": "tmapi",
        "provider": "tmapi",
        "warnings": warnings,
        "render_error": "；".join(warnings),
        "upstream_cache": "unknown",
    }
    if platform == "1688":
        result["measurements"] = measurement_projection(data)
    return result


def unpack(response, expected_key, diagnostic):
    diagnostic["http_status"] = response.status_code
    if response.status_code != 200:
        raise CollectionError("TMAPI服务返回HTTP " + str(response.status_code))
    if len(response.content) > 8_000_000:
        raise CollectionError("TMAPI返回资料超出大小限制")
    body = response.json()
    code = body.get("code")
    diagnostic["business_code"] = code if isinstance(code, int) else None
    if code != 200:
        messages = {
            417: "未取得商品数据",
            422: "请求参数无效",
            439: "套餐过期或额度不足",
            499: "上游读取超时",
            500: "上游服务异常",
            503: "接口请求受限",
        }
        raise CollectionError("TMAPI" + messages.get(code, "返回异常业务状态") + "，请重试或手动补充")
    data = body.get("data")
    if not isinstance(data, dict) or str(data.get("item_id") or data.get("asin") or "").upper() != expected_key:
        raise CollectionError("TMAPI返回商品编号不一致或数据为空")
    return data


async def collect(url, storage, api_key):
    started = time.monotonic()
    diagnostic = {"collector": "tmapi", "method": "tmapi", "parser_version": PARSER_VERSION, "api_calls": 0}
    try:
        platform, identifier = identify(url)
        description = None
        description_error = ""
        async with asyncio.timeout(90), httpx.AsyncClient(timeout=40, follow_redirects=False) as client:
            diagnostic["api_calls"] += 1
            if platform == "1688":
                response = await client.get(
                    API_ROOT + "/1688/item_detail",
                    params={"item_id": identifier, "language": "zh"},
                    headers={"apikey": api_key},
                )
            else:
                response = await client.post(
                    API_ROOT + "/amazon/item_detail_by_url", json={"url": url}, headers={"apikey": api_key}
                )
            data = unpack(response, identifier, diagnostic)
            if platform == "1688":
                try:
                    diagnostic["api_calls"] += 1
                    response = await client.get(
                        API_ROOT + "/1688/item_desc", params={"item_id": identifier}, headers={"apikey": api_key}
                    )
                    description = unpack(response, identifier, diagnostic)
                except (CollectionError, httpx.HTTPError, ValueError) as e:
                    description_error = safe_error(e)
            diagnostic["page_ms"] = round((time.monotonic() - started) * 1000)
        result = normalize(data, url, description, description_error)
        result.update(diagnostic)
        # Snapshot records provider data, never request headers or keys.
        raw = json.dumps({"data": data, "detail": description}, ensure_ascii=False).replace(api_key, "[REDACTED]")
        async with asyncio.timeout(75):
            result = await persist_result(result, raw, storage, "tmapi", filename="page.json")
        result["total_ms"] = round((time.monotonic() - started) * 1000)
        return result
    except Exception as e:  # noqa: BLE001 -- Provider errors may contain echoed auth; expose only safe categories.
        diagnostic["total_ms"] = round((time.monotonic() - started) * 1000)
        message = safe_error(e)
        if not isinstance(e, CollectionError):
            message = "TMAPI " + message
        raise CollectionError(message, diagnostic) from None
