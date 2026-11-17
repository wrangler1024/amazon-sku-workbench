import copy
import json

import httpx
import pytest
from test_market_editor import BRIEF
from test_model_failures import model_settings
from test_workflow import LISTING

from amazon_aigc.market_editor import TARGETS
from amazon_aigc.model_gateway import GenerationError, generate


@pytest.mark.parametrize("provider", ["openai", "deepseek"])
@pytest.mark.parametrize("task", ["listing", "brief"])
def test_one_call_exact_selected_markets_and_shared_design(monkeypatch, provider, task):
    settings = model_settings().model_copy(update={"openai_api_key": "synthetic", "openai_model": "synthetic"})
    selected = ["DE", "FR", "ES", "IT", "NL"]
    common = {**BRIEF, "plans": [BRIEF["plans"][0]] + [BRIEF["plans"][1]] * 6, "image_count": 7}
    raw = {"listings": {s: {**LISTING, "fact_coverage": [], "review_notes": []} for s in selected}} if task == "listing" else {"shared_brief": common, "image_texts": {s: [""] + ["Material " + s] * 6 for s in selected}}
    calls = []

    def respond(url, **kwargs):
        payload = kwargs["json"]
        calls.append(payload)
        assert "欧洲多站计划" in payload["instructions"] and "不套用英文Title Case" in payload["instructions"]
        schema = payload["text"]["format"]["schema"]
        assert schema["additionalProperties"] is False
        assert set(schema["properties"]["listings" if task == "listing" else "image_texts"]["required"]) == set(TARGETS)
        assert payload["max_output_tokens"] >= 16000
        return httpx.Response(200, json={"status": "completed", "output": [{"content": [{"type": "output_text", "text": json.dumps(raw)}]}]})

    monkeypatch.setattr(httpx, "post", respond)
    result = generate(settings, provider, task, {"site": "DE", "editor_market_targets": selected, "facts": {"material": "PP"}})
    assert len(calls) == 1 and result["status"] == "succeeded"
    assert set(result["candidate"]["listings" if task == "listing" else "image_texts"]) == set(selected)
    if task == "listing":
        assert "review_notes" not in result["candidate"]["listings"]["DE"]
        assert set(result["market_listing_reviews"]) == set(selected)
    else:
        assert result["candidate"]["shared_brief"]["plans"][0]["text"] == ""
        assert set(result["market_brief_reviews"]) == set(selected)


def test_missing_or_duplicate_market_never_becomes_partial_candidate(monkeypatch):
    raw = {"listings": {"DE": copy.deepcopy(LISTING)}}
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: httpx.Response(200, json={"output": [{"content": [{"type": "output_text", "text": json.dumps(raw)}]}]}))
    with pytest.raises(GenerationError):
        generate(model_settings(), "deepseek", "listing", {"editor_market_targets": ["DE", "FR"]})
