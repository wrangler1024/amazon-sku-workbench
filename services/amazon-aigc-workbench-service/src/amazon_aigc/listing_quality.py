"""Advisory checks against confirmed facts; no rewriting or corrective model calls."""

import json
import math
import re

from .brief_policy import normalized, review_fields

VERSION = "listing-quality-1"
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


def fact_items(context):
    facts = context.get("facts") or {}
    return [
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
        "measurements": [{**m, "fact": x["id"]} for x in items for m in measurements(x["text"])],
        "checks": checks,
    }


def fields(candidate):
    return [(k, candidate.get(k, "")) for k in ("title", "highlights", "description", "terms")] + [
        (f"bullets.{i}", x) for i, x in enumerate(candidate.get("bullets") or [])
    ]


def review(candidate, rules, coverage=None, require_coverage=False):
    values = fields(candidate)
    issues = review_fields(values, rules)
    for i, bullet in enumerate(candidate.get("bullets") or []):
        if not re.match(r"^[^:：\n]{1,160}[:：]\s*\S", str(bullet)):
            issues.append(
                {"field": f"bullets.{i}", "code": "bullet_format", "message": "请按“小标题: 说明”核对本条格式"}
            )
    actual = []
    if rules.get("confirmed"):
        for field, text in values:
            if field == "terms":
                continue
            for value in measurements(text):
                actual.append(value)
                if not any(equivalent(value, known) for known in rules["measurements"]):
                    issues.append(
                        {
                            "field": field,
                            "code": "unsupported_measurement",
                            "message": "本品事实未确认此数值或单位：" + value["text"],
                        }
                    )
        for item in rules["facts"]:
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
    unique = {(x["field"], x["code"], x["message"]): x for x in issues}
    return {"policy": rules, "issues": list(unique.values()), "coverage": coverage or []}


def add_schema(schema):
    schema["properties"]["bullets"]["items"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "heading": {"type": "string", "minLength": 1, "maxLength": 160},
            "body": {"type": "string", "minLength": 1, "maxLength": 1838},
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


def assemble_bullets(raw):
    # New model output has separate headings. Saved/manual strings remain compatible.
    bullets = raw.get("bullets") or []
    if bullets and all(isinstance(x, dict) for x in bullets):
        raw["bullets"] = [
            str(x.get("heading", "")).strip().rstrip(":：") + ": " + str(x.get("body", "")).strip() for x in bullets
        ]
