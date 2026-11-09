import argparse
import hashlib
import json
import signal
import threading
from pathlib import Path
from uuid import uuid4

from .auth import new_session
from .config import settings
from .db import Database
from .feishu import FeishuGateway
from .jobs import run_job
from .sync import import_records, refresh_identities, sync_outbox


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=["migrate", "doctor", "import", "sync", "worker", "provision", "local-session", "export"]
    )
    parser.add_argument("--kind", choices=["source", "model", "sync", "notify", "roles", "images"], default="sync")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--file")
    parser.add_argument("--output")
    args = parser.parse_args()
    s = settings()
    db = Database(s.database_url)
    db.migrate()
    gateway = FeishuGateway(s)
    if args.command == "migrate":
        print(json.dumps({"migrated": True}))
    elif args.command == "doctor":
        rows = gateway.records(s.sku_table_id, ["中文品名", "审核状态"])
        print(
            json.dumps(
                {
                    "service": "amazon-aigc-workbench-service",
                    "environment": s.environment,
                    "base_id": s.base_id,
                    "sku_count": len(rows),
                    "postgresql": True,
                    "providers": {
                        "openai": bool(s.openai_api_key and s.openai_model),
                        "deepseek": bool(s.deepseek_api_key and s.deepseek_model),
                    },
                }
            )
        )
    elif args.command == "import":
        print(json.dumps(import_records(db, gateway)))
    elif args.command == "sync":
        print(json.dumps({"synced": sync_outbox(db, gateway)}))
    elif args.command == "provision":
        roster = json.loads(Path(args.file).read_text())
        if roster["base_id"] != s.base_id:
            raise ValueError("Roster environment mismatch")
        with db.connection() as c:
            for p in roster["people"]:
                ident = c.execute(
                    "SELECT user_id FROM external_identities WHERE provider='feishu' AND tenant=%s AND subject=%s",
                    (s.tenant, p["subject"]),
                ).fetchone()
                uid = ident["user_id"] if ident else uuid4()
                c.execute(
                    "INSERT INTO users(id,name,roles,groups,active,internal,verified_at) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET name=excluded.name,roles=excluded.roles,groups=excluded.groups,active=excluded.active,internal=excluded.internal,verified_at=excluded.verified_at",
                    (uid, p["name"], p["roles"], p["groups"], p["active"], p["internal"], roster["verified_at"]),
                )
                c.execute(
                    "INSERT INTO external_identities(provider,tenant,subject,user_id) VALUES('feishu',%s,%s,%s) ON CONFLICT DO NOTHING",
                    (s.tenant, p["subject"], uid),
                )
                if p.get("union_id"):
                    c.execute(
                        "INSERT INTO external_identities(provider,tenant,subject,user_id) VALUES('feishu_union',%s,%s,%s) ON CONFLICT DO NOTHING",
                        (s.tenant, p["union_id"], uid),
                    )
        print(json.dumps({"provisioned": len(roster["people"])}))
    elif args.command == "local-session":
        if s.feishu_mode != "cli" or not s.public_url.startswith("http://127.0.0.1:"):
            raise ValueError("Local session only allowed on loopback using actual CLI user")
        import subprocess

        r = subprocess.run(
            ["lark-cli", "auth", "status", "--json", "--verify"], capture_output=True, text=True, check=True
        )
        auth = json.loads(r.stdout)
        subject = auth["identities"]["user"]["openId"]
        if auth["identities"]["user"].get("tokenStatus") != "valid":
            raise ValueError("CLI identity not verified")
        with db.connection() as c:
            u = c.execute(
                "SELECT u.* FROM users u JOIN external_identities e ON e.user_id=u.id WHERE e.subject=%s AND e.tenant=%s AND u.active=true",
                (subject, s.tenant),
            ).fetchone()
            if not u:
                raise ValueError("Current CLI user is not in test roster")
            token = new_session(c, u["id"])
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(token)
        out.chmod(0o600)
        print(json.dumps({"session_file": str(out), "identity": u["name"]}))
    elif args.command == "worker":
        from .workers import serve

        stop = threading.Event()
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: stop.set())

        def once():
            if args.kind == "images":
                from .product_images import cache_missing_product_images

                result = cache_missing_product_images(db, gateway)
                if result["pending"]:
                    print(json.dumps({"product_images": result}), flush=True)
                return result["cached"] > 0
            if args.kind == "roles":
                from .roster_sync import sync_roster
                print(json.dumps(sync_roster(db, gateway)), flush=True)
                return False
            if args.kind == "notify":
                from .notifications import run_notification

                return run_notification(db, gateway)
            if args.kind == "sync":
                if not s.personnel_table_id:
                    refresh_identities(db, gateway)
                print(
                    json.dumps({"import": import_records(db, gateway), "synced": sync_outbox(db, gateway)}), flush=True
                )
                return False
            return run_job(db, s, args.kind)

        if args.once:
            once()
        else:
            concurrency = {"model": s.model_concurrency, "source": s.source_concurrency, "sync": 1, "notify": 1, "roles": 1, "images": 1}[
                args.kind
            ]
            print(json.dumps({"worker": args.kind, "concurrency": concurrency}), flush=True)
            serve(once, concurrency, stop, poll_seconds=30 if args.kind in ("sync", "roles") else 10 if args.kind == "images" else 1)
        db.close()
    elif args.command == "export":
        out = Path(args.output)
        out.mkdir(parents=True, exist_ok=False)
        tables = [
            "users",
            "external_identities",
            "work_items",
            "external_bindings",
            "content_documents",
            "content_versions",
            "artwork_tasks",
            "assets",
            "artwork_deliveries",
            "publication_targets",
            "publication_records",
            "image_receipts",
            "source_snapshots",
            "review_snapshots",
            "audit_events",
        ]
        manifest = {"schema": 1, "environment": s.environment, "tables": {}, "files": {}}
        import shutil

        from psycopg import sql

        with db.connection() as c:
            c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            for t in tables:
                rows = c.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(t))).fetchall()
                body = "\n".join(json.dumps(r, default=str, ensure_ascii=False) for r in rows)
                (out / (t + ".ndjson")).write_text(body)
                manifest["tables"][t] = {"rows": len(rows), "sha256": hashlib.sha256(body.encode()).hexdigest()}
                if t == "assets":
                    for a in rows:
                        src = s.storage_root / a["object_key"]
                        dest = out / "files" / a["object_key"]
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(src, dest)
                        digest = hashlib.sha256(dest.read_bytes()).hexdigest()
                        if digest != a["sha256"]:
                            raise ValueError("Asset hash mismatch")
                        manifest["files"][a["object_key"]] = digest
                if t == "source_snapshots":
                    for snapshot in rows:
                        key = snapshot.get("object_key")
                        if key:
                            dest = out / "files" / key
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(s.storage_root / key, dest)
                            manifest["files"][key] = hashlib.sha256(dest.read_bytes()).hexdigest()
        (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        print(json.dumps({"exported": str(out), "tables": len(tables)}))
    db.close()


if __name__ == "__main__":
    main()
