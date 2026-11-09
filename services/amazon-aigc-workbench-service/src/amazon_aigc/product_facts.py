"""Derive the sales structure from its components without inventing product claims."""

from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from .schemas import Component


def summarize_contents(rows):
    """Summarize only explicit component selections, matching the editor's summary."""
    if not rows:
        return ""
    try:
        for row in rows:
            Component.model_validate(row)
            if isinstance(row.get("quantity"), bool):
                return ""
    except (ValidationError, AttributeError):
        return ""

    def number(value):
        text = format(Decimal(str(value)), "f")
        return text.rstrip("0").rstrip(".") if "." in text else text

    def quantities(items):
        totals = {}
        for row in items:
            unit = row["unit"].strip()
            totals[unit] = totals.get(unit, Decimal(0)) + Decimal(str(row["quantity"]))
        return " + ".join(number(n) + unit for unit, n in totals.items())

    groups = {}
    for row in rows:
        groups.setdefault(row["name"].strip(), []).append(row)
    detailed = "\n".join(
        name + "：" + "；".join(f"{r['specification']} × {number(r['quantity'])}{r['unit']}" for r in items)
        for name, items in groups.items()
    )
    if len(detailed) <= 4000:
        return detailed
    compact = "\n".join(
        f"{name} {quantities(items)}（{len(items)}种规格，详见组成明细）" for name, items in groups.items()
    )
    if len(compact) <= 4000:
        return compact
    fallback = f"共{len(rows)}项组成，{quantities(rows)}；按组成明细中各项规格及用量配套"
    return fallback if len(fallback) <= 4000 else f"共{len(rows)}项组成；按组成明细中各项规格及用量配套"


def paired_contents(current, before, after):
    """Prefill a draft, or refresh a recognizable summary; preserve custom contents."""
    if not isinstance(current, str):
        return current
    next_summary = summarize_contents(after)
    if not current.strip():
        return next_summary or current
    previous = summarize_contents(before)
    if (
        next_summary
        and previous
        and (current == previous or current.startswith((previous + "\n", previous + "；")))
    ):
        updated = next_summary + current[len(previous) :]
        return updated if len(updated) <= 4000 else current
    return current


def facts_validation_message(error):
    """Actionable field labels, with no Pydantic internals or submitted values."""
    labels = {
        "sales_name": "销售商品名称",
        "quantity": "数量",
        "included": "实际包含物",
        "specification": "规格",
        "unit": "单位",
        "name": "组成名称",
        "url": "货源链接",
        "material": "材质",
        "dimensions": "产品尺寸",
        "packaging_dimensions": "包装尺寸",
        "uses": "主要用途",
        "excluded": "不包含物",
        "source_text": "补充资料",
        "components": "规格与组成明细",
        "product_type": "商品类型",
        "confirmed": "核对状态",
        "image_asset_ids": "组成产品图片",
    }
    messages = []
    for issue in error.errors(include_url=False, include_input=False):
        location = issue["loc"]
        kind = issue["type"]
        field = location[-1] if location else ""
        label = labels.get(field, "产品资料")
        if len(location) > 1 and location[0] == "components" and isinstance(location[1], int):
            label = f"第{location[1] + 1}项组成的{label}"
        ctx = issue.get("ctx", {})
        if field == "included" and kind in ("missing", "string_too_short"):
            message = "请填写“实际包含物”，可在“销售商品资料”点击“按明细一键汇总”后核对"
        elif kind in ("missing", "string_too_short"):
            message = f"请填写“{label}”"
        elif kind == "string_too_long":
            message = f"“{label}”不能超过{ctx['max_length']}字"
        elif kind == "too_long":
            message = f"“{label}”不能超过{ctx['max_length']}项"
        elif kind in ("greater_than", "less_than_equal", "float_parsing", "float_type", "finite_number"):
            message = f"“{label}”须为大于0且不超过100000的有效数字"
        elif not location and kind == "value_error":
            reason = str(ctx.get("error", ""))
            message = (
                reason if reason in ("组合商品须逐项确认组成、规格与数量", "组成项ID不能重复") else "请检查产品资料格式"
            )
        else:
            message = f"请检查“{label}”的填写格式"
        if message not in messages:
            messages.append(message)
    return "资料尚未确认：" + "；".join(messages[:5]) + "。资料未填完整时可先保存草稿。"


