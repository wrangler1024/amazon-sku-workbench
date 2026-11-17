"""Advisory checks against confirmed facts; no rewriting or corrective model calls."""

import json
import math
import re

from .brief_policy import normalized, review_fields

VERSION = "listing-quality-2"
FACT_LABELS = {
    "sales_name": "产品名称",
    "quantity": "销售数量",
    "specification": "规格与功能",
    "material": "材质",
    "dimensions": "产品尺寸与重量",
    "included": "实际包含物",
    "excluded": "不包含物与限制",
    "uses": "用途",
    "components": "组成明细",
}
UNITS = {
    "mm": ("length", 1),
    "毫米": ("length", 1),
    "cm": ("length", 10),
    "厘米": ("length", 10),
    "m": ("length", 1000),
    "米": ("length", 1000),
    "in": ("length", 25.4),
    "inch": ("length", 25.4),
    "inches": ("length", 25.4),
    "g": ("weight", 1),
    "克": ("weight", 1),
    "kg": ("weight", 1000),
    "千克": ("weight", 1000),
    "公斤": ("weight", 1000),
    "oz": ("weight", 28.349523125),
    "lb": ("weight", 453.59237),
    "lbs": ("weight", 453.59237),
    "ml": ("volume", 1),
    "毫升": ("volume", 1),
    "l": ("volume", 1000),
    "升": ("volume", 1000),
    "v": ("voltage", 1),
    "伏": ("voltage", 1),
    "w": ("power", 1),
    "瓦": ("power", 1),
    "mah": ("charge", 1),
}
UNIT_PATTERN = "|".join(sorted(UNITS, key=len, reverse=True))
NUMBER = r"\d+(?:[.,]\d+)?"
DIMENSION = re.compile(
    rf"({NUMBER})\s*[x×*]\s*({NUMBER})(?:\s*[x×*]\s*({NUMBER}))?\s*({UNIT_PATTERN})(?![A-Za-z])", re.IGNORECASE
)
MEASUREMENT = re.compile(rf"(?<![A-Za-z0-9])({NUMBER})\s*({UNIT_PATTERN})(?![A-Za-z])", re.IGNORECASE)
SOLD_SEPARATELY = r"\b(?:sold|purchased|available)\s+separately\b|\bse\s+vende(?:n)?\s+por\s+separado\b|\bventa\s+por\s+separado\b|另售|单独销售|单独出售"
# Shared with browser RegExp; deliberately allow normal buyer instructions such
# as "check your device before purchase" and confirmed compatibility limits.
INTERNAL_REVIEW = (
    r"\b(?:requires?|needs?|awaits?)\s+(?:(?:further|manual|operator)\s+)?(?:confirmation|verification|clarification)\b|"
    r"\b(?:pending|awaiting)\s+(?:confirmation|verification|clarification)\b|"
    r"\b(?:to\s+be|not(?:\s+yet)?)\s+(?:confirmed|verified|determined)\b|\b(?:unconfirmed|unverified|TBD|TBC)\b|"
    r"\b(?:confirm|verify|clarify)\s+(?:whether|which)\s+(?:these|they|the\s+(?:supplied|provided|listed)\s+(?:dimensions|measurements))\b[^.;!?\n]{0,100}\b(?:refer|product|packag(?:e|ing)|measure)\b|"
    r"\b(?:confirm|verify|clarify)\s+(?:with\s+(?:the\s+)?(?:supplier|manufacturer)|(?:the\s+)?(?:actual|product|packaging)\s+(?:dimensions|measurements|size))\b|"
    r"\b(?:dimensions?|measurements?|size|material|compatibility)\s*:\s*(?:unknown|not\s+(?:specified|provided|confirmed)|N/?A)\b|"
    r"待(?:确认|核实|核对|验证|测量)|尚未(?:确认|核实)|(?:请|需|需要)(?:运营|供应商|人工)(?:进一步)?(?:确认|核实|核对)|"
    r"\b(?:pendientes?\s+de\s+(?:confirmación|verificación)|por\s+confirmar|sin\s+confirmar|requieren?\s+(?:confirmación|verificación))\b"
)
UNCERTAIN_FACT = re.compile(
    INTERNAL_REVIEW + r"|未确认|未核实|不确定|不清楚|不详|待测|尺寸未知|需确认|需核实", re.IGNORECASE
)


