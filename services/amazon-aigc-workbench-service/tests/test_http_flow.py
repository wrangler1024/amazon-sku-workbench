import hashlib
import io
import zipfile
from urllib.parse import unquote
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
from psycopg.types.json import Jsonb
from test_workflow import BRIEF, FACTS, LISTING

from amazon_aigc.api import create_app
from amazon_aigc.auth import new_session
from amazon_aigc.config import Settings


def test_http_early_publication_delivery_and_download(db, setup, tmp_path):
    users, item, _ = setup
    settings = Settings(
        database_url="postgresql://unused/test", storage_root=tmp_path / "assets", web_root=tmp_path / "web"
    )
    settings.storage_root.mkdir()
    client = TestClient(create_app(settings, db))
    headers = {}
    with db.connection() as c:
        c.execute("UPDATE work_items SET review_snapshot=%s WHERE id=%s", (Jsonb({"记录序号": "R-1046"}), item))
        c.execute("UPDATE users SET name='示例美工负责人' WHERE id=%s", (users["artist"]["id"],))
        for role in ["operator", "artist", "other"]:
            headers[role] = {"Authorization": "Bearer " + new_session(c, users[role]["id"])}

    def request(method, path, body=None, role="operator", **kwargs):
        result = client.request(
            method, path, headers={**headers[role], "Idempotency-Key": str(uuid4())}, json=body, **kwargs
        )
        assert result.status_code == 200, result.text
        return result

    def read():
        return request("GET", f"/api/items/{item}").json()

    def document(section, body):
        return request(
            "PUT",
            f"/api/items/{item}/document",
            {"revision": read()["document"]["revision"], "section": section, "body": body, "confirm": True},
        ).json()

    document("facts", FACTS)
    document("listing", LISTING)
    published = request(
        "POST",
        f"/api/items/{item}/publication",
        {
            "revision": read()["document"]["revision"],
            "data": {"store": "SYNTHETIC", "msku": "IT-HTTP", "receipt": "合成验证"},
        },
    ).json()
    assert published["publications"] and not published["tasks"]
    document("brief", BRIEF)
    request("POST", f"/api/items/{item}/publish-brief", {"revision": read()["document"]["revision"], "data": {}})

    def action(name, role, body=None):
        task = read()["tasks"][0]
        return request(
            "POST", f"/api/artwork/{task['id']}/{name}", {"revision": task["revision"], "data": body or {}}, role
        ).json()

    action("claim", "artist")
    action("start", "artist")
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "green").save(buf, "PNG")
    original = buf.getvalue()
    uploaded = request(
        "POST",
        f"/api/items/{item}/assets/upload",
        role="artist",
        files={"file": ("original.png", original, "image/png")},
    ).json()
    delivered = action("deliver", "artist", {"asset_ids": [uploaded["id"]]})
    delivery = delivered["deliveries"][0]["id"]
    assert client.get(f"/api/deliveries/{delivery}/download", headers=headers["operator"]).status_code == 409
    assert client.get(f"/api/deliveries/{delivery}/download-info", headers=headers["operator"]).status_code == 409
    action("review", "operator", {"result": "accepted", "note": "合成验收"})
    info = request("GET", f"/api/deliveries/{delivery}/download-info").json()
    assert info["folder"] == "R-1046_US_示例美工负责人_四件收纳盘_1_1"
    response = request("GET", f"/api/deliveries/{delivery}/download")
    assert unquote(response.headers["Content-Disposition"]).endswith(info["folder"] + ".zip")
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        assert len(z.namelist()) == 1 and "/" in z.namelist()[0]
        assert z.namelist()[0] == info["folder"] + "/01_original.png"
        assert hashlib.sha256(z.read(z.namelist()[0])).digest() == hashlib.sha256(original).digest()
    # Renaming the draft or reassigning future work cannot rename this batch's product or deliverer.
    with db.connection() as c:
        c.execute("UPDATE work_items SET name='后续商品名称' WHERE id=%s", (item,))
        c.execute("UPDATE artwork_tasks SET assignee_id=%s WHERE id=%s", (users["other"]["id"], read()["tasks"][0]["id"]))
    assert request("GET", f"/api/deliveries/{delivery}/download-info").json() == info
    response = request("GET", f"/api/deliveries/{delivery}/download", params={"short_name": "../透明/收纳_盘"})
    folder = "R-1046_US_示例美工负责人_透明-收纳-盘_1_1"
    assert unquote(response.headers["Content-Disposition"]).endswith(folder + ".zip")
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        assert z.namelist() == [folder + "/01_original.png"]
        assert z.read(z.namelist()[0]) == original
    for endpoint in ["download", "download-info"]:
        assert client.get(f"/api/deliveries/{delivery}/{endpoint}", headers=headers["other"]).status_code == 404
        assert client.get(f"/api/deliveries/{delivery}/{endpoint}").status_code == 401
    assert request("GET", f"/api/deliveries/{delivery}/download-info").json() == info
    assert client.get(f"/api/assets/{uploaded['id']}", headers=headers["other"]).status_code == 404
    request(
        "POST",
        f"/api/items/{item}/image-receipt",
        {
            "revision": read()["document"]["revision"],
            "data": {
                "publication_id": published["publications"][0]["id"],
                "delivery_id": delivery,
                "receipt": "合成补图",
            },
        },
    )
    assert read()["image_receipts"]
