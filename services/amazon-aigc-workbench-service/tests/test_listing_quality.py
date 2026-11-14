import copy
import json
import shutil
import subprocess

import httpx
import pytest

from amazon_aigc import listing_quality as quality
from amazon_aigc import source_identity as identity
from amazon_aigc.brief_policy import review_fields
from amazon_aigc.config import Settings
from amazon_aigc.domain import detail, write_document
from amazon_aigc.model_gateway import generate
from amazon_aigc.schemas import DocumentWrite


def context():
    return {
        "site": "US",
        "facts": {
            "sales_name": "XK-108替换支架",
            "quantity": "2",
            "specification": "已确认适配 ZX200；USB-C接口，M6螺纹，使用CR2032电池；G9灯座",
            "material": "PET",
            "dimensions": "20 x 8 x 5 cm，0.3 kg",
            "included": "2个支架",
            "excluded": "不包含电源适配器",
            "uses": "固定小型设备",
        },
        "source_materials": [
            {
                "kind": "supplier",
                "title": "XK-108 G9 USB-C M6 CR2032支架",
                "specifications": ["型号: XK-108", "货号: GZ-007", "Model: ZX200, XK-108", "接口: USB-C"],
                "source_text": "Item model number: XK-108\nMPN: GZ-007",
                "paired_skus": [{"sku_id": "1234567890", "specification": "XK-108 黑色", "quantity": 2}],
            },
            {"kind": "competitor", "title": "AB-900 Replacement Bracket"},
        ],
    }


def listing():
    return {
        "title": "PET Brackets for ZX200",
        "highlights": "USB-C, M6 and CR2032 Compatibility",
        "bullets": [
            "Dimensions: 20 x 8 x 5 cm",
            "Weight: 0.3 kg",
            "Material: PET",
            "Package: Two brackets",
            "Compatibility: For ZX200",
        ],
        "description": "The power adapter is not included.",
        "terms": "equipment bracket",
    }


def test_identifier_projection_preserves_specs_confirmed_fitment_and_original_records():
    original = context()
    before = copy.deepcopy(original)
    projected, rules = identity.prepare_context(original)
    assert original == before
    text = json.dumps(projected, ensure_ascii=False)
    for forbidden in ("XK-108", "GZ-007", "AB-900", "1234567890"):
        assert forbidden not in text
    for retained in ("ZX200", "USB-C", "M6", "CR2032", "G9", "20 x 8 x 5 cm", "0.3 kg"):
        assert retained in text
    assert projected["facts"]["quantity"] == "2"
    assert projected["source_materials"][0]["paired_skus"][0]["quantity"] == 2
    assert not review_fields([("copy", "For ZX200, USB-C, M6, CR2032, G9")], rules)
    assert review_fields([("copy", "Use XK-108")], rules)
    assert not review_fields([("copy", "内部SKU：OWN-XK-108-US")], rules)


def test_server_identifier_patterns_are_executable_in_browser_unicode_regex():
    if not shutil.which("node"):
        pytest.skip("Node is required for the browser regex contract")
    rules = identity.policy([{"text": value, "kind": "source_model"} for value in ("XK-108", "SKU (9)", "NO.007")])
    subprocess.run(
        [
            "node",
            "-e",
            "const p=JSON.parse(process.argv[1]);for(const c of p.checks){const r=new RegExp(c.pattern,'giu');if(!r.test(c.message))process.exit(1)}",
            json.dumps(rules),
        ],
        check=True,
        capture_output=True,
    )


def test_source_only_fitment_is_not_promoted_to_confirmed_compatibility():
    data = context()
    data["facts"]["specification"] = "黑色"
    data["source_materials"][0]["description"] = "Compatible with ZX200"
    projected, rules = identity.prepare_context(data)
    assert "ZX200" not in json.dumps(projected)
    assert review_fields([("title", "Fits ZX200")], rules)


def test_fitment_exception_does_not_keep_own_model_or_unverified_target():
    data = context()
    data["facts"]["sales_name"] = "XK-108 Bracket for ZX200"
    data["facts"]["specification"] = "适配ZX200，产品型号:XK-108；待确认适配AB-900"
    projected, rules = identity.prepare_context(data)
    assert "XK-108" not in json.dumps(projected)
    assert "AB-900" not in json.dumps(projected)
    assert "ZX200" in projected["facts"]["sales_name"]
    assert review_fields([("copy", "XK-108")], rules)


def test_standard_models_quantities_and_dimensions_are_not_source_ids():
    data = {
        "facts": {"specification": "M6螺纹"},
        "source_materials": [
            {
                "title": "2-in-1 10-Pack A4 USB3.0 3D IP67 18650 CR2032 M6 SUS304",
                "specifications": ["型号: CR2032, M6", "货号: 120"],
            }
        ],
    }
    projected, rules = identity.prepare_context(data)
    assert not rules["checks"]
    assert "CR2032" in projected["source_materials"][0]["specifications"][0]
    assert "货号: 120" not in projected["source_materials"][0]["specifications"]


def test_fact_checks_distinguish_conversion_omission_and_unsupported_claims():
    data, ids = identity.prepare_context(context())
    rules = quality.policy(data, ids)
    candidate = listing()
    assert not quality.review(candidate, rules)["issues"]
    candidate["bullets"][0] = "Dimensions: 200 x 80 x 50 mm"
    candidate["bullets"][1] = "Weight: 300 g"
    assert not quality.review(candidate, rules)["issues"]
    candidate["bullets"][1] = "Weight: 0.301 kg"
    assert any(x["code"] == "unsupported_measurement" for x in quality.review(candidate, rules)["issues"])
    candidate["bullets"][1] = "Weight: 300 g"
    candidate["description"] = "Adapter sold separately, 12 V output"
    issues = quality.review(candidate, rules)["issues"]
    assert {x["code"] for x in issues} >= {"unsupported_sale", "unsupported_measurement"}
    candidate["bullets"][0] = "A useful bracket"
    issues = quality.review(candidate, rules)["issues"]
    assert {x["code"] for x in issues} >= {"bullet_format", "missing_detail"}


