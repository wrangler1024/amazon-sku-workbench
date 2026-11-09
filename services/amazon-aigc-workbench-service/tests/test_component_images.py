import io
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from PIL import Image
from test_model_failures import model_settings
from test_workflow import FACTS

from amazon_aigc.api import create_app
from amazon_aigc.auth import new_session
from amazon_aigc.config import Settings
from amazon_aigc.model_gateway import generate
from amazon_aigc.sync import projection


def test_manual_component_photo_survives_confirmation_and_seven_image_delivery(db, setup, tmp_path):
    users, item, _ = setup
    client = TestClient(create_app(Settings(database_url="postgresql://unused/test", storage_root=tmp_path), db))
    with db.connection() as c:
        sessions = {role: new_session(c, user["id"]) for role, user in users.items()}

    def req(method, path, role="operator", **kwargs):
        return client.request(
            method,
            path,
            headers={"Authorization": "Bearer " + sessions[role], "Idempotency-Key": str(uuid4())},
            **kwargs,
        )

    def read():
        return req("GET", f"/api/items/{item}").json()

    def save(facts, confirm=False):
        return req(
            "PUT",
            f"/api/items/{item}/document",
            json={"revision": read()["document"]["revision"], "section": "facts", "body": facts, "confirm": confirm},
        )

    def photo(index=0, role="operator"):
        image = io.BytesIO()
        Image.new("RGB", (32, 32), (index * 20, 100, 70)).save(image, "PNG")
        result = req(
            "POST",
            f"/api/items/{item}/assets/upload",
            role,
            files={"file": (f"part-{index}.png", image.getvalue(), "image/png")},
        )
        assert result.status_code == 200, result.text
        return result.json()["id"]

    row = {"id": "manual", "name": "手动配件", "specification": "黑色", "quantity": 1, "unit": "件", "confirmed": True}
    facts = {**FACTS, "components": [row]}
    assert save(facts).status_code == 200
    assert save(facts, True).status_code == 200
    row["image_asset_ids"] = [str(uuid4())]
    assert save(facts).status_code == 422
    asset = photo()
    row["image_asset_ids"] = [asset, asset]
    assert "不能重复" in save(facts).text
    row["image_asset_ids"] = [asset]
    assert save(facts).status_code == 200
    result = save(facts, True)
    assert result.status_code == 200, result.text
    assert result.json()["document"]["body"]["facts"]["components"][0]["image_asset_ids"] == [asset]
    assert req("GET", f"/api/assets/{asset}", "other").status_code == 404
    # A valid image belonging to another work item cannot be attached.
    with db.connection() as c:
        other_item = uuid4()
        c.execute(
            "INSERT INTO work_items(id,environment,site,code,name,owner_id,group_name,review_status) VALUES(%s,'test','US',%s,'其他商品',%s,'一部二组','审核通过')",
            (other_item, str(other_item), users["other"]["id"]),
        )
        c.execute("UPDATE assets SET work_item_id=%s WHERE id=%s", (other_item, asset))
    assert save(facts).status_code == 422
    with db.connection() as c:
        c.execute("UPDATE assets SET work_item_id=%s WHERE id=%s", (item, asset))

    brief = {"product": "配件", "selling_points": "实际包含物", "visual_focus": "颜色和数量", "image_count": 7}
    result = req(
        "PUT",
        f"/api/items/{item}/document",
        json={"revision": read()["document"]["revision"], "section": "brief", "body": brief, "confirm": True},
    )
    assert result.status_code == 200, result.text
    result = req(
        "POST",
        f"/api/items/{item}/publish-brief",
        json={"revision": read()["document"]["revision"], "data": {"urgent": True}},
    )
    assert result.status_code == 200, result.text

    def action(name, role, data=None):
        task = read()["tasks"][0]
        return req(
            "POST", f"/api/artwork/{task['id']}/{name}", role, json={"revision": task["revision"], "data": data or {}}
        )

    with db.connection() as c:
        assert projection(c, item, SimpleNamespace(s=SimpleNamespace(feishu_mode="openapi")))["计划制图数量"] == 7
    assert action("claim", "artist").status_code == 200
    assert action("start", "artist").status_code == 200
    assert req("GET", f"/api/assets/{asset}", "artist").status_code == 200
    ids = [photo(i, "artist") for i in range(1, 8)]
    assert action("deliver", "artist", {"asset_ids": []}).status_code == 422
    assert action("deliver", "artist", {"asset_ids": ids}).status_code == 200
    assert action("review", "operator", {"result": "accepted"}).status_code == 200


def test_new_brief_generation_returns_complete_planning(monkeypatch):
    candidate = {
        "product": "本品",
        "selling_points": "核实卖点",
        "visual_focus": "实际件数",
        "image_count": 7,
        "plans": [
            {"purpose": "主图" if i == 0 else f"副图{i}", "composition": "展示确认的本品数量及用途", "text": ""}
            for i in range(7)
        ],
    }
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **kw: httpx.Response(
            200, json={"output": [{"content": [{"type": "output_text", "text": json.dumps(candidate)}]}]}
        ),
    )
    result = generate(model_settings(), "deepseek", "brief", {})
    assert result["candidate"]["image_count"] == 7
    assert result["candidate"]["plans"] == candidate["plans"]