def normalize_components(facts):
    facts = dict(facts)
    rows = facts.get("components") or []
    if not rows or any(not isinstance(r, dict) for r in rows):
        return facts
    facts["product_type"] = "bundle" if len(rows) > 1 else "ordinary"
    units = {str(r.get("unit") or "").strip() for r in rows}
    quantities = []
    for row in rows:
        try:
            quantity = Decimal(str(row.get("quantity", "")))
        except InvalidOperation:
            quantity = Decimal("NaN")
        quantities.append(quantity)
    valid = all(q.is_finite() and q > 0 for q in quantities)
    if valid and len(units) == 1 and "" not in units:
        facts["quantity"] = (
            format(sum(quantities), "f").rstrip("0").rstrip(".")
            if "." in format(sum(quantities), "f")
            else str(sum(quantities))
        )
        facts["unit"] = next(iter(units))
    elif valid and "" not in units:
        facts["quantity"] = " + ".join(str(r["quantity"]) + str(r["unit"]) for r in rows)
        facts["unit"] = ""
    else:
        facts["quantity"] = ""
        facts["unit"] = next(iter(units)) if len(units) == 1 else ""
    specification = (
        str(rows[0].get("specification") or "")
        if len(rows) == 1
        else "；".join(
            f"{r.get('specification') or '待填写规格'} × {r.get('quantity', '')}{r.get('unit', '')}" for r in rows
        )
    )
    facts["specification"] = specification if len(specification) <= 2000 else f"共{len(rows)}项组成，详见组成明细"
    return facts


def source_fact_hints(body):
    """Only explicit product attributes; packaging measurements are not product size."""
    import re

    attributes = {}
    for text in body.get("specifications") or []:
        key, sep, value = str(text).partition(":")
        if sep:
            attributes[key.strip()] = value.strip()
    material = next((attributes[k] for k in ("面料成分含量", "材质", "面料成分", "面料名称") if attributes.get(k)), "")

    def attribute(names):
        for name in names:
            for key, value in attributes.items():
                base = re.sub(r"\([^)]*\)|（[^）]*）", "", key).strip()
                if base != name or not value:
                    continue
                unit_match = re.search(r"[（(](kg|g|cm|mm|m|in|克|千克|厘米|毫米|米|英寸)[）)]", key, re.IGNORECASE)
                if re.fullmatch(r"[\d.,×xX*+\-~\s]+", value):
                    return value + (" " + unit_match[1] if unit_match else "（单位未注明）")
                return value
        return ""

    dimensions = attribute(("产品尺寸", "商品尺寸"))
    weight = attribute(("产品重量", "商品重量", "净重"))
    unspecified_weight = attribute(("重量",))
    unspecified_dimensions = attribute(("尺寸",))
    warnings = []
    if "纯棉" in body.get("title", "") and material:
        ratios = re.findall(r"(\d+(?:\.\d+)?)\s*%\s*棉", material)
        if any(float(n) < 100 for n in ratios) or re.search(r"涤纶|聚酯|锦纶|氨纶|粘纤", material):
            warnings.append(
                f"材质存在冲突：货源标题含“纯棉”，属性为“{material}”；请核对实物或供应商，不可直接写成100%纯棉"
            )
    return {
        "material": material,
        "dimensions": dimensions,
        "weight": weight,
        "unspecified_weight": unspecified_weight,
        "unspecified_dimensions": unspecified_dimensions,
        "warnings": warnings,
    }
