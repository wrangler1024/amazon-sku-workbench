"""HTTP API 端到端测试:登录、Web 路由、Agent API、限流、静态文件。

用真实 ThreadingHTTPServer 跑在 127.0.0.1 随机端口,IMAP 用 Fake 注入。
"""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from http.cookiejar import CookieJar
from pathlib import Path

from store_mail_web.config import Settings
from store_mail_web.crypto import generate_master_key
from store_mail_web.http_api import start_http_server
from store_mail_web.models import ClassifiedMail, FetchedMail
from store_mail_web.service import ApiError, Service

BASE_TS = 1756900000


class FakeSession:
    mailboxes: dict = {}
    marked_seen: list = []

    def __init__(self, host, port, mailbox, auth_code, timeout=30):
        self.mailbox = mailbox

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def exists_count(self):
        return len(FakeSession.mailboxes.get(self.mailbox, {}))

    def search_uids_after(self, last_uid):
        return sorted(u for u in FakeSession.mailboxes.get(self.mailbox, {})
                      if u > last_uid)

    def search_uids_since_days(self, days):
        return sorted(FakeSession.mailboxes.get(self.mailbox, {}))

    def fetch_mail(self, uid):
        return FakeSession.mailboxes.get(self.mailbox, {}).get(uid)

    def mark_seen(self, uid):
        FakeSession.marked_seen.append((self.mailbox, uid))
        return True


def amazon_login_mail(uid: int, code: str = "482913") -> FetchedMail:
    return FetchedMail(
        uid=uid, message_id=f"<otp{uid}@amazon>",
        subject="Amazon: verification code",
        from_name="Amazon", from_addr="account@amazon.com",
        to_addr="store@example.invalid", internal_date=None,
        received_ts=BASE_TS + uid, size_bytes=2048, unread=True,
        body_text=f"Your verification code is {code}. Do not share it.",
        body_html=f"<p>Your verification code is <b>{code}</b>.</p>",
        preview="Your verification code is",
    )


class HttpApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        FakeSession.mailboxes = {}
        FakeSession.marked_seen = []
        settings = Settings(
            secret_key=generate_master_key(),
            admin_user="admin",
            admin_password="correct-pass",
            db_path=Path(cls.tmp.name) / "http.sqlite3",
            manual_sync_min_gap_seconds=0,
            mark_seen_on_open=True,
        )
        cls.service = Service.build(settings)
        cls.service.engine._session_factory = FakeSession  # 注入假 IMAP
        static_dir = Path(__file__).resolve().parents[1] / "src/store_mail_web/static"
        openapi_path = Path(__file__).resolve().parents[1] / "openapi.yaml"
        cls.server = start_http_server("127.0.0.1", 0, cls.service,
                                       static_dir, openapi_path)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.jar = CookieJar()
        cls.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cls.jar))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.service.store.close()
        cls.tmp.cleanup()

    def request(self, method, path, body=None, api_key=None, opener=None):
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if api_key:
            req.add_header("X-API-Key", api_key)
        try:
            resp = (opener or self.opener).open(req, timeout=15)
            raw = resp.read()
            status = resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
        try:
            return status, json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return status, raw

    # ---------- 01 基础 ----------

    def test_01_healthz_and_static(self):
        status, data = self.request("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(data["service"], "store-mail-web")

        status, raw = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<html", raw)
        self.assertNotIn(b'data-view="otp"', raw)
        self.assertIn(b'<script type="module" src="/static/app.js">', raw)
        status, raw = self.request("GET", "/static/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b'from "./inbox.js"', raw)
        status, raw = self.request("GET", "/static/inbox.js")
        self.assertEqual(status, 200)
        self.assertIn(b'f-finance', raw)
        self.assertIn(b'f-shipment', raw)
        self.assertNotIn(b'f-otp', raw)
        status, raw = self.request("GET", "/api/openapi.yaml")
        self.assertEqual(status, 200)
        self.assertIn(b"openapi", raw)
        # 静态文件白名单外 → 404
        status, _ = self.request("GET", "/static/../src/service.py")
        self.assertEqual(status, 404)

    # ---------- 02 登录 ----------

    def test_02_login_flow(self):
        status, data = self.request("GET", "/api/web/accounts")
        self.assertEqual(status, 401)

        status, data = self.request("POST", "/api/auth/login",
                                    body={"username": "admin", "password": "wrong"})
        self.assertEqual(status, 401)

        status, data = self.request("POST", "/api/auth/login",
                                    body={"username": "admin", "password": "correct-pass"})
        self.assertEqual(status, 200)
        self.assertTrue(any(c.name == "smw_session" for c in self.jar))

        status, data = self.request("GET", "/api/auth/me")
        self.assertEqual(status, 200)
        self.assertEqual(data["username"], "admin")

    # ---------- 03 账号管理(会话) ----------

    def test_03_account_management(self):
        status, data = self.request("POST", "/api/web/accounts", body={
            "display_name": "店铺A-US", "chinese_name": "美西一店", "email": "store@example.invalid",
            "auth_code": "SECRET-AUTH-1",
        })
        self.assertEqual(status, 201)
        self.assertEqual(data["account"]["chinese_name"], "美西一店")
        self.assertNotIn("auth_code", json.dumps(data))
        account_id = data["account"]["id"]

        # 授权码加密落库
        raw_db = Path(self.tmp.name, "http.sqlite3").read_bytes()
        self.assertNotIn(b"SECRET-AUTH-1", raw_db)

        # 重复邮箱 → 409
        status, _ = self.request("POST", "/api/web/accounts", body={
            "display_name": "重", "email": "store@example.invalid", "auth_code": "x"})
        self.assertEqual(status, 409)

        # 测试连接(Fake IMAP)
        status, data = self.request("POST", f"/api/web/accounts/{account_id}/test")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

    # ---------- 04 同步与邮件(会话) ----------

    def test_04_sync_and_web_messages(self):
        FakeSession.mailboxes["store@example.invalid"] = {
            1: FetchedMail(
                uid=1, message_id="<n1@x>", subject="普通推广邮件",
                from_name="推广", from_addr="promo@shop.com",
                to_addr="store@example.invalid", internal_date=None,
                received_ts=BASE_TS, size_bytes=10, unread=True,
                body_text="Big sale newsletter unsubscribe now",
                body_html=None, preview="Big sale"),
            2: amazon_login_mail(2),
            3: FetchedMail(
                uid=3, message_id="<n3@x>",
                subject="Action required: Account Health warning",
                from_name="Amazon", from_addr="account-health@amazon.com",
                to_addr="store@example.invalid", internal_date=None,
                received_ts=BASE_TS + 3, size_bytes=512, unread=True,
                body_text="Your Order Defect Rate exceeds the target.",
                body_html="<table><tr><td>Your Order Defect Rate exceeds the target.</td></tr></table>",
                preview="Your Order Defect Rate"),
        }
        status, data = self.request("POST", "/api/web/sync", body={"force": True})
        self.assertEqual(status, 200)
        result = data["results"][0]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["new_messages"], 3)

        status, data = self.request("GET", "/api/web/messages?limit=10")
        self.assertEqual(status, 200)
        self.assertEqual(data["total"], 3)

        status, data = self.request("GET", "/api/web/messages?q=verification")
        self.assertEqual(data["total"], 1)
        message_id = data["messages"][0]["id"]
        self.assertNotIn("otp_code", data["messages"][0])

        # Web 打开详情 → 本地已读 + IMAP 回写 \Seen
        status, data = self.request("GET", f"/api/web/messages/{message_id}")
        self.assertEqual(status, 200)
        self.assertIn("482913", data["message"]["body_text"])
        self.assertIn(("store@example.invalid", 2), FakeSession.marked_seen)
        status, data = self.request("GET", "/api/web/messages?unread_only=1")
        self.assertEqual(data["total"], 2)  # 推广 + 告警未读

        # 旧验证码入口已取消，原邮件正文仍可正常阅读。
        status, data = self.request("GET", "/api/web/otp/overview")
        self.assertEqual(status, 404)
        self.assertEqual(self.request("GET", "/api/web/messages?otp_only=1")[0], 400)

    def test_04b_html_view_defaults_and_isolation(self):
        status, data = self.request("GET", "/api/web/messages?q=verification")
        self.assertEqual(status, 200)
        message_id = data["messages"][0]["id"]
        status, data = self.request("GET", f"/api/web/messages/{message_id}")
        self.assertEqual(status, 200)
        self.assertTrue(data["message"]["has_html"])
        self.assertFalse(data["message"]["html_truncated"])

        url = self.base + f"/api/web/messages/{message_id}/html"
        with self.opener.open(url, timeout=15) as response:
            body = response.read().decode("utf-8")
            self.assertEqual(response.headers.get_content_type(), "text/html")
            self.assertIn("<b>482913</b>", body)
            self.assertIn("sandbox", response.headers["Content-Security-Policy"])
            self.assertIn("img-src data: https:", response.headers["Content-Security-Policy"])
            self.assertIn("style-src 'unsafe-inline' https:", response.headers["Content-Security-Policy"])
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        with self.opener.open(url + "?remote=0", timeout=15) as response:
            self.assertIn("img-src data:", response.headers["Content-Security-Policy"])
            self.assertNotIn("img-src data: https:", response.headers["Content-Security-Policy"])

        # The isolated HTML route is read-only; only opening the detail marks mail read.
        status, data = self.request("GET", "/api/web/messages?q=Account%20Health%20warning")
        warning = data["messages"][0]
        self.assertEqual(warning["unread"], 1)
        with self.opener.open(self.base + f"/api/web/messages/{warning['id']}/html", timeout=15) as response:
            self.assertIn(b"<table>", response.read())
        status, data = self.request("GET", "/api/web/messages?q=Account%20Health%20warning")
        self.assertEqual(data["messages"][0]["unread"], 1)

        anonymous = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()))
        self.assertEqual(self.request("GET", f"/api/web/messages/{message_id}/html",
                                      opener=anonymous)[0], 401)
        restricted = replace(self.service.store.get_user_by_username("admin"),
                             role="user", account_ids=())
        with self.assertRaises(ApiError) as denied:
            self.service.message_html(message_id, restricted)
        self.assertEqual(denied.exception.status, 404)
        self.assertEqual(self.request("GET", f"/api/web/messages/{message_id}/html?remote=maybe")[0], 400)
        # The text-only promotion is still readable, without an HTML viewer.
        status, data = self.request("GET", "/api/web/messages?limit=10")
        promo = next(m for m in data["messages"] if m["uid"] == 1)
        status, detail = self.request("GET", f"/api/web/messages/{promo['id']}")
        self.assertFalse(detail["message"]["has_html"])
        self.assertEqual(self.request("GET", f"/api/web/messages/{promo['id']}/html")[0], 404)

    # ---------- 05 Agent API ----------

    def test_05_agent_flow(self):
        status, data = self.request("POST", "/api/web/api-keys",
                                    body={"name": "测试Agent", "rate_limit_per_min": 200})
        self.assertEqual(status, 201)
        api_key = data["key"]
        self.assertTrue(api_key.startswith("smw_"))
        self.assertNotIn(api_key, json.dumps(data["api_key"]))

        # 未带 Key → 401;坏 Key → 401
        self.assertEqual(self.request("GET", "/api/v1/accounts")[0], 401)
        self.assertEqual(
            self.request("GET", "/api/v1/accounts", api_key="smw_bad")[0], 401)

        # 列账号
        status, data = self.request("GET", "/api/v1/accounts", api_key=api_key)
        self.assertEqual(status, 200)
        self.assertEqual(len(data["accounts"]), 1)
        self.assertEqual(data["accounts"][0]["email"], "store@example.invalid")
        self.assertEqual(data["accounts"][0]["chinese_name"], "美西一店")
        self.assertNotIn("auth_code", json.dumps(data))

        # 搜索已有邮件；取消的 OTP 筛选与接口不再可用。
        status, data = self.request("GET", "/api/v1/messages?q=verification",
                                    api_key=api_key)
        self.assertEqual(status, 200)
        self.assertEqual(data["total"], 1)
        message_id = data["messages"][0]["id"]
        self.assertNotIn("otp_code", data["messages"][0])
        self.assertEqual(self.request(
            "GET", "/api/v1/otp/latest?account_id=1", api_key=api_key)[0], 404)
        self.assertEqual(self.request(
            "GET", "/api/v1/messages?otp_only=1", api_key=api_key)[0], 400)

        # 读来源邮件(Agent 读不改变未读)
        status, before = self.request("GET", "/api/v1/messages?unread_only=1",
                                      api_key=api_key)
        status, data = self.request("GET", f"/api/v1/messages/{message_id}",
                                    api_key=api_key)
        self.assertEqual(status, 200)
        self.assertIn("482913", data["message"]["body_text"])
        self.assertNotIn("body_html", data["message"])
        status, after = self.request("GET", "/api/v1/messages?unread_only=1",
                                     api_key=api_key)
        self.assertEqual(after["total"], before["total"])

        # 同步接口
        status, data = self.request("POST", "/api/v1/sync", body={"account_id": 1},
                                    api_key=api_key)
        self.assertEqual(status, 200)
        self.assertEqual(data["results"][0]["status"], "ok")

        # 错误路径
        status, data = self.request("GET", "/api/v1/messages/99999",
                                    api_key=api_key)
        self.assertEqual(status, 404)
        self.assertEqual(data["error"]["code"], "not_found")

        status, data = self.request("GET", "/api/v1/messages?limit=999",
                                    api_key=api_key)
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "validation_error")

    # ---------- 06 限流 ----------

    def test_06_rate_limit(self):
        status, data = self.request("POST", "/api/web/api-keys",
                                    body={"name": "限流Key", "rate_limit_per_min": 3})
        api_key = data["key"]
        statuses = [self.request("GET", "/api/v1/accounts", api_key=api_key)[0]
                    for _ in range(4)]
        self.assertEqual(statuses[:3], [200, 200, 200])
        self.assertEqual(statuses[3], 429)

    # ---------- 07 Key 吊销 ----------

    def test_07_key_disable(self):
        status, data = self.request("POST", "/api/web/api-keys",
                                    body={"name": "临时Key"})
        key_id, api_key = data["api_key"]["id"], data["key"]
        self.assertEqual(self.request("GET", "/api/v1/accounts",
                                      api_key=api_key)[0], 200)
        status, _ = self.request("POST", f"/api/web/api-keys/{key_id}/enable",
                                 body={"enabled": False})
        self.assertEqual(status, 200)
        self.assertEqual(self.request("GET", "/api/v1/accounts",
                                      api_key=api_key)[0], 401)

    # ---------- 08 看板 / 推送配置 / 重新分类 ----------

    def test_08_dashboard_notify_reclassify(self):
        status, data = self.request("GET", "/api/web/dashboard")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["accounts"]), 1)
        stats = data["accounts"][0]["stats"]
        self.assertIsNotNone(stats["last_alert_at"])
        self.assertEqual(len(data["recent_important"]), 1)
        self.assertEqual(data["recent_important"][0]["category"], "告警")

        # 重要筛选(告警/侵权/绩效/回款)
        status, data = self.request("GET", "/api/web/messages?important_only=1")
        self.assertEqual(status, 200)
        self.assertEqual(data["total"], 1)

        # 推送配置:非法 URL 拒绝,合法 URL 保存
        status, data = self.request("PUT", "/api/web/notify-config",
                                    body={"url": "https://evil.example.com/hook"})
        self.assertEqual(status, 400)
        status, data = self.request(
            "PUT", "/api/web/notify-config",
            body={"url": "https://open.feishu.cn/open-apis/bot/v2/hook/t",
                  "push_categories": "告警,回款"})
        self.assertEqual(status, 200)
        self.assertTrue(data["configured"])
        status, data = self.request("GET", "/api/web/notify-config")
        self.assertEqual(data["push_categories"], "告警,回款")
        status, data = self.request("PUT", "/api/web/notify-config",
                                    body={"push_categories": "不存在的分类"})
        self.assertEqual(status, 400)

        # 重新分类存量
        status, data = self.request("POST", "/api/web/reclassify")
        self.assertEqual(status, 200)
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["reclassified"], 3)

    # ---------- 09 多用户:隔离 / 独立已读 / 权限 ----------

    def test_09_multi_user(self):
        # 管理员创建只可见邮箱1(store@example.invalid)的子账号
        status, data = self.request("POST", "/api/web/users", body={
            "username": "kefu01", "full_name": "张三", "password": "pass123456",
            "account_ids": [1]})
        self.assertEqual(status, 201)
        self.assertEqual(data["user"]["full_name"], "张三")
        self.assertEqual(data["user"]["role"], "operator")
        self.assertEqual(data["user"]["account_ids"], [1])
        # 重名 / 短密码 / 非法用户名
        self.assertEqual(self.request("POST", "/api/web/users", body={
            "username": "kefu01", "full_name": "张三", "password": "pass123456"})[0], 409)
        self.assertEqual(self.request("POST", "/api/web/users", body={
            "username": "kefu02", "full_name": "李四", "password": "123"})[0], 400)
        self.assertEqual(self.request("POST", "/api/web/users", body={
            "username": "kefu02", "password": "pass123456"})[0], 400)

        # 子账号登录(独立 cookie jar)
        sub = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()))
        status, data = self.request("POST", "/api/auth/login",
                                    body={"username": "kefu01",
                                          "password": "pass123456"},
                                    opener=sub)
        self.assertEqual(status, 200)
        self.assertEqual(data["user"]["role"], "operator")
        self.assertEqual(data["user"]["full_name"], "张三")
        status, data = self.request("GET", "/api/auth/me", opener=sub)
        self.assertEqual(data["role"], "operator")

        # 可见性:账号列表 / 收件箱只含邮箱1
        status, data = self.request("GET", "/api/web/accounts", opener=sub)
        self.assertEqual(len(data["accounts"]), 1)
        self.assertEqual(data["accounts"][0]["email"], "store@example.invalid")
        self.assertEqual(data["accounts"][0]["chinese_name"], "美西一店")
        status, data = self.request("GET", "/api/web/messages?limit=10",
                                    opener=sub)
        self.assertEqual(data["total"], 3)   # 3 封都在邮箱1
        for row in data["messages"]:
            self.assertEqual(row["account_id"], 1)

        # 管理页面对子账号 403
        for path, method in [("/api/web/users", "GET"),
                             ("/api/web/api-keys", "GET"),
                             ("/api/web/rules", "PUT"),
                             ("/api/web/notify-config", "PUT"),
                             ("/api/web/reclassify", "POST"),
                             ("/api/web/sync", "POST")]:
            status, _ = self.request(method, path, body={}, opener=sub)
            self.assertEqual(status, 403, f"{method} {path} 应 403")

        # 独立已读:子账号读一封,管理员视角的未读数不变
        # (管理员在 test_04 已读过 1 封,基线取管理员自己的当前值)
        status, data = self.request("GET", "/api/web/messages?unread_only=1")
        admin_unread_before = data["total"]
        status, data = self.request("GET", "/api/web/messages?unread_only=1",
                                    opener=sub)
        sub_unread_before = data["total"]
        status, data = self.request("GET", "/api/web/messages?limit=1",
                                    opener=sub)
        target = data["messages"][0]["id"]
        status, data = self.request("GET", f"/api/web/messages/{target}",
                                    opener=sub)
        self.assertEqual(status, 200)
        status, data = self.request("GET", "/api/web/messages?unread_only=1",
                                    opener=sub)
        self.assertEqual(data["total"], sub_unread_before - 1)
        status, data = self.request("GET", "/api/web/messages?unread_only=1")
        self.assertEqual(data["total"], admin_unread_before)  # 管理员不受影响

        # 全部标为已读只影响自己
        status, data = self.request("POST", "/api/web/messages/mark-all-read",
                                    opener=sub)
        self.assertEqual(status, 200)
        status, data = self.request("GET", "/api/web/messages?unread_only=1",
                                    opener=sub)
        self.assertEqual(data["total"], 0)
        status, data = self.request("GET", "/api/web/messages?unread_only=1")
        self.assertEqual(data["total"], admin_unread_before)

        # 子账号看板只统计可见邮箱
        status, data = self.request("GET", "/api/web/dashboard", opener=sub)
        self.assertEqual(len(data["accounts"]), 1)
        self.assertEqual(data["accounts"][0]["chinese_name"], "美西一店")
        status, data = self.request("GET", "/api/web/sync/status", opener=sub)
        self.assertEqual(status, 200)
        self.assertEqual(len(data["accounts"]), 1)

        # 停用后登录被拒,旧会话立即失效
        self.request("PUT", "/api/web/users/2", body={"enabled": False})
        self.assertEqual(self.request("POST", "/api/auth/login",
                                      body={"username": "kefu01",
                                            "password": "pass123456"})[0], 401)
        self.assertEqual(self.request("GET", "/api/web/messages",
                                      opener=sub)[0], 401)

        # 恢复并删除
        self.request("PUT", "/api/web/users/2", body={"enabled": True})
        status, data = self.request("PUT", "/api/web/users/1", body={"full_name": "胡康凯"})
        self.assertEqual(status, 200)
        self.assertEqual(data["user"]["full_name"], "胡康凯")
        self.assertEqual(self.request("PUT", "/api/web/users/1",
                                      body={"enabled": False})[0], 400)
        status, _ = self.request("DELETE", "/api/web/users/2")
        self.assertEqual(status, 200)
        status, data = self.request("GET", "/api/web/users")
        self.assertEqual(len(data["users"]), 1)   # 只剩 env 管理员

    # ---------- 10 邮件标记 ----------

    def test_10_message_flags(self):
        status, data = self.request("GET", "/api/web/messages?limit=1")
        message_id = data["messages"][0]["id"]
        self.assertEqual(data["messages"][0]["flags"], [])

        # 加标记 → 响应与列表都返回;详情附带
        status, data = self.request(
            "POST", f"/api/web/messages/{message_id}/flags",
            body={"flag": "star", "on": True})
        self.assertEqual(status, 200)
        self.assertEqual(data["flags"], ["star"])
        status, data = self.request("GET", "/api/web/messages?limit=5")
        row = next(r for r in data["messages"] if r["id"] == message_id)
        self.assertEqual(row["flags"], ["star"])
        status, data = self.request("GET", f"/api/web/messages/{message_id}")
        self.assertEqual(data["message"]["flags"], ["star"])

        # 标记筛选:按类型 / 任意 / 取消
        status, data = self.request("GET", "/api/web/messages?flag=star")
        self.assertEqual(data["total"], 1)
        status, data = self.request("GET", "/api/web/messages?flag=any")
        self.assertEqual(data["total"], 1)
        status, data = self.request("GET", "/api/web/messages?flag=pin")
        self.assertEqual(data["total"], 0)
        self.request("POST", f"/api/web/messages/{message_id}/flags",
                     body={"flag": "star", "on": False})
        status, data = self.request("GET", "/api/web/messages?flag=any")
        self.assertEqual(data["total"], 0)

        # 非法标记类型 / 参数校验
        status, _ = self.request("POST", f"/api/web/messages/{message_id}/flags",
                                 body={"flag": "rainbow", "on": True})
        self.assertEqual(status, 400)
        status, _ = self.request("POST", f"/api/web/messages/{message_id}/flags",
                                 body={"flag": "star"})
        self.assertEqual(status, 400)

    def test_99_finance_and_shipment_filters(self):
        for uid, subject, category in (
            (10, "Card charge declined", "扣款"),
            (11, "FBA shipment difference", "货件"),
        ):
            mail = FetchedMail(
                uid=uid, message_id=f"<filter{uid}@amazon.com>", subject=subject,
                from_name="Amazon", from_addr="notice@amazon.com",
                to_addr="store@example.invalid", internal_date=None,
                received_ts=BASE_TS + uid, size_bytes=100, unread=True,
                body_text=subject, body_html=None, preview=subject)
            self.service.store.upsert_message(
                1, mail, ClassifiedMail(True, category, "中"))

        for path, expected in (
            ("?finance_only=1", {10}),
            ("?shipment_only=1", {11}),
            ("?finance_only=1&shipment_only=1", {10, 11}),
            ("?finance_only=1&shipment_only=1&q=shipment", {11}),
        ):
            status, data = self.request("GET", "/api/web/messages" + path)
            self.assertEqual(status, 200)
            self.assertEqual({m["uid"] for m in data["messages"]}, expected)

        status, data = self.request("POST", "/api/web/api-keys",
                                    body={"name": "筛选测试"})
        api_key = data["key"]
        status, data = self.request(
            "GET", "/api/v1/messages?finance_only=1&shipment_only=1",
            api_key=api_key)
        self.assertEqual(status, 200)
        self.assertEqual({m["uid"] for m in data["messages"]}, {10, 11})


if __name__ == "__main__":
    unittest.main()
