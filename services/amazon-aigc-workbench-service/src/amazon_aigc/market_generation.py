"""A single model call produces localized copy or one design with a language table."""
from copy import deepcopy

from .schemas import Brief, Listing

LANGUAGES = {"DE": "德语", "FR": "法语", "ES": "西班牙语（西班牙）", "IT": "意大利语", "NL": "荷兰语"}


def batch_schema(task, schema, selected):
    inner = deepcopy(schema)
    definitions = inner.pop("$defs", {})
    if task == "listing":
        properties = {"listings": {"type": "object", "properties": {s: deepcopy(inner) for s in selected}, "required": selected, "additionalProperties": False}}
    else:
        properties = {"shared_brief": inner, "image_texts": {"type": "object", "properties": {s: {"type": "array", "items": {"type": "string", "maxLength": 2000}, "minItems": 7, "maxItems": 7} for s in selected}, "required": selected, "additionalProperties": False}}
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False, **({"$defs": definitions} if definitions else {})}


def rules(task, context):
    selected = context.get("editor_market_targets")
    if not selected:
        return ""
    language = "、".join(s + "=" + LANGUAGES[s] for s in selected)
    common = "\n欧洲多站计划：只输出本批勾选站点" + language + "。所有站点严格共用facts已确认的同一销售商品、规格和实际包含物。德语名词按德语规范大写，法语、西语、意语、荷语使用当地自然大小写，不套用英文Title Case。每种语言独立检查长度与UTF-8字节。"
    if task == "listing":
        return common + "每站在listings对应键返回完整Listing及独立fact_coverage/review_notes。硬限制逐站独立适用：title绝不超过75个Unicode字符（含空格），highlights绝不超过125字符，terms不超过249 UTF-8字节。多站标题优先控制在65至70字符以内以预留语言差异；完整尺寸、包含物放五点和长描述，不把两套长尺寸全部塞入标题。输出前在本次调用内逐字计算每站title/highlights字符数，超长就自行压缩措辞后重新计数；不要输出计数过程。仅销售字段本地化，运营核对提示继续中文。不生成图需。"
    return common + "设计、构图、排版、图片顺序和完整提示词只策划一次。shared_brief全部中文，plans.text为中文基准图中文字，主图text为空；image_texts每站按同一图片顺序返回7条当地语言文案，主图为空，无字副图也为空。不得按语言重新设计、改变件数、构图或排版。shared_brief_reference仅参考已确认事实一致的人工偏好；只修改要求内的设计，保留能复用的设计。禁止生成Listing。"


def validate(task, raw, selected, context, identity, marks):
    from .brief_policy import policy as brief_policy
    from .brief_policy import review as brief_review
    from .listing_quality import assemble_bullets
    from .listing_quality import policy as listing_policy
    from .listing_quality import review as listing_review

    if set(raw) != ({"listings"} if task == "listing" else {"shared_brief", "image_texts"}):
        raise ValueError("多站候选包含未请求的内容")
    key = "listings" if task == "listing" else "image_texts"
    if not isinstance(raw.get(key), dict) or set(raw[key]) != set(selected):
        raise ValueError("多站候选缺少站点或包含未勾选站点")
    if task == "listing":
        candidates, reviews = {}, {}
        for site in selected:
            value = raw[key][site]
            coverage, notes = value.pop("fact_coverage", None), value.pop("review_notes", None)
            assemble_bullets(value)
            candidate = Listing.model_validate(value).model_dump(exclude={"translations"})
            reviews[site] = listing_review(candidate, listing_policy({**context, "site": site}, identity), coverage, require_coverage=True, notes=notes)
            candidates[site] = candidate
        return {"listings": candidates}, {"market_listing_reviews": reviews}
    common = Brief.model_validate(raw.get("shared_brief")).model_dump()
    if common["image_count"] != 7 or len(common["plans"]) != 7 or common["plans"][0]["text"].strip():
        raise ValueError("共用图需须有七张策划，主图无字")
    text_rows = raw[key]
    for site, texts in text_rows.items():
        if not isinstance(texts, list) or len(texts) != 7 or any(not isinstance(t, str) or len(t) > 2000 for t in texts) or texts[0].strip():
            raise ValueError("图中文字须与七张共用策划一一对应，主图无字")
        if any(bool(p["text"].strip()) != bool(t.strip()) for p, t in zip(common["plans"], texts, strict=True)):
            raise ValueError("站点图中文字与共用设计不一致")
    policy = brief_policy(marks)
    policy["checks"].extend(identity["checks"])
    reviews = {s: brief_review({**common, "plans": [{**p, "text": t} for p, t in zip(common["plans"], text_rows[s], strict=True)]}, policy) for s in selected}
    return {"shared_brief": common, "image_texts": text_rows}, {"market_brief_reviews": reviews}
