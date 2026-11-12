"""White-label generation projection and advisory review; never rewrite saved content."""

import copy
import re
import unicodedata

POLICY_VERSION = "white-label-1"
FIELDS = ("product", "instructions", "selling_points", "visual_focus", "requirements")
IDENTITY_KEY = re.compile(
    r"^(品牌(?:名称|名)?|商标|制造商|生产厂家|店铺(?:名称|名)?|商家|brand(?: name)?|manufacturer|seller|store|shop)$",
    re.IGNORECASE,
)
GENERIC = {
    "generic",
    "unbranded",
    "no brand",
    "oem",
    "odm",
    "other",
    "n/a",
    "无",
    "无品牌",
    "白牌",
    "其他",
    "其它",
    "不适用",
}
# Ambiguous words remain intact in factual input. Their use as a brand is still reviewed.
AMBIGUOUS = {
    "blue",
    "red",
    "black",
    "white",
    "green",
    "orange",
    "pink",
    "yellow",
    "home",
    "apple",
    "cotton",
    "silicone",
    "wood",
    "pet",
    "蓝色",
    "白色",
    "黑色",
    "红色",
    "棉",
    "硅胶",
}
PLATFORMS = r"(?<![A-Za-z0-9])(?:amazon|aliexpress|alibaba|temu|shein|ebay|taobao|tmall|1688)(?![A-Za-z0-9])|亚马逊|速卖通|阿里巴巴|淘宝|天猫"
LINKS = r"https?://[^\s]+|www\.[^\s]+|(?:amazon\.(?:com|ca|co\.uk|com\.au)|1688\.com|alicdn\.com)"
RETENTION = r"(?:保留|保持|确保|突出|固定|复刻|照搬)[^。；;\n]{0,24}(?:品牌|商标|logo|Logo|LOGO)|(?:品牌|商标|logo)[^。；;\n]{0,16}(?:清晰|醒目|不变)|(?:keep|retain|preserve)[^.;\n]{0,30}(?:brand|logo)"
NEGATION = re.compile(r"(?:不|勿|禁止|去除|移除|忽略|无需|不得|不要|without|remove|ignore|do not)", re.IGNORECASE)


def normalized(text):
    return unicodedata.normalize("NFKC", str(text or ""))


def mark_pattern(text):
    # Compatible with both Python and browser RegExp. Avoid partial English words.
    text = normalized(text)
    escaped = re.sub(r"([.*+?^${}()|\[\]\\])", r"\\\1", text)
    return (
        (r"(?<![A-Za-z0-9])" if text[:1].isascii() and text[:1].isalnum() else "")
        + escaped
        + (r"(?![A-Za-z0-9])" if text[-1:].isascii() and text[-1:].isalnum() else "")
    )


def merge_marks(*groups):
    marks = {}
    for group in groups:
        for row in group or []:
            if not isinstance(row, dict):
                continue
            text = normalized(row.get("text", "")).strip().strip("®™")
            if not 2 <= len(text) <= 100 or text.casefold() in GENERIC or "http" in text.casefold():
                continue
            if row.get("kind") not in ("supplier_brand", "competitor_brand", "shop", "platform"):
                continue
            marks.setdefault(text.casefold(), {"text": text, "kind": row["kind"]})
    return list(marks.values())[:100]


def source_marks(materials):
    result = []
    for material in materials:
        kind = "competitor_brand" if material.get("kind") == "competitor" else "supplier_brand"
        rows = [*(material.get("specifications") or []), *str(material.get("source_text") or "").splitlines()]
        for key in ("brand", "brand_name", "shop_name", "seller_name"):
            if material.get(key):
                result.append({"text": material[key], "kind": "shop" if key in ("shop_name", "seller_name") else kind})
        for row in rows:
            key, sep, value = normalized(row).partition(":")
            if sep and IDENTITY_KEY.fullmatch(key.strip()):
                result.append(
                    {
                        "text": value.strip(),
                        "kind": "shop" if re.search("店铺|商家|seller|store|shop", key, re.IGNORECASE) else kind,
                    }
                )
    return merge_marks(result)


def ambiguous_mark(text):
    return text.casefold() in AMBIGUOUS or bool(
        re.fullmatch(r"\d+(?:\.\d+)?\s*(?:m|cm|mm|g|kg|ml|l)", text, re.IGNORECASE)
    )


def policy(marks=()):
    marks = merge_marks(marks)
    return {
        "version": POLICY_VERSION,
        "marks": marks,
        "checks": [
            {"code": "platform", "pattern": PLATFORMS, "message": "含来源平台标识，请核对移除"},
            {"code": "link", "pattern": LINKS, "message": "含来源链接，请移到参考资料区"},
            {
                "code": "retain_mark",
                "pattern": RETENTION,
                "message": "疑似要求保留来源品牌或标识，请核对",
                "skip_negative": True,
            },
            *[
                {
                    "code": "source_mark",
                    "pattern": mark_pattern(m["text"]),
                    "message": "含疑似来源标识：" + m["text"],
                    "brand_context_only": ambiguous_mark(m["text"]),
                }
                for m in marks
            ],
        ],
    }


