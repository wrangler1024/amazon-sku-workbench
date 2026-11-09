import base64
import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from amazon_aigc import openai_relay
from amazon_aigc.openai_relay import (
    Config,
    DailyCounter,
    bearer_authorized,
    validate_payload,
)


def relay_config(tmp_path, daily_limit=2):
    return Config(
        openai_api_key="synthetic-upstream-key-value",
        relay_token="synthetic-relay-token-value-32-bytes-minimum",
        models=frozenset({"gpt-6.1-terra"}),
        daily_limit=daily_limit,
        state_file=tmp_path / "daily-count.json",
    )


def valid_payload():
    return {
        "model": "gpt-6.1-terra",
        "instructions": "synthetic instructions",
        "input": json.dumps({"task": "listing", "data": {}}),
        "store": False,
        "max_output_tokens": 8000,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "amazon_listing",
                "schema": {"type": "object"},
                "strict": True,
            }
        },
    }


def test_relay_authentication_is_exact():
    token = "x" * 32
    assert bearer_authorized("Bearer " + token, token)
    assert not bearer_authorized("Bearer " + token + "x", token)
    assert not bearer_authorized("Basic " + token, token)
    assert not bearer_authorized(None, token)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"model": "another-model"}, "model"),
        ({"store": True}, "store"),
        ({"max_output_tokens": 16001}, "max_output_tokens"),
        ({"tools": []}, "unsupported"),
        (
            {
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "amazon_brief",
                        "schema": {"type": "object"},
                        "strict": True,
                    }
                }
            },
            "schema",
        ),
    ],
)
def test_relay_rejects_non_allowlisted_payloads(tmp_path, change, message):
    payload = {**valid_payload(), **change}
    with pytest.raises(ValueError, match=message):
        validate_payload(payload, relay_config(tmp_path))


def test_relay_accepts_structured_task_payload(tmp_path):
    validated = validate_payload(valid_payload(), relay_config(tmp_path))
    assert validated["store"] is False
    assert validated["model"] == "gpt-6.1-terra"


def test_daily_counter_persists_and_fails_closed(tmp_path):
    path = tmp_path / "counter.json"
    counter = DailyCounter(path, 2)
    assert counter.consume()
    assert counter.consume()
    assert not counter.consume()
    assert counter.snapshot()["used"] == 2

    restarted = DailyCounter(path, 2)
    assert not restarted.consume()
    assert restarted.snapshot()["used"] == 2


def test_relay_http_surface_is_allowlisted_and_authenticated(tmp_path, monkeypatch):
    class FakeUpstream:
        def __init__(self, config):
            self.config = config

        def request(self, path, method="GET", payload=None):
            if path == "/v1/models":
                body = {
                    "object": "list",
                    "data": [{"id": "gpt-6.1-terra"}, {"id": "blocked-model"}],
                }
            else:
                assert payload["store"] is False
                if isinstance(payload["input"], list):
                    assert payload["input"][0]["content"][1]["type"] == "input_image"
                body = {"id": "resp_test", "status": "completed", "usage": {"total_tokens": 10}}
            return 200, json.dumps(body).encode()

    monkeypatch.setattr(openai_relay, "OpenAIUpstream", FakeUpstream)
    config = relay_config(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), openai_relay.make_handler(config))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, path, body=None, authorized=False):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        headers = {}
        if authorized:
            headers["Authorization"] = "Bearer " + config.relay_token
        if body is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(body)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        payload = json.loads(raw) if raw else None
        connection.close()
        return response.status, payload

    try:
        assert request("GET", "/health")[0] == 200
        assert request("GET", "/v1/models")[0] == 401
        status, models = request("GET", "/v1/models", authorized=True)
        assert status == 200
        assert [item["id"] for item in models["data"]] == ["gpt-6.1-terra"]
        status, response = request(
            "POST",
            "/v1/responses",
            body=valid_payload(),
            authorized=True,
        )
        assert status == 200
        assert response["id"] == "resp_test"
        from test_vision import jpeg

        multimodal = valid_payload()
        multimodal["input"] = [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": valid_payload()["input"]},
                    {
                        "type": "input_image",
                        "detail": "high",
                        "image_url": "data:image/jpeg;base64," + base64.b64encode(jpeg()).decode(),
                    },
                ],
            }
        ]
        assert request("POST", "/v1/responses", multimodal, authorized=True)[0] == 200
        assert request("POST", "/v1/responses", multimodal, authorized=True)[0] == 429
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_unlimited_relay_keeps_usage_across_restarts(tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    path = tmp_path / "unlimited-counter.json"
    path.write_text(json.dumps({"day": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(), "count": 2001}))
    counter = DailyCounter(path, 0, "Asia/Shanghai")
    assert counter.snapshot()["used"] == 2001 and counter.consume()
    restarted = DailyCounter(path, 0, "Asia/Shanghai")
    assert restarted.snapshot()["used"] == 2002 and restarted.consume()
    assert restarted.snapshot()["limit"] == 0 and restarted.snapshot()["used"] == 2003
    assert not DailyCounter(path, 2000, "Asia/Shanghai").consume()


def test_relay_environment_accepts_explicit_unlimited_limit(monkeypatch):
    monkeypatch.setenv("AIGC_OPENAI_API_KEY", "synthetic-upstream-key-value")
    monkeypatch.setenv("AIGC_RELAY_TOKEN", "synthetic-relay-token-value-32-bytes-minimum")
    monkeypatch.setenv("AIGC_OPENAI_ALLOWED_MODELS", "gpt-6.1-terra")
    monkeypatch.setenv("AIGC_RELAY_DAILY_LIMIT", "0")
    monkeypatch.setenv("AIGC_RELAY_MAX_CONCURRENCY", "8")
    config = Config.from_env()
    assert config.daily_limit == 0 and config.max_concurrency == 8
