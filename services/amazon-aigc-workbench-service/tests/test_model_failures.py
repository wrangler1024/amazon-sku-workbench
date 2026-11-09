import json
from uuid import uuid4

import httpx
import pytest
from psycopg.types.json import Jsonb

from amazon_aigc.config import Settings
from amazon_aigc.jobs import run_job
from amazon_aigc.model_gateway import DeepSeekProvider, GenerationError, generate, output_schema


def model_settings():
    return Settings(
        database_url="postgresql://unused/test",
        model_enabled=True,
        deepseek_api_key="synthetic-private-key",
        deepseek_model="deepseek-flash",
    )


def test_brief_has_own_prompt_reasoning_and_output_budget():
    _, schema = output_schema("brief")
    p = DeepSeekProvider().payload("deepseek-flash", "brief", {}, schema)
    assert p["reasoning"]["effort"] == "low"
    assert p["max_output_tokens"] == 393216
    assert "用中文" in p["instructions"]
    assert "Title不超过75字符" not in p["instructions"]


def test_incomplete_keeps_only_safe_diagnostics(monkeypatch):
    response = {
        "status": "incomplete",
        "id": "request-123",
        "incomplete_details": {"reason": "max_output_tokens"},
        "usage": {
            "output_tokens": 8000,
            "output_tokens_details": {"reasoning_tokens": 7600},
            "secret": "synthetic-private-key",
        },
        "output": [{"type": "reasoning", "content": [{"type": "reasoning_text", "text": "do not log private output"}]}],
    }
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: httpx.Response(200, json=response))
    with pytest.raises(GenerationError) as exc:
        generate(model_settings(), "deepseek", "brief", {})
    d = exc.value.diagnostic
    assert d["request_id"] == "request-123" and d["incomplete_reason"] == "max_output_tokens"
    assert d["usage"]["output_tokens"] == 8000
    assert "private" not in json.dumps(d)


def test_failed_call_and_job_are_recorded_without_retry(db, setup, monkeypatch):
    users, item, _ = setup
    job = uuid4()
    with db.connection() as c:
        c.execute(
            "INSERT INTO jobs(id,kind,work_item_id,actor_id,payload) VALUES(%s,'model',%s,%s,%s)",
            (
                job,
                item,
                users["operator"]["id"],
                Jsonb({"provider": "deepseek", "task": "brief", "context": {}, "revision": 0}),
            ),
        )
    calls = []

    def incomplete(*args, **kwargs):
        calls.append(1)
        return httpx.Response(
            200,
            json={
                "id": "failed-req-01",
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "usage": {"input_tokens": 200, "output_tokens": 16000},
            },
        )

    monkeypatch.setattr(httpx, "post", incomplete)
    monkeypatch.setattr("amazon_aigc.provider_config.runtime", lambda c, s, p: s)
    run_job(db, model_settings(), "model")
    with db.connection() as c:
        row = c.execute("SELECT state,result FROM jobs WHERE id=%s", (job,)).fetchone()
        log = c.execute("SELECT request_id,status,usage FROM model_calls WHERE job_id=%s", (job,)).fetchone()
    assert row["state"] == "failed" and "candidate" not in row["result"]
    assert log["request_id"] == "failed-req-01" and log["status"] == "incomplete"
    assert log["usage"]["output_tokens"] == 16000
    run_job(db, model_settings(), "model")
    assert len(calls) == 1


def test_transport_error_does_not_expose_key(monkeypatch):
    def failed(*args, **kwargs):
        raise httpx.ConnectError("synthetic-private-key")

    monkeypatch.setattr(httpx, "post", failed)
    with pytest.raises(GenerationError) as exc:
        generate(model_settings(), "deepseek", "brief", {})
    assert "synthetic-private-key" not in str(exc.value)
    assert "synthetic-private-key" not in str(exc.value.diagnostic)


def test_listing_has_bounded_reasoning_and_no_translation_output(monkeypatch):
    from amazon_aigc.model_gateway import output_schema

    _, schema = output_schema("listing")
    assert "translations" not in schema["properties"]
    assert "translations" not in schema["required"]
    payload = DeepSeekProvider().payload("deepseek-flash", "listing", {}, schema)
    assert "不附中文翻译" in payload["instructions"]
    assert payload["max_output_tokens"] == 393216
    assert payload["reasoning"] == {"effort": "low"}
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(
            200, json={"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}
        ),
    )
    with pytest.raises(GenerationError, match="文案未完整返回"):
        generate(model_settings(), "deepseek", "listing", {})
