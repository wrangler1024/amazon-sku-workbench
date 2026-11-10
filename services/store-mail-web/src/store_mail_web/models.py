"""领域数据模型(纯 dataclass,无副作用)。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# ---------- 分类常量(七分类 + 回款/扣款) ----------

CATEGORIES = ("告警", "侵权", "绩效", "扣款", "回款", "货件", "客户", "广告", "其他")
PRIORITY_HIGH = "高"
PRIORITY_MEDIUM = "中"
PRIORITY_LOW = "低"
HIGH_PRIORITY_CATEGORIES = ("告警", "侵权")
MEDIUM_PRIORITY_CATEGORIES = ("绩效", "扣款", "回款", "货件", "客户")

# 「重要通知」视图与店铺看板关注的分类
IMPORTANT_CATEGORIES = ("告警", "侵权", "绩效", "扣款", "回款")

# P0/P1 事件已由风险判定把关；默认不再因旧收件箱分类漏掉 Listing/FBA。
DEFAULT_PUSH_CATEGORIES = CATEGORIES

# 邮件标记类型(按用户独立;扩展时在此加,前端同步)
MESSAGE_FLAGS = ("star", "flag", "pin")   # 收藏 / 小红旗 / 信标


@dataclass(frozen=True)
class Account:
    """一个店铺邮箱账号。明文授权码不落在实例里,只在同步时临时解密。"""

    id: int
    display_name: str
    email: str
    imap_host: str
    imap_port: int
    enabled: bool
    last_uid: int
    last_sync_at: Optional[str]
    last_sync_status: Optional[str]      # ok / error / auth_error
    last_error: Optional[str]
    created_at: str
    updated_at: str
    site: str = ""
    owner_user_id: Optional[int] = None
    last_success_at: Optional[str] = None
    chinese_name: str = ""


@dataclass(frozen=True)
class FetchedMail:
    """IMAP 拉回的一封原始邮件(解析后、入库前)。"""

    uid: int
    message_id: str
    subject: str
    from_name: str
    from_addr: str
    to_addr: str
    internal_date: Optional[str]         # ISO 格式,可能缺
    received_ts: int                     # 排序/筛选用的 epoch 秒
    size_bytes: int
    unread: bool                         # IMAP FLAGS 无 \\Seen 时为未读
    body_text: str                       # 清洗后的纯文本正文
    body_html: Optional[str]             # 原始 HTML(可选,有大小上限)
    preview: str                         # 正文前 200 字


@dataclass(frozen=True)
class ClassifiedMail:
    """邮件业务分类与金额识别结果。"""

    is_amazon: bool
    category: str
    priority: str
    amount: Optional[str] = None          # 回款类邮件提取的金额,如 "$1,234.56"


@dataclass(frozen=True)
class User:
    """成员及业务角色；env 管理员为受保护的超管，普通管理员仍是数据库成员。"""

    id: int
    username: str
    role: str                            # operator / supervisor / director / admin
    enabled: bool
    password_hash: str = ""              # env 超管恒为空串,登录走 env 凭证
    created_at: str = ""
    last_login_at: Optional[str] = None
    account_ids: tuple = ()              # 可见邮箱(仅子账号;admin 全部可见)
    full_name: str = ""                  # 内部显示姓名；旧账号可暂为空


@dataclass(frozen=True)
class ApiKey:
    """Agent API Key 元数据。明文 Key 只在创建时返回一次。"""

    id: int
    name: str
    key_prefix: str
    key_hash: str
    enabled: bool
    rate_limit_per_min: int
    created_at: str
    last_used_at: Optional[str]
    request_count: int


@dataclass(frozen=True)
class SyncResult:
    """单账号一次同步的结果。"""

    account_id: int
    email: str
    status: str                          # ok / error / auth_error / skipped
    new_messages: int = 0
    fetched: int = 0
    duration_ms: int = 0
    error: Optional[str] = None
