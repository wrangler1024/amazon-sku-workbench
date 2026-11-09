"""Bounded, authenticated display derivatives; source originals remain unchanged."""

import asyncio
import hashlib
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps

from .collector import CollectionError, fetch_bytes, validate_url

SIZES = {"thumbnail": 192, "preview": 1280}
_locks = [threading.Lock() for _ in range(64)]
_decoders = threading.BoundedSemaphore(2)
_downloads = threading.BoundedSemaphore(4)
_source_workers = ThreadPoolExecutor(max_workers=4, thread_name_prefix="source-preview")


def image_key(url):
    return hashlib.sha256(url.encode()).hexdigest()


def source_images(body):
    urls = list(body.get("images") or [])
    for variant in body.get("variants") or []:
        urls.extend(variant.get("images") or [])
    found = []
    for url in dict.fromkeys(u for u in urls if isinstance(u, str)):
        try:
            validate_url(url, resource=True, resolve=False)
        except (ValueError, CollectionError):
            continue
        found.append(url)
    return found[:2000]


def source_image_paths(snapshot):
    return {
        url: {size: f"/api/source-images/{snapshot['id']}/{image_key(url)}?size={size}" for size in SIZES}
        for url in source_images(snapshot["body"])
    }


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def encode_preview(data, size):
    if len(data) > 20_000_000:
        raise ValueError("图片超过20MB")
    with _decoders, Image.open(io.BytesIO(data)) as im:
        if im.format not in ("JPEG", "PNG", "WEBP", "GIF") or im.width * im.height > 32_000_000:
            raise ValueError("不支持的图片格式或像素过大")
        im.draft("RGB", (SIZES[size], SIZES[size]))
        image = ImageOps.exif_transpose(im)
        image.thumbnail((SIZES[size], SIZES[size]))
        image = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
        out = io.BytesIO()
        image.save(out, "WEBP", quality=78 if size == "thumbnail" else 88, method=3)
        return out.getvalue()


def asset_preview(storage, asset, size):
    key = image_key(str(asset["id"]) + ":" + asset["sha256"])
    path = storage / "previews" / "v1" / f"{key}-{size}.webp"
    if not path.is_file():
        with _locks[int(key[:4], 16) % len(_locks)]:
            if not path.is_file():
                _atomic_write(path, encode_preview((storage / asset["object_key"]).read_bytes(), size))
    return path


def source_preview(storage: Path, url, size):
    key = image_key(url)
    path = storage / "source-previews" / "v1" / f"{key}-{size}.webp"
    if not path.is_file():
        with _locks[int(key[:4], 16) % len(_locks)]:
            if not path.is_file():
                original = storage / "source-previews" / "v1" / f"{key}.original"
                if original.is_file():
                    data = original.read_bytes()
                else:
                    async def read():
                        async with asyncio.timeout(8):
                            return (await fetch_bytes(url, resource=True, limit=20_000_000))[0]
                    with _downloads:
                        data = asyncio.run(read())
                preview = encode_preview(data, size)
                if not original.is_file():
                    _atomic_write(original, data)
                _atomic_write(path, preview)
    return path


def preview_etag(path):
    stat = path.stat()
    return '"' + hashlib.sha256(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest() + '"'


async def source_preview_async(storage, url, size):
    return await asyncio.get_running_loop().run_in_executor(_source_workers, source_preview, storage, url, size)
