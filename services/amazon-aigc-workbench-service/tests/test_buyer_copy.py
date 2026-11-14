import copy
import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from amazon_aigc import listing_quality as quality
from amazon_aigc.config import Settings
from amazon_aigc.model_gateway import generate


def context(uncertain=False):
    return {
        "site": "US",
        "facts": {
            "sales_name": "USB-C card reader",
            "quantity": "1",
            "material": "PVC",
            "specification": "USB-C plug, SD slot, TF slot and USB port",
            "dimensions": "18 x 6 x 2 cm" + ("，尚未确认是产品还是包装尺寸" if uncertain else ""),
            "included": "1 card reader",
        },
    }


def candidate():
    return {
        "title": "USB-C Card Reader",
        "highlights": "SD and TF Slots with a USB Port",
        "bullets": [
            "Connection: USB-C plug",
            "Slots: SD and TF slots",
            "Port: USB port",
            "Material: PVC",
            "Package: One card reader",
        ],
        "description": "Dimensions: 18 x 6 x 2 cm",
        "terms": "card reader",
    }


@pytest.mark.parametrize(
    "text",
    [
        "Compatible device models and simultaneous operation require confirmation.",
        "Supplied dimensions: 18 x 6 x 2 cm. Confirm whether these refer to the product or packaging and which sides they measure.",
        "Dimensions: Not specified",
        "Compatibility is to be confirmed",
        "Medidas pendientes de confirmación",
        "Dimensiones por confirmar",
        "产品尺寸待核实，请运营确认",
    ],
)
def test_internal_review_language_is_flagged_without_mutating_copy(text):
    value = candidate()
    value["description"] = text
    before = copy.deepcopy(value)
    issues = quality.review(value, quality.policy(context()))["issues"]
    assert any(x["code"] == "internal_review" and x["field"] == "description" for x in issues)
    assert value == before


@pytest.mark.parametrize(
    "text",
    [
        "Dimensions: 18 x 6 x 2 cm",
        "Check your device port before purchase.",
        "Confirm whether these dimensions fit your drawer.",
        "Adult supervision is required. Not compatible with micro-USB ports.",
        "Comprueba el puerto de tu dispositivo antes de comprar.",
        "Batteries and memory cards are not included.",
    ],
)
def test_buyer_instructions_and_confirmed_limits_are_allowed(text):
    value = candidate()
    value["description"] = text
    assert not any(x["code"] == "internal_review" for x in quality.review(value, quality.policy(context()))["issues"])


def test_uncertain_measurements_are_not_required_coverage_or_safe_claims():
    ctx = context(True)
    before = copy.deepcopy(ctx)
    rules = quality.policy(ctx)
    assert ctx == before
    assert not rules["measurements"] and rules["unconfirmed_measurements"]
    value = candidate()
    value["description"] = "PVC reader with a USB-C plug."
    issues = quality.review(value, rules)["issues"]
    assert any(x["code"] == "fact_needs_confirmation" for x in issues)
    assert not any(x["code"] in ("missing_detail", "unconfirmed_measurement") for x in issues)
    # Removing the operational qualifier does not make the unresolved number safe.
    value["description"] = "Dimensions: 180 x 60 x 20 mm"
    assert any(x["code"] == "unconfirmed_measurement" for x in quality.review(value, rules)["issues"])
    # After the facts are actually resolved, the same size is accepted.
    assert not quality.review(value, quality.policy(context()))["issues"]


@pytest.mark.parametrize("provider", ["openai", "deepseek"])
def test_review_notes_remain_outside_candidate_with_one_model_call(monkeypatch, provider):
    raw = candidate()
    raw["description"] = "PVC reader with a USB-C plug."
    raw["bullets"] = [{"heading": "Connection", "body": "USB-C plug"} for _ in range(5)]
    raw.update(
        fact_coverage=[],
        source_identifier_observations=[],
        review_notes=[
            {
                "fact_id": "dimensions",
                "reason": "尺寸归属待核实，已从销售文案省略",
            }
        ],
    )
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs["json"])
        schema = kwargs["json"]["text"]["format"]["schema"]
        assert "review_notes" in schema["required"]
        assert "uncertain" in kwargs["json"]["input"]
        return httpx.Response(200, json={"output": [{"content": [{"type": "output_text", "text": json.dumps(raw)}]}]})

    monkeypatch.setattr(httpx, "post", post)
    settings = Settings(
        database_url="postgresql://unused/test",
        model_enabled=True,
        openai_api_key="synthetic",
        openai_model="synthetic",
        deepseek_api_key="synthetic",
        deepseek_model="synthetic",
    )
    result = generate(settings, provider, "listing", context(True))
    assert len(calls) == 1
    assert "review_notes" not in result["candidate"]
    assert "待核实" not in json.dumps(result["candidate"], ensure_ascii=False)
    assert result["listing_review"]["notes"][0]["fact_id"] == "dimensions"
    assert any(x["code"] == "operator_review" for x in result["listing_review"]["issues"])


def test_browser_uses_real_server_policy_for_plain_html_and_uncertain_copy():
    if not shutil.which("node"):
        pytest.skip("Node required for cross-runtime copy contract")
    path = Path(__file__).resolve().parents[3] / "apps/editor/src/listingQuality.ts"
    script = """
import assert from 'node:assert/strict';
const {copyReviewedListing}=await import(process.argv[1]);
const [known,unknown]=JSON.parse(process.argv[2]), writes=[];
for (const text of ['Compatible models require confirmation.', '<p>Requires <b>confirmation</b></p>']) {
 await assert.rejects(copyReviewedListing(text,known,async s=>writes.push(s)),/内部核对/);
}
await assert.rejects(copyReviewedListing('Dimensions: 180 x 60 x 20 mm',unknown,async s=>writes.push(s)),/待确认数值/);
await copyReviewedListing('Dimensions: 180 x 60 x 20 mm',known,async s=>writes.push(s));
assert.deepEqual(writes,['Dimensions: 180 x 60 x 20 mm']);
"""
    subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            script,
            str(path),
            json.dumps([quality.policy(context()), quality.policy(context(True))]),
        ],
        check=True,
        capture_output=True,
    )


def test_mixed_fact_keeps_confirmed_measurement_and_isolates_unconfirmed_part():
    ctx = context()
    ctx["facts"]["dimensions"] = "18 x 6 x 2 cm；重量0.03 kg待确认"
    rules = quality.policy(ctx)
    assert {m["family"] for m in rules["measurements"]} == {"length"}
    assert {m["family"] for m in rules["unconfirmed_measurements"]} == {"weight"}
    value = candidate()
    issues = quality.review(value, rules)["issues"]
    assert not any(x["code"] in ("unconfirmed_measurement", "missing_detail") for x in issues)
    value["description"] += "; weight 30 g"
    assert any(x["code"] == "unconfirmed_measurement" for x in quality.review(value, rules)["issues"])
