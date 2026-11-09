import hashlib
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


class Database:
    def __init__(self, url):
        self.pool = ConnectionPool(url, min_size=1, max_size=6, kwargs={"row_factory": dict_row}, open=True)

    def close(self):
        self.pool.close()

    def connection(self):
        return self.pool.connection()

    def migrate(self, directory=None):
        directory = directory or Path(__file__).resolve().parents[2] / "sql"
        with self.connection() as c:
            c.execute("SELECT pg_advisory_xact_lock(58017001)")
            c.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations(version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            for p in sorted(Path(directory).glob("*.sql")):
                if p.name.startswith("."):
                    continue
                body = p.read_text()
                digest = hashlib.sha256(body.encode()).hexdigest()
                row = c.execute("SELECT checksum FROM schema_migrations WHERE version=%s", (p.name,)).fetchone()
                if row:
                    if row["checksum"] != digest:
                        raise RuntimeError("Applied migration was modified: " + p.name)
                    continue
                c.execute(body)
                c.execute("INSERT INTO schema_migrations(version,checksum) VALUES(%s,%s)", (p.name, digest))
