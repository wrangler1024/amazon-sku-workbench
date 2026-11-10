"""同步引擎:IMAP 增量拉取、失败退避、后台调度。

节奏设计(针对 163 对频繁连接敏感):
- 全局串行锁在 imap_client 层,同一时刻全服务只有一个 IMAP 连接。
- 每账号有稳定抖动(按账号 id 派生),错开各账号连接时间。
- 失败按 2 倍指数退避,上限 SYNC_BACKOFF_MAX_SECONDS;授权码错误
  (auth_error)不自动密集重试,按 4 倍常规间隔退避,等运营换授权码。
- 手动同步有最小间隔保护,防止连点触发风控。
"""

from __future__ import annotations

import logging
import random
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from .classifier import (
    classify, extract_amount, is_amazon_mail,
    load_amazon_rules, load_category_rules,
)
from .config import Settings
from .imap_client import ImapAuthError, ImapError, ImapNetworkError, ImapSession
from .models import Account, ClassifiedMail, SyncResult
from .state import Store

logger = logging.getLogger(__name__)

SCHEDULER_TICK_SECONDS = 5
AUTH_ERROR_BACKOFF_MULTIPLIER = 4


def _account_jitter(account_id: int, spread_seconds: int) -> int:
    """每账号稳定抖动:同一账号每次相同,不同账号错开。"""
    if spread_seconds <= 0:
        return 0
    return (account_id * 2654435761) % spread_seconds


