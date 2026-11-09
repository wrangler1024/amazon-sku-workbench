"""Select owned product/reference images, prepare bounded image inputs and record receipts."""

import asyncio
import base64
import hashlib
import io
import json
import os
import tempfile
import threading
import time
from uuid import UUID

from PIL import Image, ImageOps

from .collector import CollectionError, fetch_bytes, safe_error
from .source_pipeline import source_rows

ENCODING_SLOT = threading.BoundedSemaphore(1)

MAX_IMAGES = 32
MAX_TOTAL_BYTES = 10_000_000
IMAGE_FETCH_SECONDS = 5
IMAGE_PREPARATION_SECONDS = 15


def candidates(c, job):
    payload = job["payload"]
    facts = payload["context"].get("facts", {})
    components = facts.get("components") or []
    result = []
    extras = []

    def add(kind, label, *, url="", key="", required=False, error="", bindings=None):
        result.append(
            {
                "kind": kind,
                "label": label[:300],
                "url": url,
                "object_key": key,
                "required": required,
                "error": error,
                "bindings": bindings or [],
            }
        )

    for row in components:
        for aid in row.get("image_asset_ids") or []:
            try:
                asset = c.execute(
                    "SELECT object_key,name FROM assets WHERE id=%s AND work_item_id=%s AND source_url IS NULL",
                    (UUID(aid), job["work_item_id"]),
                ).fetchone()
            except (ValueError, TypeError):
                asset = None
            add(
                "manual",
                f"人工补充：{row.get('name', '')} / {row.get('specification', '')}",
                key=asset["object_key"] if asset else "",
                required=True,
                error="组成产品图片不存在或不属于当前商品" if not asset else "",
                bindings=[
                    {
                        "component_id": row["id"],
                        "specification": row.get("specification"),
                        "quantity": row.get("quantity"),
                    }
                ],
            )

    for source, _link, _source_job, body in source_rows(c, payload):
        downloads = {d.get("url"): d for d in body.get("downloads", []) if d.get("key")}
        if source["kind"] == "supplier":
            bound = [
                r
                for r in components
                if (r.get("source_id") == source["id"] if r.get("source_id") else r.get("url") == source["url"])
            ]
            paired = source.get("selected_skus") or []
            catalog = {str(s.get("sku_id")): s for s in [*(body.get("variants") or []), *paired]}
            chosen = [(catalog.get(str(r.get("supplier_sku_id"))), r) for r in bound if r.get("supplier_sku_id")]
            if not chosen:
                chosen = [(sku, {}) for sku in paired]
            if not chosen and not any(r.get("image_asset_ids") for r in bound):
                add(
                    "selected_sku",
                    "1688本品规格图",
                    required=True,
                    error="尚未选择供应规格，无法确定本品图片；请选择规格或上传组成产品图片",
                    bindings=[{"source_id": source["id"]}],
                )
            for sku, row in chosen:
                label = f"1688所选规格：{row.get('specification') or (sku or {}).get('specification', '待核对')}"
                bindings = [
                    {
                        "source_id": source["id"],
                        "component_id": row.get("id"),
                        "sku_id": row.get("supplier_sku_id") or (sku or {}).get("sku_id"),
                        "specification": row.get("specification") or (sku or {}).get("specification"),
                        "quantity": row.get("quantity"),
                    }
                ]
                images = list(dict.fromkeys((sku or {}).get("images") or []))[:2]
                if not images:
                    add(
                        "selected_sku",
                        label,
                        required=not bool(row.get("image_asset_ids")),
                        error="所选规格没有可读取的图片，请上传对应组成产品图片",
                        bindings=bindings,
                    )
                for url in images:
                    add(
                        "selected_sku",
                        label,
                        url=url,
                        key=downloads.get(url, {}).get("key", ""),
                        required=not bool(row.get("image_asset_ids")),
                        bindings=bindings,
                    )
            # Common source illustrations may help, but unselected SKU pictures never enter.
            variant_urls = {u for sku in body.get("variants", []) for u in sku.get("images", [])}
            common = [u for u in body.get("images", []) if u not in variant_urls][:2]
            extras.extend(
                {
                    "kind": "supplier_reference",
                    "label": "货源通用参考（不代表选中规格）",
                    "url": u,
                    "object_key": downloads.get(u, {}).get("key", ""),
                    "required": False,
                    "error": "",
                    "bindings": [{"source_id": source["id"]}],
                }
                for u in common
            )
        else:
            urls = list(dict.fromkeys([*body.get("images", []), *downloads]))[:3]
            if not urls:
                extras.append(
                    {
                        "kind": "competitor",
                        "label": "竞品参考图",
                        "url": "",
                        "object_key": "",
                        "required": False,
                        "error": "竞品未取得可用图片",
                        "bindings": [{"source_id": source["id"]}],
                    }
                )
            extras.extend(
                {
                    "kind": "competitor",
                    "label": "竞品参考图（不得作为本品事实）",
                    "url": u,
                    "object_key": downloads.get(u, {}).get("key", ""),
                    "required": False,
                    "error": "",
                    "bindings": [{"source_id": source["id"]}],
                }
                for u in urls
            )
    result.extend(extras)
    return [{**row, "image_id": f"image-{i + 1}", "state": "pending"} for i, row in enumerate(result)]


