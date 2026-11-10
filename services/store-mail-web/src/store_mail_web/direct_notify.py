"""Private Feishu inbox notifications, persisted atomically with each new email."""
from __future__ import annotations
import re
import threading
import time

from .feishu import FeishuError


def mail_text(mail, public_url):
    subject = mail.get("subject") or "（无主题）"
    if re.search(r"verification|security code|one.time|\botp\b|验证码|验证代码|动态码",subject,re.I):
        subject = "验证类邮件（请进入邮箱客户端查看）"
    subject=re.sub(r"[\r\n\x00-\x1f]", " ",subject)[:180]
    return "\n".join([
        "📮 Xynigo 云邮箱 · " + ("优先关注" if mail.get("priority")=="高" else "新邮件"),
        f"店铺：{mail.get('chinese_name') or mail['display_name']}",
        f"分类：{mail.get('category') or '其他'} · 关注级别：{mail.get('priority') or '低'}",
        f"收件邮箱：{mail['email']}",
        f"发件人：{str(mail.get('from_name') or mail.get('from_addr') or '')[:120]}",
        f"主题：{subject}",
        f"时间：{mail['received_at']}",
        "主管核实重要内容后，请转告本组运营及时处理。",
        "查看邮件：" + public_url.rstrip("/") + "/#/message/" + str(mail["id"]),
    ])


class DirectNotifier:
    def __init__(self, store, client, public_url):
        self.store,self.client,self.public_url=store,client,public_url
        self._stop=threading.Event()
        self._thread=None

    def start(self):
        if self._thread:
            return
        self._stop.clear()
        self._thread=threading.Thread(target=self._loop,name="feishu-private-notifier",daemon=True)
        self._thread.start()

    def stop(self,timeout=15):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
            self._thread=None

    def config(self):
        return {"mode":"private","configured":self.client.configured,
                "enabled":self.store.get_setting("feishu_dm_enabled")=="1",
                "epoch":self.store.get_setting("feishu_dm_epoch"),
                "worker_running":bool(self._thread and self._thread.is_alive()),
                "app_id":self.client.settings.feishu_app_id,
                **self.store.notification_status()}

    def set_enabled(self,enabled):
        with self.store._lock:
            # Timestamp and switch are one atomic transaction.
            if enabled and self.store.get_setting("feishu_dm_enabled")!="1":
                self.store._conn.execute("INSERT INTO settings(key,value) VALUES ('feishu_dm_epoch',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(time.time()),))
            self.store._conn.execute("INSERT INTO settings(key,value) VALUES ('feishu_dm_enabled',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",("1" if enabled else "0",))
            if not enabled:
                self.store._conn.execute("UPDATE notification_outbox SET status='skipped',last_error='全局提醒已关闭' "
                    "WHERE status IN ('pending','retry','sending')")
            self.store._conn.commit()

    def enqueue(self, item):
        # Legacy risk/group notification hook is intentionally disconnected.
        return False

    def enqueue_system(self, text):
        return False

    def run_once(self):
        if not self.client.configured or self.store.get_setting("feishu_dm_enabled")!="1":
            return False
        item=self.store.claim_notification()
        if not item:
            return False
        if item["first_attempt"] and time.time()-item["first_attempt"]>3500:
            self.store.finish_notification(item["id"],"failed","超过飞书去重窗口，请人工核查送达结果")
            return True
        context=self.store.notification_context(item)
        if not context:
            self.store.finish_notification(item["id"],"skipped","邮箱或成员已停用、授权已撤销或绑定已变更")
            return True
        try:
            if not self.client.member_active(item["open_id"]):
                self.store.revoke_sessions(item["user_id"])
                self.store.finish_notification(item["id"],"skipped","飞书成员已停用或离职")
                return True
            # Re-read after the external lookup; do not use the old authorization snapshot.
            context=self.store.notification_context(item)
            if not context:
                self.store.finish_notification(item["id"],"skipped","发送前授权检查未通过")
                return True
            mid=self.client.send_text(item["open_id"],mail_text(context,self.public_url),item["uuid"])
            self.store.finish_notification(item["id"],"sent",message_id=mid)
        except FeishuError as exc:
            if exc.code=="230013":
                self.store.revoke_sessions(item["user_id"])
            retry=exc.retryable and item["attempts"]<8
            self.store.finish_notification(item["id"],"retry" if retry else "failed",
                "飞书投递失败："+exc.code,delay=min(300,5*2**item["attempts"]))
        return True

    def _loop(self):
        while not self._stop.is_set():
            try:
                worked=self.run_once()
            except Exception:
                # The lease will recover the task. Do not leak mail/credentials into logs.
                worked=False
            self._stop.wait(0.25 if worked else 2)
