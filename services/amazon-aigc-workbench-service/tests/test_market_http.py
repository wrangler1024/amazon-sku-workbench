import hashlib
import io
import zipfile
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
from psycopg.types.json import Jsonb
from test_market_editor import BRIEF, TEXTS
from test_workflow import FACTS, LISTING

from amazon_aigc.api import create_app
from amazon_aigc.auth import new_session
from amazon_aigc.config import Settings


def test_http_five_markets_confirm_deliver_review_and_exact_download(db, setup, tmp_path):
    users, item, _ = setup
    with db.connection() as c:
        c.execute("UPDATE work_items SET site='ES',review_snapshot=%s WHERE id=%s", (Jsonb({"记录序号": "R-SYNTHETIC"}), item))
        tokens = {r: new_session(c,u["id"]) for r,u in users.items()}
    client = TestClient(create_app(Settings(database_url="postgresql://unused/test", storage_root=tmp_path, sync_enabled=False), db))
    prefix = f"/api/items/{item}"
    def read():
        return client.get(prefix, headers={"Authorization": "Bearer "+tokens["operator"]}).json()
    def post(path, body, role="operator", key=None):
        r = client.post(path, headers={"Authorization": "Bearer "+tokens[role], "Idempotency-Key": key or str(uuid4())}, json=body)
        assert r.status_code == 200, r.text
        return r.json()
    r = client.put(prefix+"/document", headers={"Authorization":"Bearer "+tokens["operator"],"Idempotency-Key":str(uuid4())}, json={"revision":0,"section":"facts","body":FACTS,"confirm":True})
    assert r.status_code == 200
    d = post(prefix+"/editor-mode", {"revision":read()["document"]["revision"],"data":{"mode":"pan_eu"}})
    sites = d["item"]["editor_targets"]
    d = post(prefix+"/market-confirm", {"revision":d["document"]["revision"],"data":{"kind":"listing","targets":sites,"listings":{s:LISTING for s in sites},"acknowledged":True}})
    payload={"revision":d["document"]["revision"],"data":{"kind":"brief","targets":sites,"shared_brief":BRIEF,"image_texts":TEXTS,"acknowledged":True}}
    key=str(uuid4());d=post(prefix+"/market-confirm",payload,key=key);assert post(prefix+"/market-confirm",payload,key=key)==d
    task=d["tasks"][0]["id"]
    def action(a, role="artist", **data):
        t=next(t for t in read()["tasks"] if t["id"]==task)
        if data.get("site"):
            m=next(m for m in read()["task_markets"] if m["site"]==data["site"] and m["task_id"]==task)
            data["market_revision"]=m["revision"]
        return post(f"/api/artwork/{task}/{a}",{"revision":t["revision"],"data":data},role)
    action("claim");action("start")
    ids=[];original=[]
    for color in ("white","green"):
        buf=io.BytesIO();Image.new("RGB",(80,80),color).save(buf,"PNG");blob=buf.getvalue();original.append(blob)
        r=client.post(prefix+"/assets/upload", headers={"Authorization":"Bearer "+tokens["artist"],"Idempotency-Key":str(uuid4())},files={"file":(color+".png",blob,"image/png")})
        assert r.status_code==200,r.text
        ids.append(r.json()["id"])
    # One pending language must not prevent the same artist uploading for another language.
    action("deliver",site=sites[0],asset_ids=ids)
    extra=io.BytesIO();Image.new("RGB",(80,80),"blue").save(extra,"PNG")
    upload=client.post(prefix+"/assets/upload",headers={"Authorization":"Bearer "+tokens["artist"],"Idempotency-Key":str(uuid4())},files={"file":("next-language.png",extra.getvalue(),"image/png")})
    assert upload.status_code==200,upload.text
    for site in sites:
        if site==sites[0]:
            d=read()
        else:
            action("save-delivery",site=site,asset_ids=ids,note="合成草稿")
            d=action("deliver",site=site,asset_ids=ids)
        delivery=next(m["current_delivery_id"] for m in d["task_markets"] if m["site"]==site)
        d=action("review","operator",site=site,delivery_id=delivery,result="accepted")
        response=client.get(f"/api/deliveries/{delivery}/download",headers={"Authorization":"Bearer "+tokens["operator"]})
        assert response.status_code==200
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            assert len(z.namelist())==2 and all('_'+site+'_' in name for name in z.namelist())
            assert [hashlib.sha256(z.read(name)).digest() for name in z.namelist()]==[hashlib.sha256(blob).digest() for blob in original]
    assert d["tasks"][0]["state"]=="accepted"
