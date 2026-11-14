"""Source identifiers are reference metadata, never a selling-product identity."""

import copy
import re

from .brief_policy import AMBIGUOUS, GENERIC, mark_pattern, normalized

VERSION = "source-identity-1"
MODEL_KEY = re.compile(
    r"^(?:产品|商品|供应商|厂家)?(?:型号|货号|款号|料号)|"
    r"^(?:item[ _-]?)?model(?:[ _-]?(?:no\.?|number))?$|"
    r"^(?:manufacturer[ _-]?)?part[ _-]?(?:no\.?|number)$|"
    r"^(?:mpn|model_no|model_number|item_no|article_no|supplier_model|supplier_sku)$",
    re.IGNORECASE,
)
CODE = re.compile(
    r"(?<![A-Za-z0-9])(?=[A-Za-z0-9._-]*[A-Za-z])(?=[A-Za-z0-9._-]*\d)[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*(?![A-Za-z0-9])"
)
SPEC = re.compile(
    r"(?:usb(?:[- ]?[ac]|[0-9.]+)?|type[- ]?c|hdmi(?:[0-9.]+)?|led|abs|pet|pvc|pp|tpu|pc|3d|"
    r"\d+(?:\.\d+)?(?:cm|mm|kg|g|ml|l|v|w|a|hz|mah|gb|tb|pcs?|packs?|pk)|"
    r"\d+(?:[-x×]\d+)*(?:cm|mm)|\d+-in-\d+|m\d+(?:x\d+(?:\.\d+)?)?|"
    r"(?:cr|lr|sr|ag)\d{2,4}|a[0-6]|ip\d{2}|aa|aaa|18650|(?:sus)?(?:304|316)l?|"
    r"\d+[- ]?(?:pieces?|packs?|sets?|pairs?|count|ct|levels?|modes?|speeds?|sided|degrees?|colors?|colours?)|bluetooth[0-9.]+)",
    re.IGNORECASE,
)
COMPAT = re.compile(
    r"适配|适用(?:于)?|兼容|匹配|compatible(?:s)?\s+(?:with|con)|fits?\b|\bfor\b|apto(?:s|a|as)?\s+para", re.IGNORECASE
)
UNVERIFIED_COMPAT = re.compile(
    r"待确认|未确认|需核对|待核实|不确定|可能|unverified|not confirmed|unknown|possibly|待验证|\?", re.IGNORECASE
)
TEXT_KEYS = {
    "title",
    "description",
    "source_text",
    "manual_text",
    "sales_name",
    "name",
    "specification",
    "included",
    "excluded",
    "material",
    "dimensions",
    "uses",
    "operator_notes",
    "selling_points",
    "keywords",
    "label",
    "product",
    "instructions",
    "visual_focus",
    "requirements",
    "purpose",
    "composition",
    "text",
}


def is_spec(text):
    return bool(SPEC.fullmatch(normalized(text).strip()))


def compatibility_codes(context):
    facts = context.get("facts") or {}
    texts = [str(facts.get(k) or "") for k in ("specification", "uses", "included", "excluded", "sales_name")]
    texts += [str(x.get("specification") or "") for x in facts.get("components") or []]
    notes = str(context.get("operator_notes") or "")
    if re.search(r"确认|confirmed|verified", notes, re.IGNORECASE):
        texts.append(notes)
    result = {}
    for text in texts:
        for clause in re.split(r"[。；;\n]", normalized(text)):
            if UNVERIFIED_COMPAT.search(clause):
                continue
            for relation in COMPAT.finditer(clause):
                target = re.split(
                    r"[,，]\s*(?:本品|产品|供应商|厂家)?(?:型号|货号|款号)\s*[:：]?", clause[relation.end() :]
                )[0]
                for match in CODE.finditer(target):
                    result[match.group().casefold()] = match.group()
    return list(result.values())


def physical_codes(context):
    facts = context.get("facts") or {}
    names = r"接口|协议|标准|螺纹|灯座|插座|不锈钢|铝合金|socket|thread|connector|protocol|standard|steel|alloy"
    result = {}
    for key in ("material", "specification", "dimensions"):
        text = normalized(facts.get(key, ""))
        for match in CODE.finditer(text):
            if re.search(
                rf"(?:{names})\s*[:：]?\s*$", text[max(0, match.start() - 20) : match.start()], re.IGNORECASE
            ) or re.match(rf"\s*(?:{names})", text[match.end() :], re.IGNORECASE):
                result[match.group().casefold()] = match.group()
    return list(result.values())


def merge_identifiers(*groups, protected=()):
    protected = {normalized(x).casefold() for x in protected}
    result = {}
    for group in groups:
        for row in group or []:
            if not isinstance(row, dict):
                continue
            value = normalized(row.get("text", "")).strip().strip(" ,，;；")
            kind = row.get("kind")
            if kind not in ("source_model", "source_item_number") or not 2 <= len(value) <= 80:
                continue
            if (
                value.casefold() in GENERIC | AMBIGUOUS | protected
                or is_spec(value)
                or re.search(r"https?://|[\n\r]", value)
            ):
                continue
            # Short numbers can be quantities or sizes; never erase them globally.
            if re.fullmatch(r"[\d., /-]+", value) and (not value.isdigit() or len(value) < 6):
                continue
            result.setdefault(value.casefold(), {"text": value, "kind": kind})
    return list(result.values())[:150]