def fact_items(context):
    facts = context.get("facts") or {}
    items = [
        {
            "id": key,
            "label": label,
            "text": json.dumps(facts[key], ensure_ascii=False)
            if isinstance(facts[key], (list, dict))
            else str(facts[key]),
        }
        for key, label in FACT_LABELS.items()
        if facts.get(key) not in (None, "", [])
    ]
    for item in items:
        if UNCERTAIN_FACT.search(item["text"]):
            item["uncertain"] = True
            parts = re.split(r"[;；。\n]", item["text"])
            item["confirmed_text"] = "; ".join(s.strip() for s in parts if s.strip() and not UNCERTAIN_FACT.search(s))
            item["unconfirmed_text"] = "; ".join(s.strip() for s in parts if UNCERTAIN_FACT.search(s))
    return items


def measurement(value, unit, text):
    family, scale = UNITS[unit.lower()]
    number = value.replace(",", ".")
    precision = len(number.split(".")[1]) if "." in number else 0
    return {
        "family": family,
        "value": float(number) * scale,
        "text": text,
        "scale": scale,
        "resolution": scale * 10**-precision,
    }


def measurements(text):
    text = normalized(text)
    result = []
    spans = []
    for match in DIMENSION.finditer(text):
        unit = match[4].lower()
        for value in match.group(1, 2, 3):
            if value:
                result.append(measurement(value, unit, match.group()))
        spans.append(match.span())
    for match in MEASUREMENT.finditer(text):
        if any(start <= match.start() < end for start, end in spans):
            continue
        result.append(measurement(match[1], match[2], match.group()))
    return result


def equivalent(a, b):
    tolerance = a["resolution"] / 2 + 1e-6 if a["scale"] != b["scale"] else 1e-6
    return (
        a["family"] == b["family"]
        and not (a["value"] == 0 < b["value"])
        and math.isclose(a["value"], b["value"], rel_tol=0, abs_tol=tolerance)
    )


def policy(context, identity=None, confirmed=True):
    items = fact_items(context) if confirmed else []
    text = "\n".join(x["text"] for x in items)
    checks = list((identity or {}).get("checks", []))
    checks.append(
        {
            "code": "internal_review",
            "pattern": INTERNAL_REVIEW,
            "message": "含内部核对说明，请移出买家文案；未确认信息核实前不要用于刊登",
        }
    )
    if not re.search(SOLD_SEPARATELY, text, re.IGNORECASE):
        checks.append(
            {
                "code": "unsupported_sale",
                "pattern": SOLD_SEPARATELY,
                "message": "本品事实未确认另售，请将“不包含”与“另售”分开核对",
            }
        )
    return {
        "version": VERSION,
        "confirmed": confirmed,
        "facts": items,
        "measurements": [
            {**m, "fact": x["id"]} for x in items for m in measurements(x.get("confirmed_text", x["text"]))
        ],
        "unconfirmed_measurements": [
            {**m, "fact": x["id"]} for x in items if x.get("uncertain") for m in measurements(x["unconfirmed_text"])
        ],
        "checks": checks,
    }


def fields(candidate):
    return [(k, candidate.get(k, "")) for k in ("title", "highlights", "description", "terms")] + [
        (f"bullets.{i}", x) for i, x in enumerate(candidate.get("bullets") or [])
    ]


