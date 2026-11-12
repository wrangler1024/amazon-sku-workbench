import copy
import json

import httpx
import pytest
from test_model_failures import model_settings

from amazon_aigc import brief_policy as bp
from amazon_aigc.model_gateway import generate, instructions
from amazon_aigc.source_pipeline import input_stamp


def brief(text="白底展示4件蓝色硅胶托盘"):
    return {
        "product": "4件蓝色硅胶托盘",
        "selling_points": "实际数量",
        "visual_focus": "产品结构",
        "image_count": 7,
        "plans": [{"purpose": "主图" if i == 0 else "产品细节", "composition": text, "text": ""} for i in range(7)],
    }


def context():
    return {
        "facts": {
            "sales_name": "BrightNest 蓝色托盘",
            "included": "BrightNest托盘4件",
            "material": "硅胶",
            "quantity": "4",
            "components": [{"name": "BrightNest托盘", "specification": "蓝色", "quantity": 4}],
        },
        "source_materials": [
            {
                "kind": "supplier",
                "title": "BrightNest蓝色托盘4件",
                "specifications": ["品牌：BrightNest", "材质: 硅胶"],
                "source_text": "品牌: BrightNest\n材质: 硅胶",
                "manual_text": "参考正面构图",
            },
            {"kind": "competitor", "specifications": ["Brand: OtherMark"], "title": "OtherMark tray"},
        ],
        "editor_requirements": {"requirements": "正面构图，保留BrightNest品牌", "references": ["https://example.test"]},
        "operator_notes": "包装未确认，不展示外盒",
        "references": ["https://example.test"],
    }


def test_generation_projection_is_detached_and_preserves_physical_facts_and_manual_preferences():
    original = context()
    before = copy.deepcopy(original)
    projected, marks = bp.prepare_context(original)
    assert original == before
    assert "editor_requirements" not in projected
    assert "正面构图" in projected["previous_brief_reference"]["requirements"]
    assert "BrightNest" not in json.dumps(projected, ensure_ascii=False)
    assert "OtherMark" not in json.dumps(projected, ensure_ascii=False)
    assert projected["facts"]["quantity"] == "4"
    assert projected["facts"]["material"] == "硅胶"
    assert projected["facts"]["components"][0]["specification"] == "蓝色"
    assert projected["operator_notes"] == "包装未确认，不展示外盒"
    assert projected["references"] == []
    assert {m["kind"] for m in marks} == {"supplier_brand", "competitor_brand"}


def test_generic_attributes_and_ambiguous_colour_are_not_deleted():
    assert not bp.source_marks([{"specifications": ["Brand: Generic", "品牌: 无品牌"]}])
    projected, marks = bp.prepare_context(
        {
            "source_materials": [{"specifications": ["Brand: Blue"]}],
            "facts": {"sales_name": "Blue silicone trays", "included": "4 blue trays"},
        }
    )
    assert projected["facts"]["sales_name"] == "Blue silicone trays"
    assert not bp.review(brief("4 blue trays"), bp.policy(marks))["issues"]


def test_warnings_cover_each_content_field_urls_case_and_platforms_but_not_references():
    value = brief()
    value.update(
        product="ＢｒｉｇｈｔＮｅｓｔ托盘", requirements="画上 Amazon 标识", references=["https://amazon.test"]
    )
    value["plans"][5].update(purpose="保留原品牌", composition="品牌清晰", text="brightnest")
    issues = bp.review(value, bp.policy([{"text": "BrightNest", "kind": "supplier_brand"}]))["issues"]
    assert {i["field"] for i in issues} >= {
        "product",
        "requirements",
        "plans.5.purpose",
        "plans.5.composition",
        "plans.5.text",
    }
    assert not any(i["field"] == "references" for i in issues)
    assert not bp.review(brief("不保留任何来源品牌；白底展示本品"), bp.policy())["issues"]
    assert bp.review(brief("不展示道具，确保品牌清晰"), bp.policy())["issues"]
    assert bp.review_fields([("legacy", "旧图需 https://www.example.test/source")], bp.policy())


@pytest.mark.parametrize("provider", ["openai", "deepseek"])
def test_one_call_advisory_result_is_not_rewritten_or_retried(monkeypatch, provider):
    candidate = brief("保持 VisionMark 品牌清晰")
    response = {**candidate, "source_mark_observations": [{"text": "VisionMark", "kind": "supplier_brand"}]}
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs["json"])
        return httpx.Response(
            200, json={"output": [{"content": [{"type": "output_text", "text": json.dumps(response)}]}]}
        )

    monkeypatch.setattr(httpx, "post", post)
    settings = model_settings().model_copy(update={"openai_api_key": "synthetic", "openai_model": "synthetic"})
    result = generate(settings, provider, "brief", context())
    assert len(calls) == 1
    assert result["status"] == "succeeded"
    assert result["candidate"]["plans"] == candidate["plans"]
    assert result["brief_review"]["issues"]
    assert "source_mark_observations" not in result["candidate"]
    assert "BrightNest" not in json.dumps(calls[0]["input"], ensure_ascii=False)
    assert "不安排包装展示图" in calls[0]["instructions"]


def test_new_rules_apply_to_text_and_vision_without_changing_listing_contract():
    for vision in (False, True):
        text = instructions("brief", vision)
        assert "默认白牌" in text and "不要把来源品牌自动替换成店铺品牌" in text
        assert "previous_brief_reference" in text and "references固定为空数组" in text
    assert "默认白牌" not in instructions("listing", True)


def test_source_notes_and_policy_version_invalidate_pending_inputs(monkeypatch):
    doc = {
        "facts_version": "version",
        "body": {"sources": [{"id": "s", "kind": "supplier", "url": "https://example.test", "manual_text": "正面"}]},
    }
    original = input_stamp(doc)
    doc["body"]["sources"][0]["manual_text"] = "侧面"
    assert input_stamp(doc) != original
    current = input_stamp(doc)
    monkeypatch.setattr(bp, "POLICY_VERSION", "next-version")
    assert input_stamp(doc) != current


def test_dimension_like_brand_names_do_not_erase_confirmed_measurements():
    projected, marks = bp.prepare_context(
        {"source_materials": [{"specifications": ["Brand: 3M"]}], "facts": {"specification": "3m 长，4件"}}
    )
    assert projected["facts"]["specification"] == "3m 长，4件"
    assert not bp.review(brief("电线长3m"), bp.policy(marks))["issues"]
    assert bp.review(brief("3M品牌清晰"), bp.policy(marks))["issues"]