def source_identifiers(materials, protected=()):
    found = []

    def add(value, kind):
        codes = CODE.findall(normalized(value))
        found.extend({"text": token, "kind": kind} for token in codes or [str(value)])

    for material in materials:
        texts = [str(material.get(k) or "") for k in ("title", "source_text", "description", "manual_text")]
        texts += [str(x) for k in ("specifications", "bullets") for x in material.get(k) or []]
        texts += [
            str(x.get("specification") or "")
            for k in ("paired_skus", "selected_variants")
            for x in material.get(k) or []
        ]
        for key, value in material.items():
            if MODEL_KEY.fullmatch(key) and isinstance(value, (str, int)):
                add(
                    value,
                    "source_item_number"
                    if re.search(r"货号|料号|item|part|mpn|sku", key, re.IGNORECASE)
                    else "source_model",
                )
        for text in texts:
            text = re.sub(r"https?://\S+", "", normalized(text))
            for clause in re.split(r"[\n；;]", text):
                key, sep, value = clause.partition(":")
                if sep and MODEL_KEY.fullmatch(key.strip()):
                    add(
                        value.strip(),
                        "source_item_number"
                        if re.search(r"货号|料号|item|part|mpn|sku", key, re.IGNORECASE)
                        else "source_model",
                    )
            found += [{"text": match.group(), "kind": "source_model"} for match in CODE.finditer(text)]
    return merge_identifiers(found, protected=protected)


def policy(marks, protected=()):
    marks = merge_identifiers(marks, protected=protected)

    # Python re.escape also escapes '-' and spaces, which are invalid identity
    # escapes in JavaScript Unicode RegExp. Escape only shared metacharacters.
    def literal(text):
        return re.sub(r"([.*+?^${}()|\[\]\\])", r"\\\1", text)

    return {
        "version": VERSION,
        "marks": marks,
        "protected": list(protected),
        "checks": [
            {
                "code": "source_identifier",
                "pattern": r"(?<![A-Za-z0-9_.-])" + literal(m["text"]) + r"(?![A-Za-z0-9_.-])",
                "message": "含来源型号或货号：" + m["text"],
            }
            for m in marks
        ],
    }


def prepare_context(context):
    result = copy.deepcopy(context)
    compatible = compatibility_codes(result)
    physical = physical_codes(result)
    protected = compatible + physical
    protected_keys = {x.casefold() for x in protected}
    marks = source_identifiers(result.get("source_materials") or [], protected)

    def clean(text):
        parts = []
        for part in re.split(r"[\n；;]", str(text)):
            key, sep, value = normalized(part).partition(":")
            if sep and MODEL_KEY.fullmatch(key.strip()):
                tokens = CODE.findall(value) or [value.strip()]
                kept = [token for token in tokens if is_spec(token) or token.casefold() in protected_keys]
                if not kept:
                    continue
                part = key + ": " + ", ".join(kept)
            for mark in marks:
                part = re.sub(mark_pattern(mark["text"]), "", normalized(part), flags=re.IGNORECASE)
            part = re.sub(r"[ \t]{2,}", " ", part).strip(" -_:：,，")
            if part:
                parts.append(part)
        return "; ".join(parts)

    def walk(value):
        if isinstance(value, dict):
            for key in list(value):
                if key in ("supplier_sku_id", "sku_id") or MODEL_KEY.fullmatch(key):
                    value.pop(key)
                elif key in TEXT_KEYS and isinstance(value[key], str):
                    value[key] = clean(value[key])
                elif key in ("specifications", "bullets") and isinstance(value[key], list):
                    value[key] = [clean(x) for x in value[key] if isinstance(x, str) and clean(x)]
                else:
                    walk(value[key])
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(result)
    result["source_identity_rules"] = {
        "mode": "exclude_source_models_and_item_numbers",
        "confirmed_compatibility_codes": compatible,
        "confirmed_specifications": physical,
    }
    return result, policy(marks, protected)


def add_observation_schema(schema):
    schema["properties"]["source_identifier_observations"] = {
        "type": "array",
        "maxItems": 50,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "text": {"type": "string", "maxLength": 80},
                "kind": {"type": "string", "enum": ["source_model", "source_item_number"]},
            },
            "required": ["text", "kind"],
        },
    }
    schema["required"].append("source_identifier_observations")


def item_policy(context, jobs):
    protected = compatibility_codes(context) + physical_codes(context)
    observed = [j.get("result", {}).get("source_identity", {}).get("marks", []) for j in jobs if j.get("result")]
    return policy(
        merge_identifiers(
            source_identifiers(context.get("source_materials") or [], protected), *observed, protected=protected
        ),
        protected,
    )
