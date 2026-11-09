import hashlib
import io
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from psycopg.types.json import Jsonb

from amazon_aigc.config import TEST_BASE, TEST_TABLE, Settings
from amazon_aigc.feishu import FeishuGateway
from amazon_aigc.product_images import PREFIX, _retry_after, cache_product_images
from amazon_aigc.sync import import_records
from amazon_aigc.workbench_views import item_rows


def png():
    stream = io.BytesIO()
    Image.new("RGB", (40, 30), "green").save(stream, format="PNG")
    return stream.getvalue()


class Gateway:
    def __init__(self, root, rows=()):
        self.s = SimpleNamespace(
            storage_root=root, sku_table_id=TEST_TABLE, base_id=TEST_BASE, tenant="thumbnail-test", environment="test"
        )
        self.rows = rows
        self.downloads = []
        self.fail = False

    def records(self, _):
        return self.rows

    def product_image(self, attachment):
        self.downloads.append(attachment["file_token"])
        if self.fail:
            raise RuntimeError("optional download unavailable")
        return png()


def set_attachment(db, item, token):
    attachments = [{"file_token": token, "name": "product.png"}] if token else []
    with db.connection() as c:
        c.execute("UPDATE work_items SET review_snapshot=%s WHERE id=%s", (Jsonb({"产品图片": attachments}), item))
    return attachments


def test_import_caches_review_image_without_touching_draft(db, setup, tmp_path):
    users, item, doc = setup
    user = users["operator"]
    subject, record = "user-" + uuid4().hex, "rec" + uuid4().hex
    with db.connection() as c:
        c.execute(
            "INSERT INTO external_identities(provider,tenant,subject,user_id) VALUES('feishu','thumbnail-test',%s,%s)",
            (subject, user["id"]),
        )
        c.execute(
            "INSERT INTO external_bindings(provider,tenant,environment,base_id,table_id,record_id,work_item_id) VALUES('feishu','thumbnail-test','test',%s,%s,%s,%s)",
            (TEST_BASE, TEST_TABLE, record, item),
        )
        c.execute(
            "UPDATE content_documents SET body=%s,revision=12 WHERE id=%s",
            (Jsonb({"facts": {"uses": "手填内容保留"}}), doc),
        )
    g = Gateway(
        tmp_path,
        [
            {
                "record_id": record,
                "中文品名": "已审核商品",
                "选品SKU": "TEST-PHOTO",
                "目标站点": "AU",
                "审核状态": "审核通过",
                "提交运营": [{"id": subject}],
                "产品图片": [{"file_token": "ProductImageToken001", "name": "product.png"}],
            }
        ],
    )
    assert import_records(db, g)["thumbnails"] == {"cached": 1, "failed": 0}
    assert import_records(db, g)["thumbnails"]["cached"] == 0
    assert len(g.downloads) == 1
    with db.connection() as c:
        d = c.execute("SELECT body,revision FROM content_documents WHERE id=%s", (doc,)).fetchone()
        assert d["revision"] == 12 and d["body"]["facts"]["uses"] == "手填内容保留"
        row = next(x for x in item_rows(c, user) if x["id"] == str(item))
        asset = c.execute("SELECT * FROM assets WHERE id=%s", (row["thumbnail_id"],)).fetchone()
        assert asset["sha256"] == hashlib.sha256(png()).hexdigest()
        assert (tmp_path / asset["object_key"]).read_bytes() == png()


def test_latest_base_image_wins_and_removed_image_never_leaks(db, setup, tmp_path):
    users, item, _ = setup
    g = Gateway(tmp_path)
    for token in ["FirstProductToken001", "NewProductToken002"]:
        images = set_attachment(db, item, token)
        cache_product_images(db, g, [(item, users["operator"]["id"], images)])
        with db.connection() as c:
            row = next(x for x in item_rows(c, users["operator"]) if x["id"] == str(item))
            assert (
                c.execute("SELECT source_url FROM assets WHERE id=%s", (row["thumbnail_id"],)).fetchone()["source_url"]
                == PREFIX + token
            )
    set_attachment(db, item, "")
    with db.connection() as c:
        row = next(x for x in item_rows(c, users["operator"]) if x["id"] == str(item))
        assert row["thumbnail_id"] is None
        assert c.execute("SELECT count(*) AS n FROM assets WHERE work_item_id=%s", (item,)).fetchone()["n"] == 2


def test_failed_thumbnail_is_optional_retried_and_missing_file_repaired(db, setup, tmp_path):
    users, item, _ = setup
    g = Gateway(tmp_path)
    token = "RetryProductToken001"
    images = set_attachment(db, item, token)
    tasks = [(item, users["operator"]["id"], images)]
    g.fail = True
    assert cache_product_images(db, g, tasks)["failed"] == 1
    assert cache_product_images(db, g, tasks)["failed"] == 0
    assert len(g.downloads) == 1
    _retry_after.pop((str(item), token))
    g.fail = False
    assert cache_product_images(db, g, tasks)["cached"] == 1
    with db.connection() as c:
        a = c.execute("SELECT * FROM assets WHERE work_item_id=%s", (item,)).fetchone()
    (tmp_path / a["object_key"]).unlink()
    assert cache_product_images(db, g, tasks)["cached"] == 1
    with db.connection() as c:
        assert c.execute("SELECT count(*) AS n FROM assets WHERE work_item_id=%s", (item,)).fetchone()["n"] == 1


