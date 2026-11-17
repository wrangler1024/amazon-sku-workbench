from uuid import uuid4

import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from amazon_aigc.db import Database
from amazon_aigc.policy_rollout import legacy_input_stamp, upgrade_pending
from amazon_aigc.source_pipeline import input_stamp


@pytest.fixture
def db(db):
    # The rollout intentionally examines the entire queue, so give each test a
    # separate local schema rather than relying on other tests' pending jobs.
    schema = "rollout_" + uuid4().hex
    with db.connection() as c:
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    isolated = Database(make_conninfo(db.pool.conninfo, options="-csearch_path=" + schema))
    isolated.migrate()
    yield isolated
    isolated.close()
    with db.connection() as c:
        c.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def queued(c, item, state, doc):
    jid = uuid4()
    payload = {
        "input_stamp": legacy_input_stamp(doc),
        "context": {"facts": {"included": "4件"}},
        "source_inputs": [{"url": "https://example.test", "manual_text": "原补充"}],
    }
    c.execute(
        "INSERT INTO jobs(id,kind,work_item_id,state,payload) VALUES(%s,'model',%s,%s,%s)",
        (jid, item, state, Jsonb(payload)),
    )
    return jid, payload


def prepare(c, item):
    body = {
        "facts": {"included": "4件"},
        "sources": [{"id": "s", "kind": "supplier", "url": "https://example.test", "manual_text": "原补充"}],
    }
    return c.execute(
        "UPDATE content_documents SET body=%s WHERE work_item_id=%s RETURNING *", (Jsonb(body), item)
    ).fetchone()


def test_preview_and_apply_preserve_waiting_state_context_and_document(db, setup):
    _, item, _ = setup
    with db.connection() as c:
        doc = prepare(c, item)
        jid, before = queued(c, item, "waiting_input", doc)
        assert upgrade_pending(c)["compatible"] == 1
        assert c.execute("SELECT payload FROM jobs WHERE id=%s", (jid,)).fetchone()["payload"] == before
        assert upgrade_pending(c, apply=True)["compatible"] == 1
        row = c.execute("SELECT * FROM jobs WHERE id=%s", (jid,)).fetchone()
        assert row["state"] == "waiting_input" and row["attempts"] == 0
        assert row["payload"]["context"] == before["context"]
        assert row["payload"]["input_stamp"] == input_stamp(doc)
        assert c.execute("SELECT * FROM content_documents WHERE work_item_id=%s", (item,)).fetchone() == doc
        assert upgrade_pending(c, apply=True)["compatible"] == 0


def test_changed_facts_or_queued_notes_are_never_rebased(db, setup):
    _, item, _ = setup
    with db.connection() as c:
        doc = prepare(c, item)
        jid, payload = queued(c, item, "queued", doc)
        body = doc["body"]
        body["sources"][0]["manual_text"] = "修改后的补充"
        c.execute("UPDATE content_documents SET body=%s WHERE work_item_id=%s", (Jsonb(body), item))
        assert upgrade_pending(c, apply=True)["stale"] == 1
        body["facts"]["included"] = "2件"
        c.execute("UPDATE content_documents SET body=%s WHERE work_item_id=%s", (Jsonb(body), item))
        assert upgrade_pending(c, apply=True)["stale"] == 1
        assert c.execute("SELECT payload FROM jobs WHERE id=%s", (jid,)).fetchone()["payload"] == payload


def test_running_calls_require_drain_and_are_not_touched(db, setup):
    _, item, _ = setup
    with db.connection() as c:
        doc = prepare(c, item)
        jid, payload = queued(c, item, "running", doc)
        with pytest.raises(RuntimeError, match="running model"):
            upgrade_pending(c, apply=True)
        assert c.execute("SELECT payload FROM jobs WHERE id=%s", (jid,)).fetchone()["payload"] == payload