def test_coverage_requires_actual_quote_and_known_confirmed_fact():
    data, ids = identity.prepare_context(context())
    rules = quality.policy(data, ids)
    candidate = listing()
    coverage = [
        {"fact_id": "material", "status": "covered", "field": "bullets.2", "quote": "PET"},
        {"fact_id": "dimensions", "status": "covered", "field": "description", "quote": "fabricated quote"},
    ]
    review = quality.review(candidate, rules, coverage, require_coverage=True)
    missing = {x["field"] for x in review["issues"] if x["code"] == "coverage_unverified"}
    assert "facts.material" not in missing and "facts.dimensions" in missing
    assert "facts.included" in missing


@pytest.mark.parametrize("provider", ["openai", "deepseek"])
@pytest.mark.parametrize("task", ["listing", "brief"])
def test_shared_model_rules_use_one_call_and_surface_image_only_identifier_without_rewriting(
    monkeypatch, provider, task
):
    candidate = (
        listing()
        if task == "listing"
        else {
            "product": "设备支架",
            "selling_points": "实际数量",
            "visual_focus": "产品结构",
            "image_count": 7,
            "plans": [{"purpose": "细节", "composition": "ZZ-909", "text": ""} for _ in range(7)],
        }
    )
    if task == "listing":
        candidate["bullets"] = [
            {"heading": "Compatibility", "body": "For ZX200"},
            *[{"heading": "Details", "body": "PET brackets"} for _ in range(4)],
        ]
        candidate["highlights"] = "ZZ-909"
        candidate["fact_coverage"] = []
    response = {
        **candidate,
        "source_identifier_observations": [
            {"text": "ZZ-909", "kind": "source_model"},
            {"text": "M6", "kind": "source_model"},
        ],
    }
    calls = []

    def post(*args, **kwargs):
        payload = kwargs["json"]
        calls.append(payload)
        assert "XK-108" not in payload["input"] and "GZ-007" not in payload["input"]
        assert "ZX200" in payload["input"] and "CR2032" in payload["input"]
        if task == "listing":
            assert payload["text"]["format"]["schema"]["properties"]["bullets"]["items"]["type"] == "object"
        return httpx.Response(
            200, json={"output": [{"content": [{"type": "output_text", "text": json.dumps(response)}]}]}
        )

    monkeypatch.setattr(httpx, "post", post)
    settings = Settings(
        database_url="postgresql://unused/test",
        model_enabled=True,
        openai_api_key="synthetic",
        openai_model="synthetic",
        deepseek_api_key="synthetic",
        deepseek_model="synthetic",
    )
    result = generate(settings, provider, task, context())
    assert len(calls) == 1 and result["status"] == "succeeded"
    assert "ZZ-909" in json.dumps(result["candidate"])
    review = result[task + "_review"]
    assert any(x["code"] == "source_identifier" for x in review["issues"])
    assert not any("M6" == x["text"] for x in result["source_identity"]["marks"])
    if task == "listing":
        assert result["candidate"]["bullets"][0] == "Compatibility: For ZX200"
        assert "fact_coverage" not in result["candidate"]


def test_item_checks_use_confirmed_facts_and_do_not_block_manual_save_or_confirmation(db, setup):
    users, item, _ = setup
    with db.connection() as c:
        facts = context()["facts"]
        d = write_document(
            c, users["operator"], item, DocumentWrite(revision=0, section="facts", body=facts, confirm=True)
        )
        old_version = d["document"]["facts_version"]
        bad = listing()
        bad["description"] = "Adapter sold separately, 500 kg"
        d = write_document(
            c,
            users["operator"],
            item,
            DocumentWrite(revision=d["document"]["revision"], section="listing", body=bad, confirm=True),
        )
        assert d["document"]["facts_version"] == old_version
        assert d["document"]["body"]["listing"] == {**bad, "translations": {}}
        assert quality.review(bad, d["listing_policy"])["issues"]
        changed = {**facts, "dimensions": "500 kg"}
        d = write_document(
            c, users["operator"], item, DocumentWrite(revision=d["document"]["revision"], section="facts", body=changed)
        )
        assert not d["listing_policy"]["confirmed"]
        assert not d["listing_policy"]["measurements"]
        assert detail(c, users["operator"], item)["document"]["body"]["listing"] == d["document"]["body"]["listing"]


def test_adopted_candidate_review_uses_the_same_projected_fact_basis(db, setup):
    users, item, _ = setup
    with db.connection() as c:
        d = write_document(
            c,
            users["operator"],
            item,
            DocumentWrite(
                revision=0,
                section="sources",
                body=[{"kind": "supplier", "url": "https://example.com/product", "manual_text": "型号: XK-108"}],
            ),
        )
        d = write_document(
            c,
            users["operator"],
            item,
            DocumentWrite(revision=d["document"]["revision"], section="facts", body=context()["facts"], confirm=True),
        )
        projected, _ = identity.prepare_context(context())
        assert d["listing_policy"]["facts"] == quality.fact_items(projected)
        assert d["document"]["body"]["facts"]["sales_name"] == "XK-108替换支架"
        assert any(x["code"] == "source_identifier" for x in d["listing_policy"]["checks"])
