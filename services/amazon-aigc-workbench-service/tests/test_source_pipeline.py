from copy import deepcopy
from uuid import UUID

import pytest
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from test_workflow import FACTS, prepare

from amazon_aigc.collector import PARSER_VERSION
from amazon_aigc.config import Settings
from amazon_aigc.domain import detail, write_document
from amazon_aigc.jobs import enqueue, run_job
from amazon_aigc.schemas import Command, DocumentWrite, JobRequest
from amazon_aigc.source_pipeline import resume, source_inputs

SUPPLIER = "https://detail.1688.com/offer/900000000002.html"
COMPETITOR = "https://www.amazon.ca/dp/B0TEST0007"
FAILED_SUPPLIER = "https://detail.1688.com/offer/123456789.html"


@pytest.fixture
def flow(db, setup, monkeypatch, tmp_path):
    users, item, _ = setup
    user = users["operator"]
    prepare(db, user, item, "facts", FACTS)
    settings = Settings(database_url="postgresql://unused/test", model_enabled=True, storage_root=tmp_path)
    monkeypatch.setattr("amazon_aigc.provider_config.runtime", lambda c, s, p: s)
    calls = []

    def model(s, provider, task, context):
        calls.append(deepcopy(context))
        return {"provider": provider, "model": "test-model", "usage": {}, "request_id": "test-request", "candidate": {}}

    async def collect(url, storage, render_enabled):
        if url in (COMPETITOR, FAILED_SUPPLIER):
            raise ValueError("需要验证码或人工验证")
        return {
            "title": "Supplier actual title",
            "specifications": ["Material: PET"],
            "bullets": ["Actual source benefit"],
            "description": "Actual supplier description",
            "status": "partial",
            "method": "http",
            "snapshot_key": "sources/test/page.html",
            "parser_version": PARSER_VERSION,
            "downloads": [],
        }

    monkeypatch.setattr("amazon_aigc.jobs.generate", model)
    monkeypatch.setattr("amazon_aigc.jobs.collect", collect)

    def save(section, body):
        with db.connection() as c:
            rev = detail(c, user, item)["document"]["revision"]
            return write_document(c, user, item, DocumentWrite(revision=rev, section=section, body=body))

    def start(task="listing", extra=None):
        with db.connection() as c:
            rev = detail(c, user, item)["document"]["revision"]
            return enqueue(
                c,
                user,
                item,
                JobRequest(kind="model", revision=rev, payload={"provider": "deepseek", "task": task, **(extra or {})}),
                settings,
            )

    def read(jid):
        with db.connection() as c:
            return c.execute("SELECT * FROM jobs WHERE id=%s", (jid,)).fetchone()

    return users, item, settings, calls, save, start, read


def test_collection_precedes_model_and_success_is_in_prompt(db, flow):
    _users, item, settings, calls, save, start, read = flow
    save("sources", [{"id": "s1", "kind": "supplier", "url": SUPPLIER}])
    job = start()
    assert job["state"] == "waiting_sources"
    run_job(db, settings, "model")
    assert calls == []
    run_job(db, settings, "source")
    run_job(db, settings, "model")
    assert read(job["job_id"])["state"] == "succeeded"
    assert calls[0]["source_materials"][0]["description"] == "Actual supplier description"
    assert calls[0]["facts"]["included"] == "4个托盘"
    assert "Material: PET" not in str(calls[0]["facts"])
    with db.connection() as c:
        assert (
            c.execute("SELECT count(*) AS n FROM source_snapshots WHERE work_item_id=%s", (item,)).fetchone()["n"] == 1
        )
    # Second content task reuses this item's successful source, and receives the same evidence.
    second = start("brief")
    run_job(db, settings, "model")
    assert read(second["job_id"])["result"]["collection"][0]["reused"] is True
    assert len(calls) == 2


