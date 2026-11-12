"""One-time queue compatibility for the v10 -> white-label prompt rollout.

Stop API/model workers before --apply. No document, version, job state or model call
is changed. The default is a read-only preview.
"""

import argparse
import hashlib
import json

from psycopg.types.json import Jsonb

from .source_pipeline import input_stamp, source_inputs


def legacy_input_stamp(doc):
    body = doc["body"]
    basis = {
        "facts_version": str(doc["facts_version"]),
        "facts": body.get("facts"),
        "keywords": body.get("keywords", ""),
        "selling_points": body.get("selling_points", ""),
        "editor_requirements": {
            k: v for k, v in (body.get("brief") or {}).items() if k not in ("plans", "image_count")
        },
        "sources": sorted(
            (s["url"], s["kind"], json.dumps(s.get("selected_skus", []), sort_keys=True, ensure_ascii=False))
            for s in source_inputs(body)
        ),
    }
    return hashlib.sha256(json.dumps(basis, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def upgrade_pending(c, *, apply=False):
    if apply and c.execute("SELECT 1 FROM jobs WHERE kind='model' AND state='running' LIMIT 1").fetchone():
        raise RuntimeError("Wait for running model calls to finish before upgrading queued inputs")
    rows = c.execute(
        "SELECT * FROM jobs WHERE kind='model' AND state IN ('queued','waiting_sources','waiting_input') "
        "ORDER BY created_at" + (" FOR UPDATE" if apply else "")
    ).fetchall()
    result = {"apply": apply, "pending": len(rows), "compatible": 0, "stale": 0, "already_current": 0}
    for row in rows:
        payload = row["payload"]
        if payload.get("resume_stamp"):
            result["already_current"] += 1
            continue
        doc = c.execute("SELECT * FROM content_documents WHERE work_item_id=%s", (row["work_item_id"],)).fetchone()
        if not doc or payload.get("input_stamp") != legacy_input_stamp(doc):
            result["stale"] += 1
            continue
        previous_notes = {s["url"]: s.get("manual_text", "") for s in payload.get("source_inputs", [])}
        current_notes = {s["url"]: s.get("manual_text", "") for s in source_inputs(doc["body"])}
        # Waiting-input tasks reload notes on explicit resume. Queued calls must
        # never silently use notes that changed after the original submission.
        if row["state"] != "waiting_input" and previous_notes != current_notes:
            result["stale"] += 1
            continue
        result["compatible"] += 1
        if apply:
            updated = {
                **payload,
                "input_stamp": input_stamp(doc),
                "resume_stamp": input_stamp(doc, include_manual=False),
                "policy_upgrade_from": {"prompt": "amazon-content-20261106-v10", "input_stamp": payload["input_stamp"]},
            }
            c.execute("UPDATE jobs SET payload=%s WHERE id=%s", (Jsonb(updated), row["id"]))
            c.execute(
                "INSERT INTO audit_events(work_item_id,action,detail) VALUES(%s,'job.policy-upgrade',%s)",
                (row["work_item_id"], Jsonb({"job_id": str(row["id"]), "preserved_state": row["state"]})),
            )
    return result


def main():
    from .config import settings
    from .db import Database

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db = Database(settings().database_url)
    try:
        with db.connection() as c:
            print(json.dumps(upgrade_pending(c, apply=args.apply)))
    finally:
        db.close()


if __name__ == "__main__":
    main()
