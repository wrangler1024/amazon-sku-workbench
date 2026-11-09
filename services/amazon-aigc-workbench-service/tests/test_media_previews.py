import hashlib
import io
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
from psycopg.types.json import Jsonb

from amazon_aigc.api import create_app
from amazon_aigc.auth import new_session
from amazon_aigc.config import Settings
from amazon_aigc.media_previews import image_key, source_images


def fixture(db, setup, tmp_path):
    users, item, _ = setup
    with db.connection() as c:
        tokens = {role: new_session(c, user["id"]) for role, user in users.items()}
    app = TestClient(create_app(Settings(database_url="postgresql://unused/test", storage_root=tmp_path), db))

    def call(method, url, role="operator", **kw):
        headers = {"Authorization": "Bearer " + tokens[role], **kw.pop("headers", {})}
        return app.request(method, url, headers=headers, **kw)
    return call, item


def image_bytes():
    out = io.BytesIO()
    Image.new("RGB", (1800, 1200), "green").save(out, "JPEG", quality=95)
    return out.getvalue()


def test_thumbnails_are_small_cached_authorized_and_preserve_download(db, setup, tmp_path):
    call, item = fixture(db, setup, tmp_path)
    original = image_bytes()
    uploaded = call("POST", f"/api/items/{item}/assets/upload", files={"file": ("original.jpg", original, "image/jpeg")})
    aid = uploaded.json()["id"]
    response = call("GET", f"/api/assets/{aid}?size=thumbnail")
    assert response.status_code == 200 and response.headers["content-type"] == "image/webp"
    assert len(response.content) < len(original) / 5
    with Image.open(io.BytesIO(response.content)) as im:
        assert im.size == (192, 128)
    assert response.headers["cache-control"].startswith("private")
    conditional = {"If-None-Match": response.headers["etag"]}
    assert call("GET", f"/api/assets/{aid}?size=thumbnail", headers=conditional).status_code == 304
    assert call("GET", f"/api/assets/{aid}?size=thumbnail", "other", headers=conditional).status_code == 404
    assert call("GET", f"/api/assets/{aid}?size=thumbnail&download=true").content == original
    assert call("GET", f"/api/assets/{aid}").content == original
    assert call("GET", f"/api/assets/{aid}?size=unbounded").status_code == 422
    with db.connection() as c:
        a = c.execute("SELECT * FROM assets WHERE id=%s", (aid,)).fetchone()
        assert a["sha256"] == hashlib.sha256(original).hexdigest()


def test_source_images_only_from_visible_snapshot_and_fetch_once(db, setup, tmp_path, monkeypatch):
    from amazon_aigc import media_previews
    call, item = fixture(db, setup, tmp_path)
    url = "https://cbu01.alicdn.com/img/example.jpg"
    snapshot = uuid4()
    with db.connection() as c:
        c.execute("INSERT INTO source_snapshots(id,work_item_id,source_id,url,document_revision,method,status,body,parser_version) VALUES(%s,%s,'supplier-1','https://detail.1688.com/offer/123.html',0,'tmapi','success',%s,'test')",
                  (snapshot, item, Jsonb({"variants": [{"images": [url]}]})))
    calls = []

    async def fetch(url, **kw):
        calls.append((url, kw))
        return image_bytes(), url, "image/jpeg"
    monkeypatch.setattr(media_previews, "fetch_bytes", fetch)
    data = call("GET", f"/api/items/{item}").json()
    path = data["sources"][0]["image_previews"][url]["thumbnail"]
    assert call("GET", path, "other").status_code == 404
    assert call("GET", f"/api/source-images/{snapshot}/{image_key('https://127.0.0.1')}").status_code == 404
    assert not calls
    r = call("GET", path)
    assert r.status_code == 200
    assert call("GET", path, headers={"If-None-Match": r.headers["etag"]}).status_code == 304
    assert call("GET", path.replace("thumbnail", "preview")).status_code == 200
    assert calls == [(url, {"resource": True, "limit": 20_000_000})]
    assert call("GET", path, "other").status_code == 404  # cache never bypasses authorization


def test_snapshot_url_validation_excludes_arbitrary_hosts_and_private_targets():
    body = {"images": ["http://cbu01.alicdn.com/x", "https://127.0.0.1/x", "https://attacker.invalid/x", "https://u:p@cbu01.alicdn.com/x"],
            "variants": [{"images": ["https://cbu01.alicdn.com/valid.jpg"]}]}
    assert source_images(body) == ["https://cbu01.alicdn.com/valid.jpg"]
