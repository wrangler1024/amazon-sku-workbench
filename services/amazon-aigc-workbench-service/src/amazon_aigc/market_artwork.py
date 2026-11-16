"""One SKU task, complete ordered deliveries and reviews for each target market."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from .domain import active_artist, detail, event, fail, get_item, project, serial
from .market_editor import activate_pending

CHILD_ACTIONS = {"save-delivery", "deliver", "save-review", "review", "cancel-review"}


def _assets(c, user, task, value, *, expected=None):
    if not isinstance(value, list) or len(value) > 12 or len(set(map(str, value))) != len(value):
        fail("交付图片列表无效或存在重复图片", 422)
    if expected is not None and len(value) != expected:
        fail("请交付本站完整" + str(expected) + "张套图，主图排在首位", 422)
    try:
        ids = [str(UUID(str(a))) for a in value]
    except (ValueError, TypeError):
        fail("图片编号无效", 422)
    rows = c.execute("SELECT * FROM assets WHERE id=ANY(%s::uuid[]) AND work_item_id=%s", (ids, task["work_item_id"])).fetchall() if ids else []
    if len(rows) != len(ids) or len({a["sha256"] for a in rows}) != len(ids) or any(not a["mime"].startswith("image/") for a in rows):
        fail("存在无权限、重复或无效图片", 422)
    for asset in rows:
        if str(asset["uploaded_by"]) == str(user["id"]):
            continue
        reused = c.execute("SELECT 1 FROM artwork_deliveries d JOIN artwork_tasks t ON t.id=d.task_id WHERE t.document_id=%s AND %s=ANY(d.asset_ids)", (task["document_id"], asset["id"])).fetchone()
        if not reused:
            fail("只能使用本人上传或本商品先前交付的原图", 403)
    return ids


def _aggregate(c, task, item):
    rows = c.execute("SELECT * FROM artwork_task_markets WHERE task_id=%s", (task["id"],)).fetchall()
    states = {r["state"] for r in rows}
    state = "accepted" if states == {"accepted"} else next(s for s in ("pending", "revision", "working", "waiting") if s in states)
    primary = next((r["current_delivery_id"] for r in rows if r["site"] == item["site"]), None)
    first = "rejected" if any(r["first_review_result"] == "rejected" for r in rows) else "accepted" if all(r["first_review_result"] == "accepted" for r in rows) else None
    c.execute("UPDATE artwork_tasks SET state=%s,current_delivery_id=%s,first_review_result=%s,revision=revision+1 WHERE id=%s", (state, primary, first, task["id"]))
    return state


def command(c, user, task, item, action, revision, data):
    site = data.get("site")
    if site not in task["market_sites"]:
        fail("请选择当前任务中的交付站点", 422)
    child = c.execute("SELECT * FROM artwork_task_markets WHERE task_id=%s AND site=%s FOR UPDATE", (task["id"], site)).fetchone()
    if data.get("market_revision") is not None:
        if data["market_revision"] != child["revision"]:
            fail("本站交付已更新，请重新读取")
    elif revision != task["revision"]:
        fail("任务已更新，请重新读取")
    owned = str(task["assignee_id"]) == str(user["id"])
    current = c.execute("SELECT * FROM artwork_deliveries WHERE id=%s", (child["current_delivery_id"],)).fetchone() if child["current_delivery_id"] else None
    delivery_id = child["current_delivery_id"]
    if action in ("save-delivery", "deliver"):
        if not owned or not active_artist(user):
            fail("仅在职执行美工可保存或交付", 403)
        if child["state"] not in ("working", "revision") or task["state"] in ("pool", "waiting", "withdrawn"):
            fail("请先开始制作，或从本站返修重新交付")
        brief = c.execute("SELECT body FROM content_versions WHERE id=%s", (task["brief_version"],)).fetchone()["body"]
        expected = len(brief.get("plans") or []) or brief.get("image_count", 7)
        ids = _assets(c, user, task, data.get("asset_ids", [str(x) for x in child["draft_asset_ids"]]), expected=expected if action == "deliver" else None)
        note = data.get("note", child["draft_note"])
        if not isinstance(note, str) or len(note) > 4000:
            fail("交付说明最多4000字", 422)
        if action == "save-delivery":
            c.execute("UPDATE artwork_task_markets SET draft_asset_ids=%s,draft_note=%s WHERE task_id=%s AND site=%s", (ids, note, task["id"], site))
        else:
            delivery_id = uuid4()
            number = c.execute("SELECT coalesce(max(version),0)+1 AS n FROM artwork_deliveries WHERE task_id=%s", (task["id"],)).fetchone()["n"]
            market_version = c.execute("SELECT coalesce(max(market_version),0)+1 AS n FROM artwork_deliveries WHERE task_id=%s AND marketplace=%s", (task["id"], site)).fetchone()["n"]
            c.execute("INSERT INTO artwork_deliveries(id,task_id,version,marketplace,market_version,asset_ids,note,delivered_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)", (delivery_id, task["id"], number, site, market_version, ids, note, user["id"]))
            c.execute("UPDATE artwork_task_markets SET state='pending',current_delivery_id=%s,draft_asset_ids='{}',draft_note='' WHERE task_id=%s AND site=%s", (delivery_id, task["id"], site))
    else:
        if not set(user["roles"]) & {"admin", "operator", "supervisor"}:
            fail("验收操作由内容负责人或管理员处理", 403)
        get_item(c, user, task["work_item_id"], True)
        if owned:
            fail("不能验收本人制作的图片", 403)
        if not current or str(data.get("delivery_id", current["id"])) != str(current["id"]):
            fail("本站交付版本已更新，请重新读取")
        if action == "cancel-review":
            if child["state"] != "accepted" or current["result"] != "accepted":
                fail("只能取消本站当前已验收版本")
            if c.execute("SELECT 1 FROM artwork_tasks WHERE document_id=%s AND batch>%s", (task["document_id"], task["batch"])).fetchone():
                fail("已有后续制作批次，不能取消历史验收")
            canceled = {k: current[k] for k in ("result", "review_note", "image_notes", "reviewed_by", "reviewed_at")}
            canceled.update(canceled_by=user["id"], canceled_at=datetime.now(UTC))
            c.execute("UPDATE artwork_deliveries SET result=NULL,reviewed_by=NULL,reviewed_at=NULL,canceled_reviews=canceled_reviews||%s WHERE id=%s", (Jsonb([serial(canceled)]), current["id"]))
            c.execute("UPDATE image_receipts SET invalidated_at=now() WHERE delivery_id=%s AND invalidated_at IS NULL", (current["id"],))
            first = c.execute("SELECT result FROM artwork_deliveries WHERE task_id=%s AND marketplace=%s AND result IS NOT NULL ORDER BY reviewed_at,market_version LIMIT 1", (task["id"], site)).fetchone()
            c.execute("UPDATE artwork_task_markets SET state='pending',first_review_result=%s WHERE task_id=%s AND site=%s", (first["result"] if first else None, task["id"], site))
        else:
            if child["state"] != "pending":
                fail("仅本站待验收图稿可填写意见或验收")
            notes = data.get("image_notes", current["image_notes"])
            note = data.get("note", current["review_note"] or "")
            if not isinstance(notes, dict) or any(str(k) not in {str(x) for x in current["asset_ids"]} or not isinstance(v, str) or len(v) > 4000 for k, v in notes.items()) or not isinstance(note, str) or len(note) > 4000:
                fail("返修意见只能关联本站当前交付图片，每项最多4000字", 422)
            notes = {str(k): v.strip() for k, v in notes.items() if v.strip()}
            c.execute("UPDATE artwork_deliveries SET image_notes=%s,review_note=%s WHERE id=%s", (Jsonb(notes), note.strip(), current["id"]))
            if action == "review":
                result = data.get("result")
                if result not in ("accepted", "rejected") or result == "rejected" and not note.strip() and not notes:
                    fail("请选择验收结果；返修时须填写意见", 422)
                c.execute("UPDATE artwork_deliveries SET result=%s,reviewed_by=%s,reviewed_at=now() WHERE id=%s", (result, user["id"], current["id"]))
                c.execute("UPDATE artwork_task_markets SET state=%s,first_review_result=coalesce(first_review_result,%s) WHERE task_id=%s AND site=%s", ("accepted" if result == "accepted" else "revision", result, task["id"], site))
    c.execute("UPDATE artwork_task_markets SET revision=revision+1 WHERE task_id=%s AND site=%s", (task["id"], site))
    state = _aggregate(c, task, item)
    event(c, item["id"], user, "artwork." + action, {**data, "site": site, "task_id": task["id"], "delivery_id": delivery_id})
    if state == "accepted":
        activate_pending(c, task["document_id"])
    if action not in ("save-review", "save-delivery"):
        project(c, item["id"])
    return detail(c, user, item["id"])
