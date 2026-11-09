import os
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from amazon_aigc.db import Database


@pytest.fixture(scope="session")
def db():
    # Tests use an isolated schema and never truncate the integration schema.
    url = os.environ.get("AIGC_TEST_DATABASE_URL", os.environ.get("DATABASE_URL"))
    if not url:
        pytest.skip("Set AIGC_TEST_DATABASE_URL to a disposable local PostgreSQL database")
    from urllib.parse import urlsplit
    assert urlsplit(url).hostname in ("127.0.0.1", "localhost", "postgres"), "Tests require a local disposable database"
    schema = "test_" + uuid4().hex
    import psycopg
    from psycopg import sql

    with psycopg.connect(url) as c:
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    database = Database(url + "?options=-csearch_path%3D" + schema)
    database.migrate()
    yield database
    database.close()
    with psycopg.connect(url) as c:
        c.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def setup(db):
    with db.connection() as c:
        users = {}
        for role, group in [("operator", "一部一组"), ("other", "一部二组"), ("artist", ""), ("art_lead", "")]:
            uid = uuid4()
            roles = ["operator"] if role == "other" else [role]
            c.execute(
                "INSERT INTO users(id,name,roles,groups,active,internal,verified_at) VALUES(%s,%s,%s,%s,true,true,now())",
                (uid, role, roles, [group] if group else []),
            )
            users[role] = c.execute("SELECT * FROM users WHERE id=%s", (uid,)).fetchone()
        item = uuid4()
        doc = uuid4()
        c.execute(
            "INSERT INTO work_items(id,environment,site,code,name,owner_id,group_name,review_status) VALUES(%s,'test','US',%s,'合成测试商品',%s,'一部一组','审核通过')",
            (item, str(item), users["operator"]["id"]),
        )
        c.execute("INSERT INTO content_documents(id,work_item_id,body) VALUES(%s,%s,%s)", (doc, item, Jsonb({})))
    return users, item, doc