def _parse_iso_ts(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


class SyncEngine:
    """同步编排。所有公开方法线程安全(账号粒度互斥)。"""

    def __init__(self, store: Store, settings: Settings,
                 session_factory: Optional[Callable[..., object]] = None):
        self._store = store
        self._settings = settings
        self._session_factory = session_factory or ImapSession
        self._notifier = None              # PushNotifier,由 Service.build 注入
        self._account_locks: dict[int, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        # account_id -> (连续失败次数, 退避到期时间戳)
        self._backoff: dict[int, tuple[int, float]] = {}
        self._last_sync_finished: dict[int, float] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def set_notifier(self, notifier) -> None:
        """注入飞书推送器(可选;新邮件按分类入队推送)。"""
        self._notifier = notifier

    def set_risk_callback(self, callback) -> None:
        """新邮件落库后建立店铺事件；失败时下次启动会补评估。"""
        self._risk_callback = callback

    # ---------- 锁与退避 ----------

    def _lock_for(self, account_id: int) -> threading.Lock:
        with self._locks_guard:
            if account_id not in self._account_locks:
                self._account_locks[account_id] = threading.Lock()
            return self._account_locks[account_id]

    def _backoff_wait(self, account_id: int) -> float:
        """还需等待多少秒才允许下一次调度同步;0 表示现在可以。"""
        entry = self._backoff.get(account_id)
        if not entry:
            return 0.0
        _, until = entry
        return max(0.0, until - time.time())

    def _record_success(self, account_id: int) -> None:
        self._backoff.pop(account_id, None)

    def _record_failure(self, account_id: int, auth_error: bool) -> None:
        fails, _ = self._backoff.get(account_id, (0, 0.0))
        fails += 1
        base = self._settings.sync_interval_seconds
        if auth_error:
            wait = min(base * AUTH_ERROR_BACKOFF_MULTIPLIER * fails,
                       self._settings.sync_backoff_max_seconds)
        else:
            wait = min(base * (2 ** min(fails - 1, 6)),
                       self._settings.sync_backoff_max_seconds)
        wait += random.uniform(0, 10)
        self._backoff[account_id] = (fails, time.time() + wait)

    # ---------- 单账号同步 ----------

    def sync_account(self, account_id: int, *, manual: bool = False,
                     force: bool = False) -> SyncResult:
        account = self._store.get_account(account_id)
        if account is None:
            raise ValueError(f"账号不存在: {account_id}")
        if not account.enabled:
            return SyncResult(account.id, account.email, "skipped",
                              error="账号已停用")

        lock = self._lock_for(account_id)
        if not lock.acquire(blocking=False):
            return SyncResult(account.id, account.email, "skipped",
                              error="该账号正在同步中")
        try:
            if manual and not force:
                last = self._last_sync_finished.get(account_id, 0.0)
                gap = time.time() - last
                if gap < self._settings.manual_sync_min_gap_seconds:
                    return SyncResult(
                        account.id, account.email, "skipped",
                        error=(
                            f"最近 {int(gap)} 秒内刚同步过,请 "
                            f"{self._settings.manual_sync_min_gap_seconds - int(gap)} "
                            "秒后再试"
                        ),
                    )
            return self._sync_locked(account)
        finally:
            self._last_sync_finished[account_id] = time.time()
            lock.release()

    def _sync_locked(self, account: Account) -> SyncResult:
        started = time.monotonic()
        new_messages = 0
        fetched = 0
        push_items: list[dict] = []
        try:
            auth_code = self._store.get_auth_code(account.id)
            session_factory = self._session_factory
            with session_factory(
                host=account.imap_host, port=account.imap_port,
                mailbox=account.email, auth_code=auth_code,
                timeout=self._settings.imap_timeout,
            ) as session:
                if account.last_uid > 0:
                    uids = session.search_uids_after(account.last_uid)
                    # 从最早的未处理 UID 继续，不能只取最后 N 封并推进游标。
                    uids = uids[:self._settings.per_run_limit]
                else:
                    uids = session.search_uids_since_days(self._settings.initial_days)
                    uids = uids[-self._settings.initial_limit:]

                amazon_rules = load_amazon_rules(self._store.get_setting("amazon_rules"))
                category_rules = load_category_rules(self._store.get_setting("category_rules"))

                max_uid = account.last_uid
                failed_uid = None
                for uid in uids:
                    mail = session.fetch_mail(uid)
                    if mail is None:
                        logger.warning("拉取失败，保留游标以便重试: %s uid=%s",
                                       account.email, uid)
                        failed_uid = uid
                        break
                    amazon = is_amazon_mail(mail.from_addr, mail.subject, amazon_rules)
                    category, priority = classify(
                        mail.subject, mail.from_addr, mail.preview, category_rules)
                    amount = (extract_amount(mail.subject, mail.body_text)
                              if category == "回款" else None)
                    classified = ClassifiedMail(amazon, category, priority, amount)
                    inserted = self._store.upsert_message(account.id, mail, classified)
                    fetched += 1
                    if inserted:
                        new_messages += 1
                        risk_event_id = None
                        callback = getattr(self, "_risk_callback", None)
                        if callback is not None:
                            try:
                                risk_event_id = callback(account.id, uid)
                            except Exception:
                                logger.exception("风险判定失败 %s uid=%s", account.email, uid)
                        received_at = (
                            mail.internal_date
                            or datetime.fromtimestamp(
                                mail.received_ts, tz=timezone.utc
                            ).isoformat(timespec="seconds"))
                        event = (self._store.risk_get_event(risk_event_id)
                                 if risk_event_id else None)
                        if (event and not event["archived"] and event["action_required"]
                                and event["severity"] in {"P0", "P1"}
                                and event["message_count"] == 1):
                            push_items.append({
                                "display_name": account.chinese_name or account.display_name,
                                "platform_name": account.display_name,
                                "email": account.email,
                                "category": category,
                                "severity": event["severity"],
                                "action_required": True,
                                "subtype": event["subtype"],
                                "owner_name": event["owner_display_name"],
                                "due_at": event["due_at"],
                                "subject": mail.subject,
                                "from_name": mail.from_name,
                                "from_addr": mail.from_addr,
                                "amount": event["amount_text"] or amount,
                                "received_at": received_at,
                                "link": (self._settings.public_url.rstrip("/") +
                                         f"/#/event/{risk_event_id}")
                                        if self._settings.public_url else None,
                            })
                    max_uid = max(max_uid, uid)

            if failed_uid is not None:
                self._store.set_sync_state(
                    account.id, "error", f"UID {failed_uid} 拉取失败，待重试",
                    last_uid=max_uid)
                self._record_failure(account.id, auth_error=False)
                self._notify_monitoring_interruption(account, "单封邮件拉取失败")
            else:
                self._store.set_sync_state(account.id, "ok", None, last_uid=max_uid)
                self._record_success(account.id)
            # 推送在 IMAP 会话关闭后入队,不占用串行连接
            if self._notifier is not None:
                for item in push_items:
                    self._notifier.enqueue(item)
            duration = int((time.monotonic() - started) * 1000)
            logger.info("同步完成 %s: 拉取 %d, 新增 %d, 待推送 %d, 耗时 %dms",
                        account.email, fetched, new_messages, len(push_items),
                        duration)
            return SyncResult(account.id, account.email,
                              "error" if failed_uid is not None else "ok",
                              new_messages=new_messages, fetched=fetched,
                              duration_ms=duration,
                              error=(f"UID {failed_uid} 拉取失败，待重试"
                                     if failed_uid is not None else None))
        except ImapAuthError as exc:
            self._record_failure(account.id, auth_error=True)
            self._store.set_sync_state(account.id, "auth_error", str(exc)[:500])
            self._notify_monitoring_interruption(account, "授权失败")
            logger.error("授权失败 %s: %s", account.email, exc)
            return SyncResult(account.id, account.email, "auth_error", error=str(exc),
                              duration_ms=int((time.monotonic() - started) * 1000))
        except (ImapNetworkError, ImapError) as exc:
            self._record_failure(account.id, auth_error=False)
            self._store.set_sync_state(account.id, "error", str(exc)[:500])
            self._notify_monitoring_interruption(account, "同步失败")
            logger.warning("同步失败 %s: %s", account.email, exc)
            return SyncResult(account.id, account.email, "error", error=str(exc),
                              duration_ms=int((time.monotonic() - started) * 1000))

    def _notify_monitoring_interruption(self, account: Account, reason: str) -> None:
        if account.last_sync_status in {"auth_error", "error"}:
            return  # 状态持续期间只提醒一次，避免每轮刷群
        if self._notifier is not None:
            label = (f"{account.chinese_name} / {account.display_name}"
                     if account.chinese_name else account.display_name)
            self._notifier.enqueue_system(
                f"⚠️ 邮箱监控中断 · {label} ({account.email})\n"
                f"原因：{reason}。请管理员在 Xynigo 邮箱客户端核对并恢复连接。")

    def sync_all(self, *, manual: bool = False, force: bool = False) -> list[SyncResult]:
        results = []
        for account in self._store.list_accounts(enabled_only=True):
            results.append(self.sync_account(account.id, manual=manual, force=force))
        return results

    # ---------- 回写已读 ----------

    def mark_seen(self, account_id: int, uid: int) -> bool:
        """把 \\Seen 回写到 IMAP。尽力而为,失败只记日志。"""
        try:
            account = self._store.get_account(account_id)
            if account is None:
                return False
            auth_code = self._store.get_auth_code(account_id)
            with self._session_factory(
                host=account.imap_host, port=account.imap_port,
                mailbox=account.email, auth_code=auth_code,
                timeout=self._settings.imap_timeout,
            ) as session:
                return session.mark_seen(uid)
        except Exception as exc:
            logger.warning("回写已读失败 account=%s uid=%s: %s", account_id, uid, exc)
            return False

    def test_account(self, account: Account) -> dict:
        """登录 + SELECT 探活,走可注入的会话工厂。返回 {ok, exists, error}。"""
        try:
            auth_code = self._store.get_auth_code(account.id)
            with self._session_factory(
                host=account.imap_host, port=account.imap_port,
                mailbox=account.email, auth_code=auth_code,
                timeout=self._settings.imap_timeout,
            ) as session:
                return {"ok": True, "exists": session.exists_count(), "error": None}
        except ImapError as exc:
            return {"ok": False, "exists": None, "error": str(exc)}
        except Exception as exc:  # 网络/系统异常
            return {"ok": False, "exists": None, "error": f"连接失败: {exc}"}

    # ---------- 后台调度 ----------

    def start_scheduler(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._scheduler_loop, name="sync-scheduler", daemon=True)
        self._thread.start()
        logger.info("同步调度已启动: 间隔 %d 秒 + 抖动 %d 秒",
                    self._settings.sync_interval_seconds,
                    self._settings.sync_jitter_seconds)

    def stop_scheduler(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def scheduler_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _scheduler_loop(self) -> None:
        while not self._stop.wait(SCHEDULER_TICK_SECONDS):
            try:
                self._scheduler_tick()
            except Exception:  # pragma: no cover - 兜底
                logger.exception("同步调度循环异常")

    def _scheduler_tick(self) -> None:
        now = time.time()
        interval = self._settings.sync_interval_seconds
        for account in self._store.list_accounts(enabled_only=True):
            effective = interval + _account_jitter(
                account.id, self._settings.sync_jitter_seconds)
            last_finished = self._last_sync_finished.get(account.id)
            if last_finished is None:
                last_finished = _parse_iso_ts(account.last_sync_at) or 0.0
            if now - last_finished < effective:
                continue
            if self._backoff_wait(account.id) > 0:
                continue
            self.sync_account(account.id)

    # ---------- 状态视图 ----------

    def sync_status(self) -> list[dict]:
        """每账号的同步状态(含调度器内部视角),供前端展示。"""
        rows = []
        for account in self._store.list_accounts():
            fails, until = self._backoff.get(account.id, (0, 0.0))
            rows.append({
                "account_id": account.id,
                "email": account.email,
                "display_name": account.display_name,
                "chinese_name": account.chinese_name,
                "enabled": account.enabled,
                "last_uid": account.last_uid,
                "last_sync_at": account.last_sync_at,
                "last_success_at": account.last_success_at,
                "last_sync_status": account.last_sync_status,
                "last_error": account.last_error,
                "backoff_failures": fails,
                "backoff_until": (
                    datetime.fromtimestamp(until, tz=timezone.utc).isoformat(timespec="seconds")
                    if fails else None
                ),
            })
        return rows