def safe_path(root, key):
    path = (root / key).resolve()
    if not key or not path.is_relative_to(root.resolve()):
        raise ValueError("图片文件位置无效")
    return path


def encode_image(data):
    # Bound decoded-image memory while model HTTP requests remain concurrent.
    with ENCODING_SLOT:
        return _encode_image(data)


def _encode_image(data):
    if len(data) > 20_000_000:
        raise ValueError("原图超过20MB")
    with Image.open(io.BytesIO(data)) as im:
        if im.format not in ("JPEG", "PNG", "WEBP") or min(im.size) < 32:
            raise ValueError("图片格式不支持或分辨率过低")
        if im.width * im.height > 16_000_000:
            raise ValueError("图片像素过大，请上传较小的清晰图片")
        im.draft("RGB", (1280, 1280))
        ImageOps.exif_transpose(im, in_place=True)
        image = im
        image.thumbnail((1280, 1280))
        if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
            rgba = image.convert("RGBA")
            rgb = Image.new("RGB", rgba.size, "white")
            rgb.paste(rgba, mask=rgba.getchannel("A"))
        else:
            rgb = image.convert("RGB")
        out = io.BytesIO()
        rgb.save(out, "JPEG", quality=85, optimize=True)
        return out.getvalue(), rgb.width, rgb.height


