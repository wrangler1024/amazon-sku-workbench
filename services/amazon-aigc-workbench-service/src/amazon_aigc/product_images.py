"""Cache the SKU table's first product attachment for authenticated task thumbnails."""

import hashlib
import io
import logging
import re
import time
from pathlib import Path
from uuid import uuid4

from PIL import Image

PREFIX = "feishu-product-image:"
_retry_after = {}


def cache_product_images(db, gateway, items, *, max_attempts=5):
    cached = failed = 0
    attempts = 0
    deadline = time.monotonic() + 25
    for item_id, owner_id, attachments in items:
        if not isinstance(attachments, list) or not attachments or not isinstance(attachments[0], dict):
            continue
        attachment = attachments[0]
        token = attachment.get("file_token", "")
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9]{10,100}", token):
            continue
        source = PREFIX + token
        retry_key = (str(item_id), token)
        if _retry_after.get(retry_key, 0) > time.monotonic():
            continue
        try:
            with db.connection() as c:
                # Independent from document import: one failed attachment must not roll back items.
                lock = c.execute(
                    "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,58017004)) AS acquired",
                    (str(item_id),),
                ).fetchone()
                if not lock["acquired"]:
                    continue
                asset = c.execute(
                    "SELECT id,object_key FROM assets WHERE work_item_id=%s AND source_url=%s LIMIT 1",
                    (item_id, source),
                ).fetchone()
                if asset and (gateway.s.storage_root / asset["object_key"]).is_file():
                    continue
                # Bound each sync pass so image backfill cannot starve business projections.
                if attempts >= max_attempts or time.monotonic() >= deadline:
                    break
                attempts += 1
                data = gateway.product_image(attachment)
                if not data or len(data) > 20_000_000:
                    raise ValueError("Product image size invalid")
                with Image.open(io.BytesIO(data)) as im:
                    im.verify()
                with Image.open(io.BytesIO(data)) as im:
                    width, height = im.size
                    mime = Image.MIME.get(im.format, "")
                if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
                    raise ValueError("Product image format unsupported")
                aid = asset["id"] if asset else uuid4()
                key = asset["object_key"] if asset else "product-images/" + str(aid)
                path = gateway.s.storage_root / key
                path.parent.mkdir(parents=True, exist_ok=True)
                temp = path.with_suffix("." + uuid4().hex + ".tmp")
                try:
                    temp.write_bytes(data)
                    temp.replace(path)
                finally:
                    temp.unlink(missing_ok=True)
                if not asset:
                    c.execute(
                        "INSERT INTO assets(id,work_item_id,uploaded_by,object_key,name,sha256,size_bytes,mime,width,height,source_url) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            aid,
                            item_id,
                            owner_id,
                            key,
                            Path(attachment.get("name") or "product-image").name[:200],
                            hashlib.sha256(data).hexdigest(),
                            len(data),
                            mime,
                            width,
                            height,
                            source,
                        ),
                    )
            cached += 1
            _retry_after.pop(retry_key, None)
        except Exception as exc:  # noqa: BLE001 -- attachments are optional; retry without blocking the sync loop
            failed += 1
            _retry_after[retry_key] = time.monotonic() + 300
            logging.getLogger(__name__).warning(
                "Product thumbnail cache failed item=%s error=%s", item_id, type(exc).__name__
            )
    now = time.monotonic()
    for key in list(_retry_after):
        if _retry_after[key] <= now:
            _retry_after.pop(key, None)
    return {"cached": cached, "failed": failed}


def cache_missing_product_images(db, gateway):
    """Drain imported attachments independently of slow Feishu projection writes.

    The DB snapshot is the durable queue. No editor document, task, or Base
    field is changed. Per-item locks also protect against the importer's cache.
    """
    with db.connection() as c:
        rows = c.execute("""
            SELECT w.id,w.owner_id,w.review_snapshot->'产品图片' AS attachments,a.object_key
            FROM work_items w LEFT JOIN LATERAL(
              SELECT object_key FROM assets WHERE work_item_id=w.id
                AND source_url='feishu-product-image:'||(w.review_snapshot->'产品图片'->0->>'file_token')
              LIMIT 1
            ) a ON true
            WHERE w.environment=%s AND coalesce(w.review_snapshot->'产品图片'->0->>'file_token','')<>''
            ORDER BY w.created_at,w.id
        """, (gateway.s.environment,)).fetchall()
    missing = [(r["id"], r["owner_id"], r["attachments"]) for r in rows
               if not r["object_key"] or not (gateway.s.storage_root / r["object_key"]).is_file()]
    return {"pending": len(missing), **cache_product_images(db, gateway, missing, max_attempts=20)}


def thumbnail_rows(c, user, item_ids):
    from .domain import can_read, serial

    rows = c.execute("""
        SELECT w.*,a.id AS thumbnail_id FROM work_items w LEFT JOIN LATERAL(
          SELECT id FROM assets WHERE work_item_id=w.id
            AND source_url='feishu-product-image:'||(w.review_snapshot->'产品图片'->0->>'file_token')
          LIMIT 1
        ) a ON true WHERE w.id=ANY(%s)
    """, (list(set(item_ids)),)).fetchall()
    return serial([{"id": r["id"], "thumbnail_id": r["thumbnail_id"],
                    "thumbnail_pending": bool((r["review_snapshot"].get("产品图片") or []) and not r["thumbnail_id"])}
                   for r in rows if can_read(c, user, r)])
