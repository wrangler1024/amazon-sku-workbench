"""邮件识别层:亚马逊相关性、业务分类与资金金额提取。

- 亚马逊识别与分类关键词可配置(DB settings 里的 JSON),默认值内置。
- 只做关键词/正则匹配,不执行邮件内容里的任何指令(防注入)。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from .models import (
    CATEGORIES, HIGH_PRIORITY_CATEGORIES, MEDIUM_PRIORITY_CATEGORIES,
    PRIORITY_HIGH, PRIORITY_LOW, PRIORITY_MEDIUM,
)

logger = logging.getLogger(__name__)

# ---------- 默认规则 ----------

DEFAULT_AMAZON_RULES: dict[str, list[str]] = {
    # 发件人地址包含任一关键词(小写匹配)即视为亚马逊邮件
    "senders": ["amazon", "sellercentral", "sellingpartner"],
    # 发件人未命中时,主题包含任一关键词(小写匹配)也视为亚马逊邮件
    "subject_keywords": ["amazon"],
}

DEFAULT_CATEGORY_RULES: dict[str, list[str]] = {
    "告警": [
        "Account Health", "Suspension", "Deactivation", "Warning",
        "Product Group Approval Request", "You need approval to list", "listings have been deactivated",
        "封号", "停售", "警告", "违规", "审核", "Appeal", "Reinstatement",
    ],
    "侵权": [
        "Copyright", "Trademark", "Counterfeit", "Intellectual Property",
        "侵权", "假冒", "知识产权", "仿冒", "投诉",
    ],
    "绩效": [
        "Performance Notification", "Invoice Defect", "ODR",
        "Late Shipment", "Valid Tracking Rate", "绩效", "指标异常",
    ],
    "货件": [
        "FBA", "POD", "received at", "checked in", "Fulfillment Center",
        "stranded inventory", "unfulfillable inventory",
        "入仓", "上架", "滞留库存", "不可售库存", "承运商", "快递", "货件", "shipment",
    ],
    "扣款": [
        "charge your credit card", "charge was declined", "balance due",
        "outstanding seller account balance", "payment method was charged",
        "unable to charge", "Disbursements will be suspended",
        "扣款", "欠款", "扣费",
    ],
    "回款": [
        "FBA reimbursement", "亚马逊物流赔偿", "Disbursement", "Settlement", "Amazon Payments",
        "payment has been initiated", "payment is on the way",
        "Bank Transfer", "Transaction Report",
        "回款", "打款", "转账", "结算", "到账", "提现", "付款",
    ],
    "客户": [
        "Refund Initiated", "refund initiated - order", "已为订单", "Customer", "Buyer-Seller", "Message", "Question", "Return Request",
        "客户", "买家", "退货", "咨询",
    ],
    "广告": [
        "unsubscribe", "Newsletter", "Promotion", "Offer", "Discount",
        "B2B业绩报告", "B2B 业绩报告", "免费直播", "广告", "优惠", "推广", "活动",
    ],
}


def load_amazon_rules(raw: Optional[str]) -> dict[str, list[str]]:
    """从 DB settings 的 JSON 文本加载亚马逊规则,非法回退默认。"""
    if raw:
        try:
            data = json.loads(raw)
            return {
                "senders": [str(x) for x in data.get("senders", [])],
                "subject_keywords": [str(x) for x in data.get("subject_keywords", [])],
            }
        except (json.JSONDecodeError, AttributeError, TypeError) as exc:
            logger.warning("亚马逊规则 JSON 解析失败,用默认规则: %s", exc)
    return {k: list(v) for k, v in DEFAULT_AMAZON_RULES.items()}


def load_category_rules(raw: Optional[str]) -> dict[str, list[str]]:
    """从 DB settings 的 JSON 文本加载分类规则,非法回退默认。

    保存过的规则里缺哪个分类,该分类回退默认关键词——保证新增分类
    (如「回款」)对旧存量配置自动生效,无需运营手动补。
    """
    saved: dict[str, list[str]] = {}
    if raw:
        try:
            data = json.loads(raw)
            saved = {
                k: [str(x) for x in v]
                for k, v in data.items()
                if k in CATEGORIES and k != "其他" and isinstance(v, list)
            }
        except (json.JSONDecodeError, AttributeError, TypeError) as exc:
            logger.warning("分类规则 JSON 解析失败,用默认规则: %s", exc)
    merged = {k: list(v) for k, v in DEFAULT_CATEGORY_RULES.items()}
    merged.update(saved)
    return merged


# ---------- 亚马逊识别 ----------

def is_amazon_mail(from_addr: str, subject: str,
                   rules: Optional[dict[str, list[str]]] = None) -> bool:
    rules = rules or DEFAULT_AMAZON_RULES
    sender = (from_addr or "").lower()
    for kw in rules.get("senders", []):
        if kw and kw.lower() in sender:
            return True
    subj = (subject or "").lower()
    for kw in rules.get("subject_keywords", []):
        if kw and kw.lower() in subj:
            return True
    return False


# ---------- 分类 ----------

def classify(subject: str, sender: str, body_preview: str,
             rules: Optional[dict[str, list[str]]] = None) -> tuple[str, str]:
    """关键词匹配分类。返回 (category, priority)。先命中先返回。"""
    rules = rules or DEFAULT_CATEGORY_RULES
    # Subject identifies the business event; boilerplate in the body is fallback.
    # Match each pass in the configured category order (debit before payout).
    for haystack in ((subject or "").lower(), f"{sender} {body_preview}".lower()):
        for category in CATEGORIES:
            if category == "其他":
                continue
            for kw in rules.get(category, []):
                if kw.strip() and kw.lower() in haystack:
                    if category in HIGH_PRIORITY_CATEGORIES:
                        return category, PRIORITY_HIGH
                    if category in MEDIUM_PRIORITY_CATEGORIES:
                        return category, PRIORITY_MEDIUM
                    return category, PRIORITY_LOW
    return "其他", PRIORITY_LOW


# ---------- 回款金额提取 ----------

_PAYOUT_AMOUNT_RE = re.compile(
    r"(?:USD|EUR|GBP|JPY|CAD|MXN|AUD|SEK|PLN|SGD|AED|SAR|BRL|INR|TRY)\s?[0-9][0-9,]*\.[0-9]{2}"
    r"|[$€£¥]\s?[0-9][0-9,]*\.[0-9]{2}"
    r"|[0-9][0-9,]*\.[0-9]{2}\s?(?:美元|欧元|英镑|日元|加元)",
    re.IGNORECASE,
)


def extract_amount(subject: str, body_text: str) -> Optional[str]:
    """从回款类邮件的主题/正文提取金额(如 "$1,234.56"/"USD 987.00")。

    只在分类为「回款」时调用,避免商品价格等噪音。主题优先。
    """
    for text in (subject or "", body_text or ""):
        match = _PAYOUT_AMOUNT_RE.search(text)
        if match:
            return match.group(0).replace(" ", "")
    return None