async def prepare_images(storage, item_id, rows, progress=None):
    ready = []
    seen = {}
    total = 0
    started = time.monotonic()
    deadline = started + IMAGE_PREPARATION_SECONDS
    for row in rows:
        try:
            if row.get("error"):
                raise ValueError(row["error"])
            identity = row["object_key"] or row["url"]
            cache = storage / "vision" / str(item_id) / (hashlib.sha256(identity.encode()).hexdigest() + ".jpg")
            if cache.is_file() and time.time() - cache.stat().st_mtime < 86400:
                image = cache.read_bytes()
                with Image.open(io.BytesIO(image)) as im:
                    width, height = im.size
            else:
                path = safe_path(storage, row["object_key"]) if row["object_key"] else None
                if path and path.is_file():
                    data = path.read_bytes()
                elif row["url"]:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ValueError("图片等待超时，本次跳过该图")
                    async with asyncio.timeout(min(IMAGE_FETCH_SECONDS, remaining)):
                        data, _, _ = await fetch_bytes(row["url"], True, 20_000_000)
                else:
                    raise ValueError("图片原文件缺失，请重新上传")
                image, width, height = encode_image(data)
                cache.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(prefix=".image-", dir=cache.parent)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(image)
                    os.replace(temporary, cache)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            digest = hashlib.sha256(image).hexdigest()
            row.update(width=width, height=height, sha256=digest)
            if digest in seen:
                row.update(state="duplicate", duplicate_of=seen[digest]["image_id"])
                seen[digest]["bindings"].extend(row["bindings"])
            elif len(ready) >= MAX_IMAGES or total + len(image) > MAX_TOTAL_BYTES:
                row.update(
                    state="failed" if row["required"] else "skipped",
                    error="超出本次32张或10MB图片上限，本次跳过该图",
                )
            else:
                row.update(state="ready", error="")
                seen[digest] = row
                ready.append(
                    {
                        "image_id": row["image_id"],
                        "data_url": "data:image/jpeg;base64," + base64.b64encode(image).decode(),
                        "detail": "high",
                    }
                )
                total += len(image)
        except Exception as e:  # noqa: BLE001 -- isolate untrusted files and network resources
            reason = str(e) if isinstance(e, (ValueError, CollectionError)) else safe_error(e)
            row.update(state="failed", error=reason[:250])
        if progress:
            progress(public_report(rows, "preparing"))
    report = public_report(rows, "ready")
    report["preparation_ms"] = round((time.monotonic() - started) * 1000)
    if not ready:
        report["reason"] = "没有可用图片，本次自动依据已确认文字资料生成；美工可继续查看货源和参考链接"
    elif report["failed_count"] or report["skipped_count"]:
        report["reason"] = "部分图片未读取，已使用其余可用图片和文字资料继续生成"
    return ready, report


def public_report(rows, state):
    return {
        "state": state,
        "sent_count": 0,
        "recognized_count": 0,
        "unclear_count": 0,
        "ready_count": sum(r["state"] == "ready" for r in rows),
        "failed_count": sum(r["state"] == "failed" for r in rows),
        "skipped_count": sum(r["state"] == "skipped" for r in rows),
        "duplicate_count": sum(r["state"] == "duplicate" for r in rows),
        "images": [{k: v for k, v in r.items() if k not in ("object_key",)} for r in rows],
    }


def vision_schema(schema, image_ids):
    schema = json.loads(json.dumps(schema))
    schema["properties"]["image_observations"] = {
        "type": "array",
        "minItems": len(image_ids),
        "maxItems": len(image_ids),
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "image_id": {"type": "string", "enum": image_ids},
                "status": {"type": "string", "enum": ["recognized", "unclear"]},
                "observation": {"type": "string", "maxLength": 800},
                "reason": {"type": "string", "maxLength": 400},
            },
            "required": ["image_id", "status", "observation", "reason"],
        },
    }
    schema["required"].append("image_observations")
    return schema


def apply_observations(report, observations):
    report = json.loads(json.dumps(report))
    expected = {r["image_id"] for r in report["images"] if r["state"] == "ready"}
    if (
        not isinstance(observations, list)
        or len(observations) != len(expected)
        or {r.get("image_id") for r in observations if isinstance(r, dict)} != expected
    ):
        raise ValueError("模型图片识别回执缺失或与送入图片不一致")
    for result in observations:
        if (
            result.get("status") not in ("recognized", "unclear")
            or not isinstance(result.get("observation"), str)
            or not isinstance(result.get("reason"), str)
        ):
            raise ValueError("模型图片识别回执格式无效")
        if result["status"] == "recognized" and not result["observation"].strip():
            raise ValueError("图片识别缺少可见内容摘要")
        if result["status"] == "unclear" and not result["reason"].strip():
            raise ValueError("图片无法识别时须返回原因")
        row = next(r for r in report["images"] if r["image_id"] == result["image_id"])
        row.update(state=result["status"], observation=result["observation"][:800], error=result["reason"][:400])
    report.update(
        state="completed",
        sent_count=len(expected),
        recognized_count=sum(r["status"] == "recognized" for r in observations),
        unclear_count=sum(r["status"] == "unclear" for r in observations),
    )
    return report
