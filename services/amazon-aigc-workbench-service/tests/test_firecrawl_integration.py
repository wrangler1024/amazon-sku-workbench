import asyncio
import json
from copy import deepcopy

import httpx
import pytest
from fastapi import HTTPException
from test_model_failures import model_settings
from test_workflow import BRIEF

from amazon_aigc.collector import CollectionError
from amazon_aigc.collector_config import key, reserve, save, status
from amazon_aigc.firecrawl_collector import PARSER_VERSION, collect, parse_response
from amazon_aigc.jobs import run_job
from amazon_aigc.model_gateway import GenerationError, generate, output_schema
from amazon_aigc.schemas import Brief

pytest_plugins = ["test_source_pipeline"]

URL = "https://www.amazon.ca/dp/B0TEST0007"
HTML = '<h1>Product summary</h1><span id="productTitle">Dog Treat Pouch</span><div id="feature-bullets"><li>Actual feature</li></div><div id="productDescription">Actual source description</div>'


def test_firecrawl_materials_are_normalized_without_changing_facts():
    result, _ = parse_response(
        {"rawHtml": HTML, "markdown": "Product details from source", "metadata": {"statusCode": 200, "url": URL}}, URL
    )
    assert result["title"] == "Dog Treat Pouch"
    assert result["bullets"] == ["Actual feature"]
    assert result["source_text"] == "Product details from source"
    assert result["variant_verified"] is False and result["collector"] == "firecrawl"


@pytest.mark.parametrize(
    "data",
    [
        {"metadata": {"statusCode": 403}},
        {"metadata": {"title": "Robot Check"}, "markdown": "captcha verification required"},
        {"metadata": {"url": "https://www.amazon.ca/dp/DIFFERENT1"}},
        {"metadata": {"url": "https://www.amazon.com/dp/B0TEST0007"}},
    ],
)
def test_firecrawl_rejects_challenges_and_wrong_product(data):
    with pytest.raises(CollectionError):
        parse_response(data, URL)


def test_firecrawl_fresh_request_and_sanitized_failures(monkeypatch, tmp_path):
    recorded = []

    async def post(self, url, **kwargs):
        recorded.append(kwargs["json"])
        return httpx.Response(401, text="secret-fc-private-and-provider-body")

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    with pytest.raises(CollectionError) as exc:
        asyncio.run(collect(URL, tmp_path, "fc-private-test-secret"))
    assert recorded[0]["maxAge"] == 0
    assert recorded[0]["formats"] == ["rawHtml", "markdown", "images"]
    assert "private" not in str(exc.value) and "private" not in json.dumps(exc.value.diagnostic)
    assert exc.value.diagnostic["http_status"] == 401
    assert exc.value.diagnostic["total_ms"] >= 0


def test_collector_secret_access_and_daily_limit(db, flow):
    users, item, settings, _calls, _save, _start, _read = flow
    admin = {**users["operator"], "roles": ["admin"]}
    try:
        with db.connection() as c, pytest.raises(HTTPException):
            save(c, users["operator"], settings, {"api_key": "fc-test-private", "enabled": True})
        with db.connection() as c:
            out = save(c, admin, settings, {"api_key": "fc-test-private", "enabled": True, "daily_limit": 1})
            encrypted = c.execute("SELECT key_ciphertext FROM collector_configs").fetchone()["key_ciphertext"]
            assert "fc-test-private" not in encrypted and "fc-test-private" not in str(out)
            assert status(c)["provider"] == "firecrawl" and key(c, settings) == "fc-test-private"
            save(c, admin, settings, {"api_key": "", "enabled": True, "daily_limit": 1})
            assert (
                reserve(c, settings, {"id": "test-job", "work_item_id": item, "actor_id": admin["id"]})
                == "fc-test-private"
            )
        with db.connection() as c, pytest.raises(CollectionError, match="上限"):
            reserve(c, settings, {"id": "second-job", "work_item_id": item, "actor_id": admin["id"]})
    finally:
        with db.connection() as c:
            c.execute("DELETE FROM collector_configs")
            c.execute("DELETE FROM audit_events WHERE action='collector.request' AND work_item_id=%s", (item,))


def test_enabled_firecrawl_recollects_native_cache_and_feeds_model(db, flow, monkeypatch):
    users, _item, settings, calls, write, start, read = flow
    write("sources", [{"id": "c", "kind": "competitor", "url": URL, "manual_text": "Confirmed operator notes"}])
    native = start()
    run_job(db, settings, "source")
    run_job(db, settings, "model")
    assert read(native["job_id"])["state"] == "succeeded"
    received = []

    async def firecrawl(url, storage, api_key):
        received.append(url)
        return {
            "title": "Actual Firecrawl title",
            "description": "Actual Firecrawl text",
            "source_text": "Markdown details",
            "specifications": [],
            "bullets": [],
            "status": "partial",
            "method": "firecrawl",
            "collector": "firecrawl",
            "snapshot_key": "test",
            "parser_version": PARSER_VERSION,
            "downloads": [],
            "page_ms": 120,
            "total_ms": 130,
        }

    monkeypatch.setattr("amazon_aigc.firecrawl_collector.collect", firecrawl)
    admin = {**users["operator"], "roles": ["admin"]}
    try:
        with db.connection() as c:
            save(c, admin, settings, {"api_key": "fc-test-private", "enabled": True})
        current = start("brief")
        run_job(db, settings, "source")
        run_job(db, settings, "model")
        row = read(current["job_id"])
        assert received == [URL] and row["state"] == "succeeded"
        assert calls[-1]["source_materials"][0]["description"] == "Actual Firecrawl text"
        assert row["result"]["collection"][0]["collector"] == "firecrawl"
    finally:
        with db.connection() as c:
            c.execute("DELETE FROM collector_configs")


def test_generation_requires_seven_plans_and_keeps_legacy_counts(monkeypatch):
    _, schema = output_schema("brief")
    assert schema["properties"]["plans"]["minItems"] == schema["properties"]["plans"]["maxItems"] == 7
    assert schema["properties"]["image_count"]["const"] == 7
    candidate = {**BRIEF, "plans": [deepcopy(BRIEF["plans"][0]) for _ in range(6)]}
    assert Brief.model_validate(candidate).image_count == 6
    assert Brief.model_validate({k: v for k, v in BRIEF.items() if k != "plans"}).image_count == 7
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **kw: httpx.Response(
            200,
            json={
                "id": "wrong-count",
                "output": [{"content": [{"type": "output_text", "text": json.dumps(candidate)}]}],
            },
        ),
    )
    with pytest.raises(GenerationError, match="校验"):
        generate(model_settings(), "deepseek", "brief", {})


def test_firecrawl_has_wall_clock_deadline_for_streaming_responses(monkeypatch, tmp_path):
    original = asyncio.timeout
    monkeypatch.setattr("amazon_aigc.firecrawl_collector.asyncio.timeout", lambda seconds: original(0.01))

    async def never_finishes(self, url, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(httpx.AsyncClient, "post", never_finishes)
    with pytest.raises(CollectionError, match="超时") as exc:
        asyncio.run(collect(URL, tmp_path, "fc-private-test-secret"))
    assert exc.value.diagnostic["total_ms"] < 3000
