import pytest
from fastapi import HTTPException
from test_unified_artwork import image
from test_workflow import BRIEF, FACTS, LISTING, prepare

from amazon_aigc.config import Settings
from amazon_aigc.domain import detail, publish_brief, publish_listing, task_command
from amazon_aigc.jobs import enqueue
from amazon_aigc.publication import image_receipt, withdraw_registration
from amazon_aigc.schemas import JobRequest
from amazon_aigc.workbench_query import workbench_page, workbench_summary


def test_manual_content_without_facts_completes_artwork_and_publication(db, setup):
    users, item, _ = setup
    user = users["operator"]
    d = prepare(db, user, item, "listing", LISTING)
    listing_id = d["document"]["listing_version"]
    assert d["document"]["facts_version"] is None
    with db.connection() as c:
        assert workbench_page(c, user, tab="listing")["total"] == 0
        assert workbench_summary(c, user)["badges"]["facts"] == 1
        d = publish_listing(c, user, item, d["document"]["revision"], {"msku": "MANUAL-TEST"})
    d = prepare(db, user, item, "brief", {**BRIEF, "plans": [], "image_count": 7})
    brief_id = d["document"]["brief_version"]
    with db.connection() as c:
        assert workbench_page(c, user, tab="brief")["total"] == 1
        d = publish_brief(c, user, item, d["document"]["revision"], {})
        assert workbench_page(c, user, tab="brief")["total"] == 0
        task = d["tasks"][0]
        task_command(c, users["artist"], task["id"], "claim", 0, {})
        task_command(c, users["artist"], task["id"], "start", 1, {})
    asset_id = image(db, item, users["artist"], 1)
    with db.connection() as c:
        task_command(c, users["artist"], task["id"], "deliver", 2, {"asset_ids": [asset_id]})
        d = task_command(c, user, task["id"], "review", 3, {"result": "accepted"})
        d = image_receipt(c, user, item, d["document"]["revision"],
                          {"delivery_id": d["tasks"][0]["current_delivery_id"]})
        assert d["document"]["facts_version"] is None
        assert d["document"]["listing_version"] == listing_id
        assert d["document"]["brief_version"] == brief_id
        assert all(v["facts_version"] is None for v in d["versions"])
        for tab in ("facts", "listing", "brief"):
            assert workbench_page(c, user, tab=tab, scope="all")["total"] == 0
        assert workbench_page(c, user, tab="copyPublication")["total"] == 1
        assert workbench_page(c, user, tab="imagePublication")["total"] == 1
        d = withdraw_registration(c, user, item, d["document"]["revision"],
                                  {"kind": "images", "registration_id": d["image_receipts"][0]["id"], "reason": "测试撤回"})
        assert workbench_page(c, user, tab="facts", scope="all")["total"] == 1
        assert workbench_page(c, user, tab="listing")["total"] == 0


@pytest.mark.parametrize("provider", ["openai", "deepseek"])
@pytest.mark.parametrize("section", ["listing", "brief"])
def test_ai_requires_confirmed_facts_even_after_manual_confirmation(db, setup, monkeypatch, provider, section):
    users, item, _ = setup
    user = users["operator"]
    settings = Settings(database_url="postgresql://unused/test", model_enabled=True)
    calls = []

    def runtime(c, s, p):
        calls.append(p)
        return s

    monkeypatch.setattr("amazon_aigc.provider_config.runtime", runtime)
    d = prepare(db, user, item, section, LISTING if section == "listing" else BRIEF)
    with pytest.raises(HTTPException) as error, db.connection() as c:
        enqueue(c, user, item, JobRequest(kind="model", revision=d["document"]["revision"],
                                         payload={"provider": provider, "task": section}), settings)
    assert error.value.detail == "请先确认产品事实" and not calls
    with db.connection() as c:
        assert c.execute("SELECT count(*) n FROM jobs WHERE work_item_id=%s", (item,)).fetchone()["n"] == 0
    d = prepare(db, user, item, "facts", FACTS)
    with db.connection() as c:
        result = enqueue(c, user, item, JobRequest(kind="model", revision=d["document"]["revision"],
                                                  payload={"provider": provider, "task": section}), settings)
        job = c.execute("SELECT * FROM jobs WHERE id=%s", (result["job_id"],)).fetchone()
        assert job["payload"]["context"]["facts"]["sales_name"] == FACTS["sales_name"]
        assert detail(c, user, item)["document"]["facts_version"]
        # This test verifies admission only; leave no runnable model work for
        # subsequent worker tests sharing the isolated session schema.
        c.execute("UPDATE jobs SET state='succeeded',finished_at=now() WHERE id=%s", (result["job_id"],))
    assert calls == [provider]