def test_failure_pauses_then_manual_supplement_reaches_model(db, flow):
    users, item, settings, calls, save, start, read = flow
    rows = [{"id": "s1", "kind": "supplier", "url": SUPPLIER}, {"id": "c1", "kind": "supplier", "url": FAILED_SUPPLIER}]
    save("sources", rows)
    job = start(extra={"allow_confirmed_only": True, "context": {"facts": {"included": "forged"}}})
    run_job(db, settings, "source")
    run_job(db, settings, "source")
    run_job(db, settings, "model")
    assert read(job["job_id"])["state"] == "waiting_input" and not calls
    assert start()["job_id"] == job["job_id"]
    rows[1]["manual_text"] = "手动粘贴的场景参考，不是本品规格"
    d = save("sources", rows)
    with db.connection() as c, pytest.raises(HTTPException):
        resume(
            c,
            users["other"],
            item,
            UUID(job["job_id"]),
            Command(revision=d["document"]["revision"], data={"mode": "manual"}),
        )
    with db.connection() as c:
        resume(
            c,
            users["operator"],
            item,
            UUID(job["job_id"]),
            Command(revision=d["document"]["revision"], data={"mode": "manual"}),
        )
    run_job(db, settings, "model")
    result = read(job["job_id"])
    assert result["state"] == "succeeded" and result["result"]["document_revision"] == d["document"]["revision"]
    assert calls[0]["source_materials"][1]["manual_text"] == rows[1]["manual_text"]
    assert calls[0]["source_materials"][1]["collection_state"] == "failed"
    assert calls[0]["facts"]["included"] == "4个托盘"


def test_confirmed_only_requires_explicit_resume(db, flow):
    users, item, settings, calls, save, start, read = flow
    save("sources", [{"id": "c", "kind": "supplier", "url": FAILED_SUPPLIER}])
    job = start()
    run_job(db, settings, "source")
    run_job(db, settings, "model")
    with db.connection() as c:
        rev = detail(c, users["operator"], item)["document"]["revision"]
        with pytest.raises(HTTPException):
            resume(c, users["operator"], item, UUID(job["job_id"]), Command(revision=rev, data={"mode": "manual"}))
    with db.connection() as c:
        resume(c, users["operator"], item, UUID(job["job_id"]), Command(revision=rev, data={"mode": "confirmed_facts"}))
    run_job(db, settings, "model")
    assert read(job["job_id"])["state"] == "succeeded"
    assert calls[0]["source_materials"][0]["collection_state"] == "failed"
    assert "title" not in calls[0]["source_materials"][0]


def test_changed_input_stops_stale_generation(db, flow):
    _users, _item, settings, calls, save, start, read = flow
    save("sources", [{"id": "s", "kind": "supplier", "url": SUPPLIER}])
    job = start()
    run_job(db, settings, "source")
    save("keywords", "changed keywords")
    run_job(db, settings, "model")
    assert read(job["job_id"])["state"] == "failed" and not calls


def test_component_urls_deduplicate_and_join_both_tasks(db, flow):
    _users, item, settings, calls, _save, start, read = flow
    with db.connection() as c:
        d = c.execute("SELECT * FROM content_documents WHERE work_item_id=%s", (item,)).fetchone()
        body = d["body"]
        body["facts"]["components"] = [{"id": str(n), "url": SUPPLIER} for n in range(6)]
        c.execute("UPDATE content_documents SET body=%s WHERE id=%s", (Jsonb(body), d["id"]))
    one = start()
    two = start("brief")
    assert (
        read(one["job_id"])["payload"]["source_jobs"][0]["job_id"]
        == read(two["job_id"])["payload"]["source_jobs"][0]["job_id"]
    )
    run_job(db, settings, "source")
    run_job(db, settings, "model")
    run_job(db, settings, "model")
    assert len(calls) == 2
    assert (
        len(
            source_inputs(
                {"sources": [{"id": "a", "url": SUPPLIER}], "facts": {"components": [{"id": "b", "url": SUPPLIER}]}}
            )
        )
        == 1
    )


