"""邮件信号判定。业务类型、严重程度和是否需要动作是独立维度。

只把邮件当作线索：邮件中提到的卖家后台操作必须由运营自行核实。
规则刻意保持可解释，判定依据会随事件一起保存。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from .classifier import extract_amount


EVENT_LABELS = {
    "account": "账号安全与资格",
    "performance": "账号健康与绩效",
    "compliance": "侵权与商品合规",
    "listing": "Listing 与可售性",
    "fba": "FBA 货件与库存",
    "finance": "资金与费用",
    "customer": "客户与售后",
    "ads": "广告与平台公告",
    "review": "待核对",
}
ACTIVE_STATUSES = ("待处理", "处理中", "待核对")
CLOSED_STATUSES = ("已完成", "误报")
ALL_STATUSES = ACTIVE_STATUSES + CLOSED_STATUSES

_ASIN = re.compile(r"\b(B0[A-Z0-9]{8})\b", re.I)
_SHIPMENT = re.compile(r"\b(FBA[A-Z0-9]{8,})\b", re.I)
_SKU = re.compile(r"\bSKU\s*[:#：-]?\s*([A-Z0-9][A-Z0-9._-]{3,30})", re.I)
_ORDER = re.compile(r"\b(\d{3}-\d{7}-\d{7})\b")
_DEADLINE = re.compile(
    r"\b(?:within\s+\d{1,3}\s+(?:hours?|days?)|(?:by|before)\s+\d{4}-\d{2}-\d{2})\b"
    r"|(?:请在|限)\s*\d{1,3}\s*(?:小时|天)内|截止(?:日期|时间)?[:：]?\s*\d{4}[-/]\d{1,2}[-/]\d{1,2}",
    re.I,
)


@dataclass(frozen=True)
class Assessment:
    event_type: str
    subtype: str
    severity: str
    action_required: bool
    basis: str
    tags: tuple[str, ...]
    entity_type: Optional[str] = None
    entity_ref: Optional[str] = None
    finance_state: Optional[str] = None
    amount_text: Optional[str] = None
    deadline_text: Optional[str] = None
    source_trust: str = "待核实"

    def target_due_at(self, received_ts: int) -> Optional[str]:
        """内部跟进目标，不冒充亚马逊邮件中的正式期限。"""
        if not self.action_required:
            return None
        minutes = 15 if self.severity == "P0" else 24 * 60
        return (datetime.fromtimestamp(received_ts, tz=timezone.utc).replace(
            microsecond=0) + timedelta(minutes=minutes)).isoformat()


def _entity(text: str) -> tuple[Optional[str], Optional[str]]:
    for name, pattern in (("ASIN", _ASIN), ("货件", _SHIPMENT),
                          ("SKU", _SKU), ("订单", _ORDER)):
        match = pattern.search(text)
        if match:
            return name, match.group(1).upper()
    return None, None


def assess_mail(subject: str, sender: str, body: str = "", *,
                is_amazon: bool = False,
                legacy_category: str = "") -> Optional[Assessment]:
    """返回值得进入店铺信号流的事件；普通邮件和一次性登录码不进入看板。"""
    title = (subject or "").lower()
    sender_l = (sender or "").lower()
    sender_domain = sender_l.rsplit("@", 1)[-1]
    amazon_domain = bool(re.fullmatch(
        r"(?:[a-z0-9-]+\.)*amazon\.[a-z]{2,3}(?:\.[a-z]{2})?",
        sender_domain))
    haystack = f"{title} {(body or '')[:1200].lower()}"
    entity_type, entity_ref = _entity(f"{subject} {(body or '')[:1200]}")
    deadline_match = _DEADLINE.search(f"{subject} {(body or '')[:1200]}")
    deadline_text = deadline_match.group(0) if deadline_match else None

    def has(*words: str) -> bool:
        return any(word in haystack for word in words)

    def event(kind: str, subtype: str, severity: str, action: bool,
              basis: str, *, finance: Optional[str] = None,
              tags: tuple[str, ...] = ()) -> Assessment:
        amount = extract_amount(subject, body) if kind == "finance" else None
        secondary = []
        if entity_type:
            secondary.append(entity_type)
        if kind != "listing" and has("listing suppressed", "listing deactivated", "搜索抑制"):
            secondary.append("Listing受影响")
        if kind != "finance" and has("funds on hold", "disbursement suspended", "资金冻结"):
            secondary.append("资金受影响")
        all_tags = tuple(dict.fromkeys(tags + (subtype,) + tuple(secondary)))
        if not amazon_domain:
            # 不可信发件人不能直接制造 P0；保留为待核对线索。
            return Assessment("review", subtype, "P2", True,
                              f"来源待核实；{basis}", all_tags + (kind,),
                              entity_type, entity_ref, finance, amount,
                              deadline_text, "未验证")
        return Assessment(kind, subtype, severity, action, basis, all_tags,
                          entity_type, entity_ref, finance, amount, deadline_text,
                          "发件域名匹配；仍需后台核实")

    if has("verification code", "verify code", "otp", "验证码", "security code",
           "one-time password") \
            and not has("account deactivated", "account suspended", "risk of deactivation",
                        "bank account changed", "product recall", "账号停用", "资金冻结"):
        return None
    if has("account deactivated", "account suspended", "account at risk",
           "account is at risk of deactivation", "risk of deactivation",
           "selling privileges removed", "账号停用", "账户停用", "账号冻结"):
        return event("account", "账号停用风险", "P0", True, "主题或正文命中账号停用风险")
    if has("bank account changed", "deposit method changed", "password changed",
           "unauthorized login", "收款账户变更", "密码已更改", "未经授权登录"):
        return event("account", "账号或收款资料变更", "P0", True,
                     "主题或正文命中敏感账号变更")
    if has("identity verification", "verify your identity", "tax interview",
           "verify your account", "身份验证", "税务资料验证"):
        return event("account", "账号资料验证", "P1", True, "主题或正文命中账号资料验证")
    if has("product recall", "product safety", "safety violation", "召回", "产品安全"):
        return event("compliance", "产品安全或召回", "P0", True,
                     "主题或正文命中产品安全/召回")
    if has("intellectual property", "trademark complaint", "copyright complaint",
           "counterfeit", "侵权投诉", "商标投诉", "版权投诉", "假货投诉"):
        return event("compliance", "知识产权投诉", "P0", True,
                     "主题或正文命中侵权投诉")
    if has("compliance document", "regulatory compliance", "provide documentation",
           "合规文件", "合规资料", "认证文件"):
        return event("compliance", "商品合规资料", "P1", True,
                     "主题或正文命中商品合规资料要求")
    if has("listing suppressed", "search suppressed", "listing deactivated",
           "listing inactive", "not buyable", "不可购买", "搜索抑制", "商品下架"):
        return event("listing", "Listing 不可售或被抑制", "P1", True,
                     "主题或正文命中 Listing 可售性异常")
    if has("attribute rejected", "image rejected", "listing issue", "图片被拒", "属性被拒"):
        return event("listing", "Listing 内容被拒", "P1", True,
                     "主题或正文命中 Listing 内容问题")
    if has("stranded inventory", "unfulfillable inventory", "不可售库存", "滞留库存"):
        return event("fba", "滞留或不可售库存", "P1", True,
                     "主题或正文命中 FBA 库存异常")
    if has("shipment discrepancy", "units not received", "receiving discrepancy",
           "入仓差异", "接收异常", "货件差异"):
        return event("fba", "入仓差异", "P1", True, "主题或正文命中 FBA 入仓差异")
    if has("out of stock", "low inventory", "replenishment alert", "断货", "库存不足"):
        return event("fba", "库存不足", "P1", True, "主题或正文命中库存不足")
    if has("charge was declined", "unable to charge", "failed charge",
           "outstanding seller account balance", "扣款失败", "信用卡扣款失败"):
        return event("finance", "扣款失败", "P1", True, "主题或正文命中扣款失败",
                     finance="扣款失败")
    if has("funds on hold", "account level reserve", "disbursement suspended",
           "资金冻结", "资金预留", "暂停打款"):
        return event("finance", "资金预留或冻结", "P1", True,
                     "主题或正文命中资金预留/冻结", finance="冻结/预留")
    if has("disbursement failed", "payout failed", "transfer failed", "打款失败", "回款失败"):
        return event("finance", "回款失败", "P1", True,
                     "主题或正文命中回款失败", finance="回款失败")
    if has("disbursement initiated", "disbursement has been initiated",
           "payment is on the way", "payment has been initiated",
           "打款已发起", "转账已发起"):
        return event("finance", "平台发起回款", "P2", False,
                     "主题或正文命中平台发起回款；银行到账未核验", finance="平台已发起")
    if has("a-to-z claim", "chargeback claim", "索赔", "拒付"):
        return event("customer", "索赔或拒付", "P1", True,
                     "主题或正文命中索赔/拒付")
    if has("buyer message", "message from buyer", "return request",
           "买家消息", "退货请求"):
        return event("customer", "买家消息或退货", "P1", True,
                     "主题或正文命中买家消息/退货")
    if has("performance notification", "policy violation", "order defect rate",
           "绩效通知", "政策违规", "指标异常"):
        return event("performance", "绩效或政策异常", "P1", True,
                     "主题或正文命中绩效/政策风险")
    if has("advertising payment failed", "campaign paused", "ads suspended",
           "广告扣款失败", "广告活动暂停"):
        return event("ads", "广告投放中断", "P1", True,
                     "主题或正文命中广告投放/付款异常")
    if has("fee change", "policy update", "费率调整", "政策更新"):
        return event("ads", "平台政策或费率公告", "P2", False,
                     "主题或正文命中政策/费率公告")
    # 兼容旧分类：有明确信号但缺少更细的关键字时进入待核对。
    if legacy_category in ("告警", "侵权", "绩效", "扣款", "回款", "货件", "客户"):
        return event("review", "旧分类待核对", "P2", True,
                     f"旧分类为{legacy_category}，未命中新版细分规则")
    return None
