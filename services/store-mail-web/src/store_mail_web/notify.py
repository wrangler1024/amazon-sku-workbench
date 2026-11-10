"""飞书群机器人 Webhook 推送。

- 配置存 DB settings(feishu_webhook_url / feishu_webhook_secret /
  push_categories),设置页可在线改;env 变量作兜底默认值。
- 后台队列线程异步发,不阻塞 IMAP 串行同步;失败重试 2 次后丢弃并记日志。
- 支持飞书自定义机器人的「签名校验」(开启签名后必须带 timestamp+sign)。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import queue
import re
import threading
import time
import urllib.request
from typing import Optional

from .models import DEFAULT_PUSH_CATEGORIES
from .state import Store

logger = logging.getLogger(__name__)

MAX_QUEUE = 200
RETRIES = 2
RETRY_WAIT_SECONDS = 5
TIMEOUT_SECONDS = 10

SETTING_URL = "feishu_webhook_url"
SETTING_SECRET = "feishu_webhook_secret"
SETTING_PUSH_CATEGORIES = "push_categories"


def feishu_sign(secret: str, timestamp: int) -> str:
    """飞书自定义机器人签名:以 "timestamp\\nsecret" 为 key 的空消息 HMAC-SHA256。"""
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"),
                      digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def format_push_text(item: dict) -> str:
    """把首次识别的待办邮件组装成推送文本。"""
    subject = item.get("subject", "") or "(无主题)"
    code = item.get("otp_code")
    if code:
        subject = subject.replace(str(code), "[验证码已隐藏]")
    if re.search(r"verification code|security code|\botp\b|验证码", subject, re.I):
        subject = re.sub(r"\b\d{4,8}\b", "[验证码已隐藏]", subject)
    severity = item.get("severity")
    lines = [
        "📮 Xynigo 邮箱客户端 · 店铺待办" if severity else "📮 Xynigo 邮箱客户端 · 新邮件",
        f"[{severity + ' · ' + item.get('subtype', '') if severity else item.get('category', '其他')}] {item.get('display_name', '')}"
        f"{(' / ' + item['platform_name']) if item.get('platform_name') and item['platform_name'] != item.get('display_name') else ''}"
        f" ({item.get('email', '')})",
        f"主题: {subject}",
        f"发件人: {item.get('from_name') or item.get('from_addr', '')}",
    ]
    if item.get("amount"):
        lines.append(f"金额: {item['amount']}")
    if item.get("owner_name"):
        lines.append(f"负责人: {item['owner_name']}")
    if item.get("due_at"):
        lines.append(f"内部跟进目标: {item['due_at']}")
    lines.append(f"时间: {item.get('received_at', '')}")
    if item.get("link"):
        lines.append(f"处理入口: {item['link']}")
    return "\n".join(lines)


class PushNotifier:
    """读取 store 配置,把新邮件推送出去。"""

    def __init__(self, store: Store, env_url: str = "", env_secret: str = ""):
        self._store = store
        self._env_url = (env_url or "").strip()
        self._env_secret = (env_secret or "").strip()
        self._queue: queue.Queue = queue.Queue(maxsize=MAX_QUEUE)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ---------- 配置 ----------

    def config(self) -> dict:
        """DB 配置优先,env 兜底。返回 {url, secret, push_categories(set)}。

        新同步只将 P0/P1 待办送入此队列。分类设置是附加过滤，
        默认允许全部旧分类，避免新业务事件被旧分类漏掉。
        """
        url = (self._store.get_setting(SETTING_URL) or "").strip() or self._env_url
        secret = ((self._store.get_setting(SETTING_SECRET) or "").strip()
                  or self._env_secret)
        raw_categories = ((self._store.get_setting(SETTING_PUSH_CATEGORIES) or "")
                          .strip())
        if raw_categories:
            categories = tuple(
                c.strip() for c in raw_categories.split(",") if c.strip())
        else:
            categories = tuple(DEFAULT_PUSH_CATEGORIES)
        return {"url": url, "secret": secret, "push_categories": categories}

    # ---------- 队列 ----------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker_loop,
                                        name="push-notifier", daemon=True)
        self._thread.start()
        logger.info("推送工作线程已启动")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def enqueue(self, item: dict) -> bool:
        """仅首次识别的 P0/P1 待办入队；系统监控异常走 enqueue_system。"""
        config = self.config()
        if (not config["url"] or item.get("severity") not in {"P0", "P1"}
                or item.get("action_required") is not True):
            return False
        if (item.get("severity") != "P0" and config["push_categories"]
                and item.get("category") not in config["push_categories"]):
            return False
        try:
            self._queue.put_nowait(item)
            return True
        except queue.Full:
            logger.warning("推送队列已满(%d),丢弃: %s", MAX_QUEUE,
                           item.get("subject"))
            return False

    def enqueue_system(self, text: str) -> bool:
        """系统监控异常通知；不包含邮箱授权码或邮件正文。"""
        if not self.config()["url"]:
            return False
        try:
            self._queue.put_nowait({"_system_text": text})
            return True
        except queue.Full:
            logger.warning("推送队列已满，系统监控异常未入队")
            return False

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            text = item.get("_system_text") or format_push_text(item)
            self._deliver(text, retries=RETRIES)

    def _deliver(self, text: str, retries: int) -> None:
        attempt = 0
        while attempt <= retries:
            result = self.send_now(text)
            if result["ok"]:
                return
            attempt += 1
            if attempt <= retries and not self._stop.wait(RETRY_WAIT_SECONDS):
                continue
            break
        logger.error("推送失败(已重试 %d 次)", attempt)

    # ---------- 直发(测试按钮用,同步返回结果) ----------

    def send_now(self, text: str) -> dict:
        config = self.config()
        url = config["url"]
        if not url:
            return {"ok": False, "error": "未配置飞书 Webhook 地址"}
        payload: dict = {"msg_type": "text", "content": {"text": text}}
        if config["secret"]:
            timestamp = int(time.time())
            payload["timestamp"] = str(timestamp)
            payload["sign"] = feishu_sign(config["secret"], timestamp)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw else {}
            # 新版返回 {"code":0,...};旧版 {"StatusCode":0,...};200 视为成功
            code = data.get("code", data.get("StatusCode", 0))
            if str(code) not in {"0"}:
                return {"ok": False, "error": f"飞书返回异常: {raw[:200]}"}
            return {"ok": True, "error": None}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