def test_invalid_link_becomes_visible_failure_without_fetch(db, flow):
    users, item, settings, calls, save, start, read = flow
    save("sources", [{"id": "x", "url": "http://127.0.0.1/private", "kind": "supplier"}])
    job = start()
    run_job(db, settings, "model")
    assert read(job["job_id"])["state"] == "waiting_input" and not calls
    with db.connection() as c:
        report = detail(c, users["operator"], item)["jobs"][0]
        # timestamps may order parent/child equally; locate by ID.
        report = next(j for j in detail(c, users["operator"], item)["jobs"] if j["id"] == job["job_id"])
        assert "HTTPS" in report["collection"][0]["error"]


def test_failed_source_can_retry_without_duplicate_model_call(db, flow, monkeypatch):
    users, item, settings, calls, save, start, read = flow
    save("sources", [{"id": "c", "kind": "supplier", "url": FAILED_SUPPLIER}])
    job = start()
    run_job(db, settings, "source")
    run_job(db, settings, "model")
    old_source = read(job["job_id"])["payload"]["source_jobs"][0]["job_id"]

    async def recovered(*args):
        return {
            "title": "Recovered competitor",
            "description": "Scenario reference",
            "specifications": [],
            "bullets": [],
            "status": "partial",
            "method": "http",
            "snapshot_key": "test",
            "parser_version": PARSER_VERSION,
            "downloads": [],
        }

    monkeypatch.setattr("amazon_aigc.jobs.collect", recovered)
    with db.connection() as c:
        rev = detail(c, users["operator"], item)["document"]["revision"]
        resume(c, users["operator"], item, UUID(job["job_id"]), Command(revision=rev, data={"mode": "retry"}))
    assert read(job["job_id"])["payload"]["source_jobs"][0]["job_id"] != old_source
    run_job(db, settings, "model")
    assert not calls
    run_job(db, settings, "source")
    run_job(db, settings, "model")
    assert read(job["job_id"])["state"] == "succeeded" and len(calls) == 1
    assert calls[0]["source_materials"][0]["title"] == "Recovered competitor"


def test_migrations_ignore_os_metadata(db, tmp_path):
    (tmp_path / "._004_source_pipeline.sql").write_bytes(b"\x00\xff\xa3")
    db.migrate(tmp_path)


def test_optional_competitor_failure_does_not_block_confirmed_product(db, flow):
    _users, _item, settings, calls, save, start, read = flow
    save("sources", [{"id": "c", "kind": "competitor", "url": COMPETITOR}])
    job = start()
    run_job(db, settings, "source")
    run_job(db, settings, "model")
    assert read(job["job_id"])["state"] == "succeeded" and len(calls) == 1
    material = calls[0]["source_materials"][0]
    assert material["collection_state"] == "failed" and "title" not in material
    assert material["reference_scope"] == "optional_expression_and_composition_reference"
    assert calls[0]["facts"]["included"] == "4个托盘"


def test_generation_without_optional_reference(db, flow):
    _users, _item, settings, calls, _save, start, read = flow
    job = start()
    run_job(db, settings, "model")
    assert read(job["job_id"])["state"] == "succeeded" and len(calls) == 1
    assert calls[0]["source_materials"] == []

@pytest.mark.parametrize('task', ['listing', 'brief'])
def test_competitor_notes_reach_each_model_task_even_if_collection_fails(db, flow, task):
    _, _, settings, calls, save, start, read = flow
    note = '参考竞品的排版、标题关键词和五点组织，不借用规格或品牌'
    save('sources', [{'id': 'c', 'kind': 'competitor', 'url': COMPETITOR, 'manual_text': note}])
    job = start(task)
    run_job(db, settings, 'source')
    run_job(db, settings, 'model')
    assert read(job['job_id'])['state'] == 'succeeded'
    assert calls[0]['source_materials'][0]['manual_text'] == note
    assert calls[0]['source_materials'][0]['reference_scope'] == 'optional_expression_and_composition_reference'
    assert calls[0]['facts']['included'] == '4个托盘'
