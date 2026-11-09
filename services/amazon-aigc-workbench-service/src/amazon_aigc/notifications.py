"""Transactional Feishu collaboration notices, with independent delivery and retry."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from psycopg.types.json import Jsonb

from .config import TEST_BASE, TEST_TABLE, valid_target

WORKBENCH_URL = f"https://example.feishu.cn/base/{TEST_BASE}?table={TEST_TABLE}&view=vewExampleTest"
TITLES = {
    "artwork.publish": "新图需待认领或分配",
    "artwork.claim": "制图任务已被认领",
    "artwork.assign": "制图任务已指派",
    "artwork.return": "制图任务退回公共池",
    "artwork.withdraw": "制图需求已撤回",
    "artwork.deliver": "图稿已交付，请验收",
    "artwork.review": "图稿验收结果",
    "artwork.cancel-review": "图稿验收已取消",
    "notification.test": "协作通知测试",
}


def enabled(c):
    return c.execute("SELECT enabled FROM notification_config WHERE singleton=true").fetchone()["enabled"]


def enqueue_event(c, event_id, item_id, actor, action, detail):
    if action not in TITLES or not item_id or not enabled(c):
        return
    item = c.execute("SELECT * FROM work_items WHERE id=%s", (item_id,)).fetchone()
    binding = c.execute(
        "SELECT * FROM external_bindings WHERE work_item_id=%s AND provider='feishu'", (item_id,)
    ).fetchone()
    if not binding or not valid_target(binding["environment"], binding["base_id"], binding["table_id"]):
        return
    task = c.execute(
        "SELECT t.* FROM artwork_tasks t JOIN content_documents d ON d.id=t.document_id "
        "WHERE d.work_item_id=%s ORDER BY t.batch DESC LIMIT 1",
        (item_id,),
    ).fetchone()
    if not task:
        return
    leads = [
        r["id"]
        for r in c.execute("SELECT id FROM users WHERE 'art_lead'=ANY(roles) AND active AND internal").fetchall()
    ]
    recipients = {
        "artwork.publish": leads,
        "artwork.claim": [item["owner_id"]],
        "artwork.assign": [item["owner_id"], task["assignee_id"]],
        "artwork.return": [item["owner_id"], *leads],
        "artwork.withdraw": [task["assignee_id"], *leads],
        "artwork.deliver": [item["owner_id"]],
        "artwork.review": [task["assignee_id"]],
        "artwork.cancel-review": [task["assignee_id"]],
    }.get(action, [])
    maker = c.execute("SELECT name FROM users WHERE id=%s", (task["assignee_id"],)).fetchone()
    result = detail.get("result")
    title = TITLES[action] if action != "artwork.review" else "图稿验收通过" if result == "accepted" else "图稿需返修"
    payload = {
        "title": title,
        "code": item["code"],
        "name": item["name"],
        "site": item["site"],
        "group": item["group_name"],
        "actor": actor["name"],
        "maker": maker["name"] if maker else "",
        "batch": task["batch"],
        "delivery_id": str(task["current_delivery_id"] or ""),
        "state": task["state"],
        "assignee_id": str(task["assignee_id"] or ""),
        "note": str(detail.get("note") or detail.get("reason") or "")[:1200],
        "result": result,
    }
    if action == "artwork.review" and detail.get("image_notes"):
        payload["note"] = (payload["note"] + "\n含逐图返修意见，请在工作台查看具体图片。").strip()
    for recipient in set(recipients) - {None, actor["id"]}:
        c.execute(
            "INSERT INTO notification_outbox(id,event_id,work_item_id,task_id,recipient_id,action,payload) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(event_id,recipient_id) DO NOTHING",
            (uuid4(), event_id, item_id, task["id"], recipient, action, Jsonb(payload)),
        )


def content(payload, settings=None):
    lines = [
        f"商品：{payload.get('code', '')} {payload.get('name', '')}".strip(),
        f"站点 / 小组：{payload.get('site', '')} / {payload.get('group', '')}",
        f"制作批次：{payload.get('batch', '—')} · 操作人：{payload.get('actor', '系统')}",
    ]
    if payload.get("maker"):
        lines.append("执行美工：" + payload["maker"])
    if payload.get("note"):
        lines.append(payload["note"])
    if payload.get("test"):
        lines = ["这是一条协作通知测试，用于验证消息通道。", "未改变任何商品状态。"]
    return {
        "zh_cn": {
            "title": ("【联调】" if not settings or settings.environment != "production" else "") + payload["title"],
            "content": [[{"tag": "text", "text": line}] for line in lines]
            + [[{"tag": "a", "text": "打开亚马逊编辑工作台", "href": (f"{settings.feishu_base_origin}/base/{settings.base_id}?table={settings.sku_table_id}&view={settings.base_view_id}" if settings else WORKBENCH_URL)}]],
        }
    }


def recipient_subject(c, row, tenant="example"):
    from .domain import can_read
    from .roster_sync import fresh

    user = c.execute("SELECT * FROM users WHERE id=%s", (row["recipient_id"],)).fetchone()
    if not user or not fresh(user) or not user["active"] or not user["internal"] or not user["verified_at"]:
        return None
    if (datetime.now(UTC) - user["verified_at"]).total_seconds() > 86400:
        return None
    if row["work_item_id"]:
        item = c.execute("SELECT * FROM work_items WHERE id=%s", (row["work_item_id"],)).fetchone()
        binding = c.execute(
            "SELECT * FROM external_bindings WHERE work_item_id=%s AND provider='feishu'", (row["work_item_id"],)
        ).fetchone()
        if (
            not item
            or not binding
            or not valid_target(binding["environment"], binding["base_id"], binding["table_id"])
            or not (can_read(c, user, item) or row["action"] == "artwork.withdraw" and "art_lead" in user["roles"])
        ):
            return None
    ident = c.execute(
        "SELECT subject FROM external_identities WHERE user_id=%s AND provider='feishu_union' AND tenant=%s",
        (user["id"], tenant),
    ).fetchone()
    return ident["subject"] if ident else None


def still_relevant(c, row):
    if row["action"] == "notification.test":
        return True
    task = c.execute("SELECT * FROM artwork_tasks WHERE id=%s", (row["task_id"],)).fetchone()
    if not task:
        return False
    action, p = row["action"], row["payload"]
    allowed = {
        "artwork.publish": ["pool"],
        "artwork.claim": ["waiting", "working"],
        "artwork.assign": ["waiting", "working"],
        "artwork.return": ["pool"],
        "artwork.withdraw": ["withdrawn"],
        "artwork.deliver": ["pending"],
        "artwork.review": ["accepted" if p.get("result") == "accepted" else "revision"],
        "artwork.cancel-review": ["pending"],
    }
    if task["state"] not in allowed.get(action, []):
        return False
    if action in ("artwork.assign", "artwork.claim") and p.get("assignee_id") != str(task["assignee_id"] or ""):
        return False
    return not p.get("delivery_id") or p["delivery_id"] == str(task["current_delivery_id"])


def send(gateway, subject, body, message_uuid):
    # Deliberately separate from the Base-pinned gateway.request method.
    if not valid_target(gateway.s.environment, gateway.s.base_id, gateway.s.sku_table_id) or gateway.s.feishu_mode != "openapi":
        raise RuntimeError("Notification transport requires a configured OpenAPI target")
    response = httpx.post(
        "https://open.feishu.cn/open-apis/im/v1/messages",
        params={"receive_id_type": "union_id"},
        headers={"Authorization": "Bearer " + gateway.access_token()},
        json={
            "receive_id": subject,
            "msg_type": "post",
            "content": json.dumps(body, ensure_ascii=False),
            "uuid": str(message_uuid),
        },
        timeout=20,
    )
    response.raise_for_status()
    result = response.json()
    if result.get("code") != 0:
        raise RuntimeError("Feishu message rejected: " + str(result.get("code")))
    return result["data"]["message_id"]


def run_notification(db, gateway, sender=send):
    token = uuid4()
    with db.connection() as c:
        if not enabled(c):
            return False
        row = c.execute(
            "SELECT * FROM notification_outbox WHERE ((state IN ('queued','failed') AND run_after<=now()) "
            "OR (state='sending' AND lease_until<now())) AND attempts<5 ORDER BY created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED"
        ).fetchone()
        if not row:
            return False
        # Ambiguous network failures are retried only inside Feishu's deduplication window.
        if row["attempts"] and (datetime.now(UTC) - row["created_at"]).total_seconds() > 3300:
            c.execute(
                "UPDATE notification_outbox SET state='failed',attempts=5,error='重试窗口已过，请核对发送记录后处理' WHERE id=%s",
                (row["id"],),
            )
            return True
        if row["work_item_id"] and gateway:
            binding = c.execute("SELECT * FROM external_bindings WHERE work_item_id=%s AND provider='feishu'", (row["work_item_id"],)).fetchone()
            if not binding or (binding["base_id"], binding["table_id"]) != (gateway.s.base_id, gateway.s.sku_table_id):
                raise RuntimeError("Notification environment mismatch")
        subject = recipient_subject(c, row, getattr(getattr(gateway, "s", None), "tenant", "example"))
        if not subject or not still_relevant(c, row):
            c.execute(
                "UPDATE notification_outbox SET state='skipped',error='接收人权限或任务状态已变化',finished_at=now() WHERE id=%s",
                (row["id"],),
            )
            return True
        c.execute(
            "UPDATE notification_outbox SET state='sending',attempts=attempts+1,lease_token=%s,lease_until=now()+interval '90 seconds' WHERE id=%s",
            (token, row["id"]),
        )
    try:
        message_id = sender(gateway, subject, content(row["payload"], gateway.s if gateway else None), row["id"])
        with db.connection() as c:
            c.execute(
                "UPDATE notification_outbox SET state='sent',message_id=%s,error=NULL,finished_at=now(),lease_until=NULL WHERE id=%s AND lease_token=%s",
                (message_id, row["id"], token),
            )
    except Exception as exc:  # noqa: BLE001 -- an IM outage never rolls back business work
        error = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        with db.connection() as c:
            c.execute(
                "UPDATE notification_outbox SET state='failed',error=%s,run_after=now()+make_interval(secs=>least(600,30*power(2,attempts)::int)),lease_until=NULL WHERE id=%s AND lease_token=%s",
                (error[:250], row["id"], token),
            )
    return True


def status(c):
    return {
        "enabled": enabled(c),
        "counts": {
            r["state"]: r["count"]
            for r in c.execute("SELECT state,count(*) FROM notification_outbox GROUP BY state").fetchall()
        },
        "events": c.execute(
            "SELECT n.id,n.action,n.state,n.attempts,n.error,n.created_at,n.finished_at,n.message_id,u.name AS recipient_name,w.code FROM notification_outbox n JOIN users u ON u.id=n.recipient_id LEFT JOIN work_items w ON w.id=n.work_item_id ORDER BY n.created_at DESC LIMIT 50"
        ).fetchall(),
    }


def test_notice(c, user):
    from .domain import fail

    if not enabled(c):
        fail("请先启用协作通知")
    ident = uuid4()
    c.execute(
        "INSERT INTO notification_outbox(id,recipient_id,action,payload) VALUES(%s,%s,%s,%s)",
        (ident, user["id"], "notification.test", Jsonb({"title": TITLES["notification.test"], "test": True})),
    )
    return {"id": str(ident), "recipient": user["name"]}
