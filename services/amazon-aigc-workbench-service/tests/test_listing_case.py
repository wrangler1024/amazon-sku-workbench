import json

import httpx
import pytest

from amazon_aigc.config import Settings
from amazon_aigc.model_gateway import PROMPT_VERSION, generate


@pytest.mark.parametrize("provider,vision", [("openai", False), ("openai", True), ("deepseek", False)])
@pytest.mark.parametrize(
    "site,title,highlights",
    [
        (
            "US",
            "PET Storage Trays for Cosmetics and Small Accessories",
            "Clear PET Trays with Dividers for Pens and Brushes, 23 x 8 x 5 cm Each",
        ),
        (
            "MX",
            "Bandejas de PET para cosméticos y accesorios pequeños",
            "Bandejas transparentes de PET para bolígrafos y brochas, 23 x 8 x 5 cm cada una",
        ),
    ],
)
def test_listing_case_rules_reach_provider_and_preserve_language_exceptions(
    monkeypatch, provider, vision, site, title, highlights
):
    settings = Settings(
        database_url="postgresql://unused/test",
        model_enabled=True,
        openai_api_key="synthetic-key",
        openai_model="synthetic-model",
        deepseek_api_key="synthetic-key",
        deepseek_model="synthetic-model",
    )
    expected = {
        "title": title,
        "highlights": highlights,
        "bullets": ["Confirmed product detail"] * 5,
        "description": "Confirmed product description",
        "terms": "storage organisers",
    }
    response_text = json.dumps(expected)
    context = {"site": site, "facts": {"material": "PET", "dimensions": "23 x 8 x 5 cm"}}
    if vision:
        context["_vision_images"] = [
            {"image_id": "synthetic-image", "data_url": "data:image/png;base64,c3ludGhldGlj", "detail": "low"}
        ]
        context["_vision_report"] = {"images": []}
    requests = []

    def respond(url, *, headers, json, timeout):
        requests.append(json)
        rules = json["instructions"]
        assert "英文Title（商品标题）和Item Highlights（商品亮点）的整条内容必须采用Title Case" in rules
        assert "不能只把句首单词大写" in rules
        assert "小词在非首尾位置小写" in rules and "位于整条内容首尾时首字母大写" in rules
        assert "标准缩写及型号保持正确写法" in rules
        assert "单位和尺寸连接符x保留标准写法，优先于Title Case规则" in rules
        assert "墨西哥西语Title和Item Highlights按当地句子大小写" in rules
        assert "不套用英文Title Case" in rules
        assert "五点说明正文和长描述正文仍按正常句子大小写" in rules
        assert "默认白牌" not in rules  # The listing request must not use the artwork prompt.
        assert json["text"]["format"]["schema"]["properties"]["highlights"]["maxLength"] == 125
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [{"content": [{"type": "output_text", "text": response_text}]}],
            },
        )

    monkeypatch.setattr(httpx, "post", respond)
    result = generate(settings, provider, "listing", context)
    assert len(requests) == 1
    assert result["status"] == "succeeded"
    assert result["prompt_version"] == PROMPT_VERSION
    assert result["candidate"] == expected
    if provider == "openai" and vision:
        assert requests[0]["input"][0]["content"][1]["text"] == "图片ID：synthetic-image"
    else:
        assert json.loads(requests[0]["input"])["data"]["site"] == site
