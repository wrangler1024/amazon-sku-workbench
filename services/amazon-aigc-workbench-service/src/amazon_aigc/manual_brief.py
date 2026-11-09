"""Manual confirmation compatibility; model output still uses strict Brief validation."""

from pydantic import Field

from .schemas import Brief, StrictModel


class ManualImagePlan(StrictModel):
    purpose: str = Field(default="", max_length=200)
    composition: str = Field(default="", max_length=4000)
    text: str = Field(default="", max_length=2000)


class ManualBrief(Brief):
    # Suggestions are optional for operators, including partially edited cards.
    # Keep model-generated candidates on the original strict Brief schema.
    plans: list[ManualImagePlan] = Field(default_factory=list, max_length=12)


def normalize_manual_brief(value):
    if not isinstance(value, dict):
        return value
    plans = value.get("plans")
    if not isinstance(plans, list) or not 1 <= len(plans) <= 12:
        return value
    for i, plan in enumerate(plans):
        default_title = "白底主图" if i == 0 else f"副图 {i}"
        if (
            not isinstance(plan, dict)
            or set(plan) - {"purpose", "composition", "text"}
            or not all(isinstance(plan.get(k, ""), str) for k in ("purpose", "composition", "text"))
            or plan.get("purpose", "").strip() not in ("", default_title)
            or plan.get("composition", "").strip()
            or plan.get("text", "").strip()
        ):
            return value
    # All cards are untouched UI placeholders. Preserve the operator's complete
    # original brief and requested image count; never synthesize artwork advice.
    return {**value, "plans": [], "image_count": len(plans) if value.get("image_count") is None else value["image_count"]}


def brief_validation_message(error):
    labels = {
        "product": "产品与销售组合", "instructions": "使用说明", "selling_points": "卖点总结",
        "visual_focus": "产品表现重点", "requirements": "特殊要求与核实项", "references": "参考链接",
        "image_count": "图片数量", "plans": "套图策划建议", "purpose": "主题",
        "composition": "画面要求", "text": "图中文字",
    }
    issues = []
    for e in error.errors(include_url=False, include_input=False):
        loc = e.get("loc", ())
        if len(loc) >= 3 and loc[0] == "plans" and isinstance(loc[1], int):
            name = f"第{loc[1] + 1}张图片的{labels.get(loc[2], '策划内容')}"
        else:
            name = labels.get(loc[0] if loc else "", "图需内容")
        kind, context = e.get("type", ""), e.get("ctx", {})
        if kind in ("missing", "string_too_short"):
            message = f"请补充{name}"
        elif kind == "string_too_long":
            message = f"{name}不能超过{context.get('max_length', '')}字"
        elif kind == "extra_forbidden":
            message = "图需包含不支持的字段，请刷新后重试"
        elif loc and loc[0] == "image_count":
            message = "图片数量须为1至12张"
        elif loc and loc[0] == "plans" and kind == "too_long":
            message = "套图策划建议最多12张"
        else:
            message = f"请检查{name}的填写格式"
        if message not in issues:
            issues.append(message)
    return "；".join(issues) or "请核对图需内容后再提交"