def test_download_pins_origin_and_preserves_permission_context(monkeypatch):
    g = FeishuGateway(Settings(database_url="postgresql://localhost/test"))
    monkeypatch.setattr(g, "access_token", lambda: "test-only")
    seen = {}

    # Response has no context manager; use the ordinary client streaming wrapper around MockTransport.
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=png())))

    def bounded_stream(method, url, **kwargs):
        seen.update(method=method, url=url, **kwargs)
        return client.stream(method, url, **kwargs)

    monkeypatch.setattr(httpx, "stream", bounded_stream)
    extra = '{"bitablePerm":{"tableId":"' + TEST_TABLE + '","rev":112}}'
    from urllib.parse import urlencode

    image = {"file_token": "ProductImageToken001", "url": "https://untrusted.invalid/x?" + urlencode({"extra": extra})}
    assert g.product_image(image) == png()
    assert seen["url"] == "https://open.feishu.cn/open-apis/drive/v1/medias/ProductImageToken001/download"
    assert seen["params"]["extra"] == extra
    with pytest.raises(ValueError):
        g.product_image({"file_token": "../not-a-token"})
    with pytest.raises(ValueError):
        g.product_image(
            {
                **image,
                "url": "https://untrusted.invalid/?" + urlencode({"extra": '{"bitablePerm":{"tableId":"production"}}'}),
            }
        )
    client.close()


def test_download_rejects_oversized_stream(monkeypatch):
    g = FeishuGateway(Settings(database_url="postgresql://localhost/test"))
    monkeypatch.setattr(g, "access_token", lambda: "test-only")
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"x" * 20_000_001)))
    monkeypatch.setattr(httpx, "stream", client.stream)
    with pytest.raises(ValueError, match="20MB"):
        g.product_image({"file_token": "ProductImageToken001"})
    client.close()


def test_independent_image_queue_drains_batch_and_preserves_documents(db, setup, tmp_path):
    from amazon_aigc.product_images import cache_missing_product_images
    users, _, doc = setup
    env = 'image-worker-' + uuid4().hex
    g = Gateway(tmp_path)
    g.s.environment = env
    items = []
    with db.connection() as c:
        before = c.execute('SELECT * FROM content_documents WHERE id=%s', (doc,)).fetchone()
        for i in range(9):
            item = uuid4()
            c.execute("INSERT INTO work_items(id,environment,site,code,name,owner_id,group_name,review_status,review_snapshot) VALUES(%s,%s,'AU',%s,'image',%s,'一部一组','审核通过',%s)",
                      (item, env, str(item), users['operator']['id'], Jsonb({'产品图片':[{'file_token':f'BatchProductToken{i:03d}'}]})))
            items.append(item)
    assert cache_missing_product_images(db,g) == {'pending':9,'cached':9,'failed':0}
    assert cache_missing_product_images(db,g) == {'pending':0,'cached':0,'failed':0}
    with db.connection() as c:
        assert c.execute('SELECT * FROM content_documents WHERE id=%s', (doc,)).fetchone() == before
        asset=c.execute('SELECT object_key FROM assets WHERE work_item_id=%s',(items[0],)).fetchone()
    (tmp_path/asset['object_key']).unlink()
    assert cache_missing_product_images(db,g)['cached'] == 1


def test_thumbnail_api_authorization_and_image_arrival(db, setup, tmp_path):
    from fastapi.testclient import TestClient

    from amazon_aigc.api import create_app
    from amazon_aigc.auth import new_session
    users,item,doc=setup
    images=set_attachment(db,item,'MetadataProductToken001')
    with db.connection() as c:
        headers={k:{'Authorization':'Bearer '+new_session(c,u['id'])} for k,u in users.items()}
        before=c.execute('SELECT * FROM content_documents WHERE id=%s',(doc,)).fetchone()
    client=TestClient(create_app(Settings(database_url='postgresql://unused/test',storage_root=tmp_path),db))
    url='/api/workbench/thumbnails?item_id='+str(item)
    assert client.get(url).status_code==401
    assert client.get(url,headers=headers['other']).json()==[]
    assert client.get(url,headers=headers['artist']).json()==[]
    assert client.get(url,headers=headers['operator']).json()==[{'id':str(item),'thumbnail_id':None,'thumbnail_pending':True}]
    page=client.get('/api/workbench/items?tab=facts',headers=headers['operator']).json()
    assert next(x for x in page['items'] if x['id']==str(item))['thumbnail_pending']
    assert cache_product_images(db,Gateway(tmp_path),[(item,users['operator']['id'],images)])['cached']==1
    result=client.get(url,headers=headers['operator']).json()[0]
    assert result['thumbnail_id'] and not result['thumbnail_pending']
    assert client.get('/api/workbench/thumbnails?'+'&'.join(['item_id='+str(item)]*51),headers=headers['operator']).status_code==422
    assert client.get('/api/workbench/thumbnails?item_id=bad',headers=headers['operator']).status_code==422
    with db.connection() as c:
        assert c.execute('SELECT * FROM content_documents WHERE id=%s',(doc,)).fetchone()==before
