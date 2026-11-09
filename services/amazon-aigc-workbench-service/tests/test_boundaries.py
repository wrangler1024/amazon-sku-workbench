import pytest
from pydantic import ValidationError

from amazon_aigc.collector import CollectionError, parse_html, validate_url
from amazon_aigc.config import PRODUCTION_BASE, Settings
from amazon_aigc.model_gateway import DeepSeekProvider, OpenAIProvider, instructions, output_schema
from amazon_aigc.schemas import Facts


def test_production_target_is_refused():
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql://localhost/test", base_id=PRODUCTION_BASE)


@pytest.mark.parametrize(
    "url",
    [
        "http://detail.1688.com/offer/1.html",
        "https://127.0.0.1/x",
        "https://169.254.169.254/latest",
        "https://amazon.com.evil.test/dp/X",
        "https://user:pass@amazon.com/dp/X",
        "https://amazon.com:8443/",
    ],
)
def test_source_network_boundary(url):
    with pytest.raises(CollectionError):
        validate_url(url, resolve=False)


def test_dynamic_fallback_does_not_assume_specifications():
    result = parse_html(
        '<h1>Clear tray</h1><meta property="og:image" content="https://m.media-amazon.com/i.jpg">',
        "https://www.amazon.com/dp/TEST",
    )
    assert result["status"] == "partial" and result["variant_verified"] is False


def test_captcha_classified():
    with pytest.raises(CollectionError):
        parse_html("<p>Enter the characters you see below</p>", "https://amazon.com/dp/X")


def test_bundle_requires_every_component_confirmation():
    with pytest.raises(ValidationError):
        Facts(product_type="bundle", sales_name="套装", quantity="1套", included="多个配件")


def test_provider_payloads_are_separate():
    _, schema = output_schema("listing")
    o = OpenAIProvider().payload("configured", "listing", {}, schema)
    d = DeepSeekProvider().payload("configured", "listing", {}, schema)
    assert o["store"] is False and "store" not in d
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_openai_can_use_supported_region_gateway():
    settings = Settings(
        database_url="postgresql://localhost/test",
        openai_base_url="https://lax.example.test/amazon-aigc/openai",
    )
    assert OpenAIProvider().url(settings) == "https://lax.example.test/amazon-aigc/openai/v1/responses"


def test_listing_prompt_uses_keywords_and_search_term_budget():
    prompt = instructions("listing")
    assert "运营已选关键词" in prompt
    assert "65—75字符" in prompt
    assert "105—125字符" in prompt
    assert "200—245 UTF-8字节" in prompt


def test_amazon_accessibility_heading_is_not_product_title():
    result = parse_html(
        '<h1>Product summary presents key product information</h1><span id="productTitle">Real product name</span>',
        "https://www.amazon.com.au/dp/X",
    )
    assert result["title"] == "Real product name"
