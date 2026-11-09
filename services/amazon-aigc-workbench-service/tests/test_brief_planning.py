import json
from types import SimpleNamespace

import httpx
import pytest
from test_model_failures import model_settings
from test_workflow import FACTS, prepare

from amazon_aigc.domain import publish_brief
from amazon_aigc.model_gateway import GenerationError, generate
from amazon_aigc.sync import projection


def planning(count=7):
    return {
        "product": "组合产品",
        "selling_points": "已确认卖点",
        "visual_focus": "实际件数",
        "image_count": count,
        "plans": [
            {
                "purpose": "主图" if i == 0 else f"副图{i}",
                "composition": "展示已确认本品和数量",
                "text": "" if i == 0 else f"Feature {i}",
            }
            for i in range(count)
        ],
    }


@pytest.mark.parametrize("provider", ["deepseek", "openai"])
def test_both_providers_request_and_return_seven_complete_plans(monkeypatch, provider):
    candidate = planning()
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs["json"])
        return httpx.Response(
            200, json={"output": [{"content": [{"type": "output_text", "text": json.dumps(candidate)}]}]}
        )

    monkeypatch.setattr(httpx, "post", post)
    settings = model_settings().model_copy(update={"openai_api_key": "synthetic", "openai_model": "synthetic"})
    result = generate(settings, provider, "brief", {})
    schema = calls[0]["text"]["format"]["schema"]
    assert schema["properties"]["plans"]["minItems"] == schema["properties"]["plans"]["maxItems"] == 7
    assert "plans" in schema["required"]
    assert result["candidate"]["plans"] == candidate["plans"]


@pytest.mark.parametrize(
    "candidate",
    [
        {**planning(), "plans": []},
        {**planning(), "plans": planning(6)["plans"]},
        {**planning(), "plans": [{"purpose": "主图", "composition": ""}] * 7},
    ],
)
def test_missing_incomplete_or_wrong_number_plans_are_not_accepted(monkeypatch, candidate):
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **kw: httpx.Response(
            200, json={"output": [{"content": [{"type": "output_text", "text": json.dumps(candidate)}]}]}
        ),
    )
    with pytest.raises(GenerationError):
        generate(model_settings(), "deepseek", "brief", {})


def test_edited_plans_publish_with_matching_count_and_keep_old_version(db, setup):
    users, item, _ = setup
    user = users["operator"]
    prepare(db, user, item, "facts", FACTS)
    original = prepare(db, user, item, "brief", planning())
    revised = planning(6)
    revised["image_count"] = 7  # Old callers may carry a stale count; plans define the new delivery.
    current = prepare(db, user, item, "brief", revised)
    assert current["document"]["body"]["brief"]["image_count"] == 6
    assert (
        len(next(v for v in current["versions"] if v["id"] == original["document"]["brief_version"])["body"]["plans"])
        == 7
    )
    with db.connection() as c:
        publish_brief(c, user, item, current["document"]["revision"], {})
        assert projection(c, item, SimpleNamespace(s=SimpleNamespace(feishu_mode="openapi")))["计划制图数量"] == 6
