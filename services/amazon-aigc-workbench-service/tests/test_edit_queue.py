from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from test_workflow import BRIEF, FACTS, LISTING, pool, prepare

from amazon_aigc.api import create_app
from amazon_aigc.auth import new_session
from amazon_aigc.config import Settings
from amazon_aigc.domain import detail, publish_brief, publish_listing, reopen_edit, task_command


def test_pending_queue_follows_content_not_publication_or_art_progress(db, setup, tmp_path):
    users, item, _ = setup
    with db.connection() as c:
        session = new_session(c, users["operator"]["id"])
    client = TestClient(create_app(Settings(database_url="postgresql://unused/test", storage_root=tmp_path), db))
    headers = {"Authorization": "Bearer " + session}

    def read():
        rows = client.get("/api/items", headers=headers).json()
        row = next(r for r in rows if r["id"] == str(item))
        specific = client.get(f"/api/items/{item}", headers=headers).json()
        assert row["editing"] == specific["editing"]
        return row

    assert read()["editing"]["todos"] == [{"stage": "facts", "label": "待核对资料"}, {"stage": "listing", "label": "待确认文案"}, {"stage": "brief", "label": "待确认图需"}]
    prepare(db, users["operator"], item, "facts", FACTS)
    assert len(read()["editing"]["todos"]) == 2
    d = prepare(db, users["operator"], item, "listing", LISTING)
    with db.connection() as c:
        publish_listing(c, users["operator"], item, d["document"]["revision"],
                        {"store": "TEST", "msku": "TEST", "receipt": "合成回执"})
    assert read()["editing"]["pending"]  # Early publication still needs a brief.
    d = prepare(db, users["operator"], item, "brief", BRIEF)
    assert read()["editing"]["todos"] == [{"stage": "brief", "label": "待提交图需"}]
    with db.connection() as c:
        d = publish_brief(c, users["operator"], item, d["document"]["revision"], {})
    assert not read()["editing"]["pending"]
    t = d["tasks"][0]
    for action, actor, payload in [("claim", "artist", {}), ("return", "artist", {"reason": "合成退回"})]:
        with db.connection() as c:
            t = task_command(c, users[actor], t["id"], action, t["revision"], payload)["tasks"][0]
        assert not read()["editing"]["pending"]  # Returning to the pool is not withdrawing the brief.
    with db.connection() as c:
        task_command(c, users["operator"], t["id"], "withdraw", t["revision"], {})
    assert read()["editing"]["pending"]


def test_reopen_listing_http_preserves_versions_and_is_idempotent(db, setup, tmp_path):
    users, item, _ = setup
    t = pool(db, setup)
    d = prepare(db, users["operator"], item, "listing", LISTING)
    with db.connection() as c:
        d = publish_listing(c, users["operator"], item, d["document"]["revision"],
                            {"store": "TEST", "msku": "TEST", "receipt": "合成回执"})
        token = new_session(c, users["operator"]["id"])
        other = new_session(c, users["other"]["id"])
    client = TestClient(create_app(Settings(database_url="postgresql://unused/test", storage_root=tmp_path), db))
    payload = {"revision": d["document"]["revision"], "data": {"section": "listing"}}
    headers = {"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid4())}
    path = f"/api/items/{item}/reopen-edit"
    result = client.post(path, headers=headers, json=payload)
    assert result.status_code == 200, result.text
    reopened = result.json()
    assert reopened["editing"]["todos"] == [{"stage": "listing", "label": "待确认文案"}]
    assert reopened["document"]["body"] == d["document"]["body"]
    assert reopened["versions"] == d["versions"] and reopened["publications"] == d["publications"]
    assert reopened["tasks"][0]["id"] == t["id"] and reopened["tasks"][0]["state"] == "pool"
    assert client.post(path, headers=headers, json=payload).json() == reopened
    headers["Idempotency-Key"] = str(uuid4())
    assert client.post(path, headers=headers, json=payload).status_code == 409
    headers["Authorization"] = "Bearer " + other
    assert client.post(path, headers=headers, json=payload).status_code == 404
    d = prepare(db, users["operator"], item, "listing", LISTING)
    assert not d["editing"]["pending"]
    assert d["document"]["listing_version"] != d["publications"][0]["listing_version"]


def test_reopen_brief_withdraws_unstarted_and_requires_resubmission(db, setup):
    users, item, _ = setup
    t = pool(db, setup)
    d = prepare(db, users["operator"], item, "listing", LISTING)
    with db.connection() as c:
        d = reopen_edit(c, users["operator"], item, d["document"]["revision"], {"section": "brief"})
    assert d["tasks"][0]["state"] == "withdrawn"
    assert d["tasks"][0]["brief_version"] == t["brief_version"]
    assert d["document"]["listing_version"] and not d["document"]["brief_version"]
    d = prepare(db, users["operator"], item, "brief", BRIEF)
    assert d["editing"]["pending"]
    with db.connection() as c:
        d = publish_brief(c, users["operator"], item, d["document"]["revision"], {})
    assert not d["editing"]["pending"] and d["tasks"][0]["batch"] == 2


@pytest.mark.parametrize("section", ["facts", "brief"])
def test_started_artwork_blocks_reopening_its_inputs(db, setup, section):
    users, item, _ = setup
    t = pool(db, setup)
    d = prepare(db, users["operator"], item, "listing", LISTING)
    with db.connection() as c:
        t = task_command(c, users["artist"], t["id"], "claim", t["revision"], {})["tasks"][0]
        task_command(c, users["artist"], t["id"], "start", t["revision"], {})
    with db.connection() as c, pytest.raises(HTTPException):
        reopen_edit(c, users["operator"], item, d["document"]["revision"], {"section": section})
    with db.connection() as c:
        current = detail(c, users["operator"], item)
        assert current["document"] == d["document"] and not current["editing"]["pending"]
        reopened = reopen_edit(c, users["operator"], item, d["document"]["revision"], {"section": "listing"})
        assert reopened["editing"]["pending"] and reopened["tasks"][0]["state"] == "working"


def test_new_brief_after_acceptance_must_be_submitted_again(db, setup):
    users, item, _doc = setup
    t = pool(db, setup)
    prepare(db, users["operator"], item, "listing", LISTING)
    with db.connection() as c:
        # Isolate the already-covered image acceptance flow from this queue regression.
        c.execute("UPDATE artwork_tasks SET state='accepted' WHERE id=%s", (t["id"],))
    d = prepare(db, users["operator"], item, "brief", {**BRIEF, "visual_focus": "新批次画面"})
    assert d["editing"]["todos"] == [{"stage": "brief", "label": "待提交图需"}]
    with db.connection() as c:
        d = reopen_edit(c, users["operator"], item, d["document"]["revision"], {"section": "facts"})
        assert d["editing"]["todos"] == [{"stage": "facts", "label": "待核对资料"}, {"stage": "listing", "label": "待确认文案"}, {"stage": "brief", "label": "待确认图需"}]
        assert not any(d["document"][x + "_version"] for x in ["facts", "listing", "brief"])
        assert d["tasks"][0]["state"] == "accepted" and d["versions"]
