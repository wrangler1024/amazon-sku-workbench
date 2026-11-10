"""启动完全离线的店铺看板演示；使用临时数据库和虚构邮箱。

    PYTHONPATH=src python3 scripts/dashboard_demo.py --port 8791
    浏览器：http://127.0.0.1:8791  账号 demo / 密码 demo-local-only

不会连接 IMAP、飞书或生产数据库。Ctrl+C 后临时数据库自动删除。
"""

from __future__ import annotations

import argparse
import signal
import tempfile
import threading
import time
from pathlib import Path

from store_mail_web.config import Settings
from store_mail_web.classifier import classify, extract_amount
from store_mail_web.crypto import generate_master_key, hash_password
from store_mail_web.http_api import start_http_server
from store_mail_web.models import ClassifiedMail, FetchedMail
from store_mail_web.service import Service


def populate(service: Service) -> None:
    stores = [
        ("Maple-CA-01", "枫叶一店", "CA"), ("Maple-CA-02", "枫叶二店", "CA"),
        ("West-US-01", "美西一店", "US"), ("East-US-02", "美东二店", "US"),
        ("Sun-MX", "日光店", "MX"), ("London-UK", "雾都店", "UK"),
        ("Coast-AU", "海岸店", "AU"), ("North-CA", "北岸店", "CA"),
        ("Bridge-US", "星桥店", "US"),
    ]
    owner = service.store.create_user("ops_team", hash_password("demo-local-only"),
                                      full_name="演示运营")
    accounts = []
    for i, (platform_name, chinese_name, site) in enumerate(stores, 1):
        account = service.store.create_account(
            platform_name, f"demo-{site.lower()}-{i}@example.invalid", "DUMMY-NOT-USABLE",
            "imap.invalid", 993, site=site, owner_user_id=owner.id,
            chinese_name=chinese_name)
        service._grant_owner_account_access(account.id, owner.id)
        service.store.set_sync_state(account.id, "ok", None)
        accounts.append(account)
    cases = [
        (0, "Your seller account is at risk of deactivation", 42),
        (0, "Listing suppressed for ASIN B012345678", 31),
        (0, "Listing suppressed for ASIN B012345678 — action required", 30),
        (1, "Stranded inventory for ASIN B087654321", 125),
        (2, "Shipment discrepancy for FBA123456789", 78),
        (3, "Charge was declined for your seller account", 55),
        (4, "Payment has been initiated: USD 1,241.50", 92),
        (5, "New buyer message about order 123-1234567-1234567", 101),
        (6, "Provide compliance documents for ASIN B043216789", 160),
        (7, "September promotion newsletter", 12),
        (8, "Weekly seller newsletter", 3),
    ]
    now = int(time.time())
    for uid, (account_index, subject, minutes_ago) in enumerate(cases, 1):
        account = accounts[account_index]
        demo_html = ("<!doctype html><html><body style='margin:0;background:#eef2f6'>"
                     "<table width='100%' cellpadding='0' cellspacing='0' style='padding:24px'>"
                     "<tr><td align='center'><table width='560' cellpadding='18' "
                     "style='background:#fff;border:1px solid #dce4eb;border-radius:8px'>"
                     "<tr><td style='font:700 22px Arial;color:#082548'>账号通知</td></tr>"
                     "<tr><td style='font:15px/1.6 Arial;color:#34445b'>"
                     "Your seller account is at risk of deactivation. "
                     "请登录卖家后台核实通知和处理期限。</td></tr>"
                     "<tr><td><img src='https://example.invalid/demo-logo.png' "
                     "alt='演示外部图片' width='110'></td></tr>"
                     "</table></td></tr></table></body></html>") if uid == 1 else None
        mail = FetchedMail(
            uid=uid, message_id=f"demo-{uid}@amazon.com", subject=subject,
            from_name="Amazon", from_addr="notice@amazon.com",
            to_addr=account.email, internal_date=None,
            received_ts=now - minutes_ago * 60, size_bytes=1150,
            unread=True,
            body_text=(subject + "\n此邮件仅用于演示。实际处置须登录卖家后台核实。"),
            body_html=demo_html, preview=subject,
        )
        category, priority = classify(subject, mail.from_addr, mail.preview)
        amount = extract_amount(subject, mail.body_text) if category == "回款" else None
        service.store.upsert_message(account.id, mail, ClassifiedMail(
            True, category, priority, amount))
        service.assess_synced_message(account.id, uid)
    # 监控中断是数据源状态，不假装为一封邮件。
    service.store.set_sync_state(accounts[-1].id, "auth_error", "演示：授权失败")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8791)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="store-mail-dashboard-demo-") as tmp:
        service = Service.build(Settings(
            host="127.0.0.1", port=args.port, secret_key=generate_master_key(),
            admin_user="demo", admin_password="demo-local-only",
            mark_seen_on_open=False,
            db_path=Path(tmp) / "demo.sqlite3"))
        populate(service)
        project = Path(__file__).resolve().parents[1]
        server = start_http_server(
            "127.0.0.1", args.port, service,
            project / "src/store_mail_web/static", project / "openapi.yaml")
        stopped = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stopped.set())
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
        print(f"演示入口 http://127.0.0.1:{args.port}  账号 demo / 密码 demo-local-only")
        try:
            stopped.wait()
        finally:
            server.shutdown()
            server.server_close()
            service.store.close()


if __name__ == "__main__":
    main()