def review_fields(fields, rules):
    issues = []
    for field, value in fields:
        text = normalized(value)
        for check in rules["checks"]:
            for match in re.finditer(check["pattern"], text, re.IGNORECASE):
                clause = re.split(r"[。；;\n,，]", text[: match.start()])[-1] + match.group()
                full_clause = clause + re.split(r"[。；;\n,，]", text[match.end() :])[0]
                if check.get("skip_negative") and NEGATION.search(clause):
                    continue
                if check.get("brand_context_only") and not re.search(
                    r"品牌|商标|logo|brand", full_clause, re.IGNORECASE
                ):
                    continue
                issues.append({"field": field, "code": check["code"], "message": check["message"]})
                break
    return issues


def brief_fields(brief):
    fields = [(key, brief.get(key, "")) for key in FIELDS]
    for i, plan in enumerate(brief.get("plans") or []):
        fields.extend((f"plans.{i}.{key}", plan.get(key, "")) for key in ("purpose", "composition", "text"))
    return fields


def review(brief, rules):
    return {"policy": rules, "issues": review_fields(brief_fields(brief), rules)}


def clean_reference(text, marks):
    lines = []
    for line in str(text or "").splitlines():
        key, sep, _ = normalized(line).partition(":")
        if sep and IDENTITY_KEY.fullmatch(key.strip()):
            continue
        for mark in marks:
            if not ambiguous_mark(mark["text"]) and re.search(
                mark_pattern(mark["text"]), normalized(line), re.IGNORECASE
            ):
                line = re.sub(mark_pattern(mark["text"]), "", normalized(line), flags=re.IGNORECASE)
        for pattern in (LINKS, PLATFORMS):
            if re.search(pattern, normalized(line), re.IGNORECASE):
                line = re.sub(pattern, "", normalized(line), flags=re.IGNORECASE)
        if line.strip():
            lines.append(line.strip())
    return "\n".join(lines)


def prepare_context(context):
    """Projection only: raw snapshots, confirmed facts and manual drafts stay unchanged."""
    result = copy.deepcopy(context)
    materials = result.get("source_materials") or []
    marks = source_marks(materials)
    result["branding_policy"] = {
        "mode": "white_label",
        "version": POLICY_VERSION,
        "packaging_basis": "仅依据实际包含物及运营补充说明；包装不明确时不策划包装展示图",
    }
    # A previous AI draft is reference content, never an instruction with operator authority.
    previous = result.pop("editor_requirements", {})
    result["previous_brief_reference"] = previous
    for key in FIELDS:
        if isinstance(previous.get(key), str):
            clean = clean_reference(previous[key], marks)
            previous[key] = "；".join(
                part
                for part in re.split(r"[。；;\n,，]", clean)
                if not review_fields([("draft", part)], {"checks": [policy()["checks"][2]]})
            )
    previous.pop("references", None)
    result["references"] = []
    for material in materials:
        for key in ("title", "description", "source_text", "manual_text"):
            if isinstance(material.get(key), str):
                material[key] = clean_reference(material[key], marks)
        for key in ("specifications", "bullets"):
            material[key] = [clean for value in material.get(key, []) if (clean := clean_reference(value, marks))]
        material["identity_use"] = "来源标识不得用作本品品牌；规格图中的包装印刷也不代表实际销售版本"
        for key in ("paired_skus", "selected_variants"):
            for row in material.get(key, []):
                row["specification"] = clean_reference(row.get("specification", ""), marks)
    facts = result.get("facts") or {}
    for key in ("sales_name", "specification", "included"):
        if isinstance(facts.get(key), str):
            facts[key] = clean_reference(facts[key], marks)
    for row in facts.get("components") or []:
        for key in ("name", "specification"):
            row[key] = clean_reference(row.get(key, ""), marks)
    for image in result.get("vision_manifest") or []:
        image["label"] = clean_reference(image.get("label", ""), marks)
        for binding in image.get("bindings") or []:
            if isinstance(binding.get("specification"), str):
                binding["specification"] = clean_reference(binding["specification"], marks)
    for key in ("operator_notes", "selling_points", "keywords"):
        if isinstance(result.get(key), str):
            result[key] = clean_reference(result[key], marks)
    return result, marks


def add_observation_schema(schema):
    schema["properties"]["source_mark_observations"] = {
        "type": "array",
        "maxItems": 50,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "text": {"type": "string", "maxLength": 100},
                "kind": {"type": "string", "enum": ["supplier_brand", "competitor_brand", "shop", "platform"]},
            },
            "required": ["text", "kind"],
        },
    }
    schema["required"].append("source_mark_observations")


def item_policy(c, item_id, snapshots):
    materials = [{**s["body"], "kind": "supplier"} for s in snapshots]
    # Retain image-only observations for older candidates and legacy/manual copy checks.
    rows = c.execute(
        "SELECT result->'brief_review'->'policy'->'marks' AS marks FROM jobs "
        "WHERE work_item_id=%s AND kind='model' AND result->'brief_review' IS NOT NULL "
        "ORDER BY created_at DESC LIMIT 100",
        (item_id,),
    ).fetchall()
    return policy(merge_marks(source_marks(materials), *(row["marks"] or [] for row in rows)))
