"""Enrich test-only collection cache from its immutable local JSON, without API calls."""

import argparse
import json

from psycopg.types.json import Jsonb

from amazon_aigc.config import TEST_BASE, settings
from amazon_aigc.db import Database
from amazon_aigc.source_measurements import measurement_projection
from amazon_aigc.tmapi_collector import identify


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--apply", action="store_true")
    options = args.parse_args()
    s = settings()
    assert s.base_id == TEST_BASE
    root = s.storage_root.resolve()
    db = Database(s.database_url)
    changed = []
    skipped = 0
    try:
        with db.connection() as c:
            rows = c.execute(
                "SELECT id,url,object_key,body FROM source_snapshots WHERE body->>'collector'='tmapi' AND NOT body ? 'measurements'"
            ).fetchall()
            for row in rows:
                platform, identifier = identify(row["url"])
                if platform != "1688":
                    continue
                path = (root / (row["object_key"] or "")).resolve()
                if (
                    not path.is_relative_to(root)
                    or not path.is_file()
                    or path.suffix != ".json"
                    or path.stat().st_size > 8_000_000
                ):
                    skipped += 1
                    continue
                try:
                    raw = json.loads(path.read_text())
                    data = raw.get("data") or {}
                    if str(data.get("item_id")) != identifier:
                        raise ValueError("Item mismatch")
                    measurements = measurement_projection(data)
                except (ValueError, AttributeError, TypeError):
                    skipped += 1
                    continue
                if options.apply:
                    c.execute(
                        "UPDATE source_snapshots SET body=jsonb_set(body,'{measurements}',%s) WHERE id=%s",
                        (Jsonb(measurements), row["id"]),
                    )
                changed.append(str(row["id"]))
        print(
            json.dumps(
                {"applied": options.apply, "enriched_count": len(changed), "skipped": skipped, "snapshot_ids": changed}
            )
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