def review(candidate, rules, coverage=None, require_coverage=False, notes=None):
    values = fields(candidate)
    issues = review_fields(values, rules)
    for i, bullet in enumerate(candidate.get("bullets") or []):
        if not re.match(r"^[^:：\n]{1,160}[:：]\s*\S", str(bullet)):
            issues.append(
                {"field": f"bullets.{i}", "code": "bullet_format", "message": "请按“小标题: 说明”核对本条格式"}
            )
        if re.search(r"[.!?。！？…]$", str(bullet).strip()):
            issues.append(
                {"field": f"bullets.{i}", "code": "bullet_ending", "message": "请按SOP去除五点末尾的句尾标点"}
            )
    actual = []
    if rules.get("confirmed"):
        for field, text in values:
            if field == "terms":
                continue
            for value in measurements(text):
                actual.append(value)
                if not any(equivalent(value, known) for known in rules["measurements"]):
                    uncertain = any(equivalent(value, known) for known in rules.get("unconfirmed_measurements", []))
                    issues.append(
                        {
                            "field": field,
                            "code": "unconfirmed_measurement" if uncertain else "unsupported_measurement",
                            "message": (
                                "仍含待确认数值，核实前请勿用于刊登：" if uncertain else "本品事实未确认此数值或单位："
                            )
                            + value["text"],
                        }
                    )
        for item in rules["facts"]:
            if item.get("uncertain"):
                issues.append(
                    {
                        "field": "facts." + item["id"],
                        "code": "fact_needs_confirmation",
                        "message": item["label"] + "含待确认信息，核实后再补入文案",
                    }
                )
            expected = [m for m in rules["measurements"] if m["fact"] == item["id"]]
            if any(not any(equivalent(found, value) for found in actual) for value in expected):
                issues.append(
                    {
                        "field": "facts." + item["id"],
                        "code": "missing_detail",
                        "message": item["label"] + "中的已确认数值尚未完整覆盖，请核对补充",
                    }
                )
    if require_coverage:
        reports = {r.get("fact_id"): r for r in coverage or [] if isinstance(r, dict)}
        texts = dict(values)
        for item in rules.get("facts", []):
            if item.get("uncertain"):
                continue
            report = reports.get(item["id"], {})
            quote = str(report.get("quote") or "").strip()
            content = str(texts.get(report.get("field"), ""))
            if report.get("status") != "covered" or not quote or quote not in content:
                issues.append(
                    {
                        "field": "facts." + item["id"],
                        "code": "coverage_unverified",
                        "message": item["label"] + "未返回可核对的正文引用，请人工检查遗漏或冲突",
                    }
                )
    safe_notes = [
        {"fact_id": n["fact_id"], "reason": n["reason"].strip()[:600]}
        for n in (notes if isinstance(notes, list) else [])[:12]
        if isinstance(n, dict)
        and n.get("fact_id") in FACT_LABELS
        and isinstance(n.get("reason"), str)
        and n["reason"].strip()
    ]
    issues.extend(
        {"field": "facts." + n["fact_id"], "code": "operator_review", "message": n["reason"]} for n in safe_notes
    )
    unique = {(x["field"], x["code"], x["message"]): x for x in issues}
    return {"policy": rules, "issues": list(unique.values()), "coverage": coverage or [], "notes": safe_notes}


def add_schema(schema):
    schema["properties"]["bullets"]["items"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "heading": {"type": "string", "minLength": 1, "maxLength": 160, "pattern": r"^[^:：\n]+$"},
            "body": {"type": "string", "minLength": 1, "maxLength": 1838, "pattern": r"\S(?:[\s\S]*[^\s.!?。！？…])?$"},
        },
        "required": ["heading", "body"],
    }
    schema["properties"]["fact_coverage"] = {
        "type": "array",
        "maxItems": len(FACT_LABELS),
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "fact_id": {"type": "string", "enum": list(FACT_LABELS)},
                "status": {"type": "string", "enum": ["covered", "missing", "conflict"]},
                "field": {
                    "type": "string",
                    "enum": ["title", "highlights", "description", *[f"bullets.{i}" for i in range(5)]],
                },
                "quote": {"type": "string", "maxLength": 400},
            },
            "required": ["fact_id", "status", "field", "quote"],
        },
    }
    schema["required"].append("fact_coverage")
    schema["properties"]["review_notes"] = {
        "type": "array",
        "maxItems": 12,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "fact_id": {"type": "string", "enum": list(FACT_LABELS)},
                "reason": {"type": "string", "minLength": 1, "maxLength": 600},
            },
            "required": ["fact_id", "reason"],
        },
    }
    schema["required"].append("review_notes")


def assemble_bullets(raw):
    # New model output has separate headings. Saved/manual strings remain compatible.
    bullets = raw.get("bullets") or []
    if bullets and all(isinstance(x, dict) for x in bullets):
        raw["bullets"] = [
            str(x.get("heading", "")).strip().rstrip(":：") + ": " + str(x.get("body", "")).strip() for x in bullets
        ]
