import asyncio
import json
from copy import deepcopy

import httpx
import pytest

from amazon_aigc.collector import CollectionError
from amazon_aigc.collector_config import save, status
from amazon_aigc.jobs import run_job
from amazon_aigc.tmapi_collector import collect, normalize, unpack

pytest_plugins = ["test_source_pipeline"]
URL = "https://detail.1688.com/offer/900000000002.html"
AMAZON = "https://www.amazon.ca/dp/B0TEST0007"
GENERAL = "https://cbu01.alicdn.com/general.jpg"
BLACK = "https://cbu01.alicdn.com/black.jpg"
PINK = "https://cbu01.alicdn.com/pink.jpg"
SUPPLIER = {
    "item_id": 900000000002,
    "product_url": URL,
    "title": "硅胶包",
    "product_props": [{"材质": "硅胶"}],
    "main_imgs": [GENERAL, BLACK, PINK],
    "sku_props": [{"pid": "0", "values": [{"vid": "0", "imageUrl": BLACK}, {"vid": "1", "imageUrl": PINK}]}],
    "skus": [
        {"skuid": "black", "props_ids": "0:0", "props_names": "黑色", "package_info": {"weight": 99}},
        {"skuid": "pink", "props_ids": "0:1", "props_names": "粉色"},
    ],
}


def test_variant_photos_do_not_mix_into_unselected_product():
    result = normalize(SUPPLIER, URL)
    assert result["images"] == [GENERAL]
    assert result["variant_count"] == 2 and result["variant_images_excluded"] == 2
    assert result["variant_verified"] is False and not result["selected_variants"]
    assert "99" not in str(result["specifications"])
    selected = normalize(SUPPLIER, URL + "?skuId=black")
    assert selected["images"] == [BLACK, GENERAL] and PINK not in selected["images"]
    assert selected["selected_variants"][0]["sku_id"] == "black"
    with pytest.raises(CollectionError, match="SKU"):
        normalize(SUPPLIER, URL + "?skuId=missing")


def test_identity_and_marketplace_must_match():
    with pytest.raises(CollectionError, match="编号"):
        normalize({**SUPPLIER, "item_id": 1}, URL)
    with pytest.raises(CollectionError, match="详情图"):
        normalize(SUPPLIER, URL, {"item_id": 1, "detail_imgs": []})
    with pytest.raises(CollectionError, match="站点"):
        normalize({"item_id": "B0TEST0007", "title": "Pouch", "url": "https://www.amazon.com/dp/B0TEST0007"}, AMAZON)


def test_missing_competitor_description_stays_partial():
    data = {
        "item_id": "B0TEST0007",
        "asin": "B0TEST0007",
        "title": "Pouch",
        "main_imgs": ["https://m.media-amazon.com/image.jpg"],
        "product_props": [{"name": "Material", "value": "Silicone"}],
        "highlights": ["One", "Two", "Three", "Four", "Five"],
        "desc": "",
    }
    result = normalize(data, AMAZON)
    assert result["status"] == "partial" and len(result["bullets"]) == 5 and result["description"] == ""
    assert "未返回长描述" in result["render_error"]


def test_http_success_does_not_hide_business_failure_or_echo_key():
    diagnostic = {}
    with pytest.raises(CollectionError, match="额度") as exc:
        unpack(httpx.Response(200, json={"code": 439, "msg": ("test-tmapi-" + "t" * 16)}), "900000000002", diagnostic)
    assert ("test-tmapi-" + "t" * 16) not in str(exc.value)
    assert diagnostic["business_code"] == 439


def test_detail_failure_keeps_supplier_data_and_safe_snapshot(monkeypatch, tmp_path):
    calls = []
    saved = []

    async def get(self, url, **kw):
        calls.append((url, kw["params"]))
        return (
            httpx.Response(200, json={"code": 200, "data": deepcopy(SUPPLIER)})
            if url.endswith("item_detail")
            else httpx.Response(200, json={"code": 499, "msg": ("test-tmapi-" + "t" * 16)})
        )

    async def persist(result, raw, storage, method, filename):
        saved.append(raw)
        return {**result, "downloads": [], "snapshot_key": "test.json"}

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    monkeypatch.setattr("amazon_aigc.tmapi_collector.persist_result", persist)
    result = asyncio.run(collect(URL, tmp_path, ("test-tmapi-" + "t" * 16)))
    assert result["status"] == "partial" and result["title"] == "硅胶包"
    assert len(calls) == 2 and all(c[0].startswith("https://api.tmapi.top/") for c in calls)
    assert result["api_calls"] == 2 and result["page_ms"] >= 0
    assert ("test-tmapi-" + "t" * 16) not in saved[0] and ("test-tmapi-" + "t" * 16) not in json.dumps(result)


def test_tmapi_switch_is_exclusive_and_actual_material_reaches_generation(db, flow, monkeypatch):
    users, item, settings, calls, write, start, read = flow
    admin = {**users["operator"], "roles": ["admin"]}
    write("sources", [{"id": "s", "kind": "supplier", "url": URL}])
    received = []

    async def tmapi(url, storage, api_key):
        received.append(url)
        r = normalize(SUPPLIER, url)
        return {**r, "method": "tmapi", "snapshot_key": "test.json", "downloads": [], "page_ms": 100, "total_ms": 110}

    monkeypatch.setattr("amazon_aigc.tmapi_collector.collect", tmapi)
    try:
        with db.connection() as c:
            save(c, admin, settings, {"provider": "firecrawl", "api_key": ("fc-test-" + "f" * 16), "enabled": True})
            out = save(c, admin, settings, {"provider": "tmapi", "api_key": ("test-tmapi-" + "t" * 16), "enabled": True})
            assert out["provider"] == "tmapi" and status(c)["provider"] == "tmapi"
            assert (
                c.execute("SELECT enabled FROM collector_configs WHERE provider='firecrawl'").fetchone()["enabled"]
                is False
            )
        first = start()
        second = start("brief")
        assert (
            read(first["job_id"])["payload"]["source_jobs"][0]["job_id"]
            == read(second["job_id"])["payload"]["source_jobs"][0]["job_id"]
        )
        run_job(db, settings, "source")
        run_job(db, settings, "model")
        run_job(db, settings, "model")
        assert received == [URL]
        assert all(read(j["job_id"])["state"] == "succeeded" for j in (first, second))
        assert calls[-1]["source_materials"][0]["collector"] == "tmapi"
        assert calls[-1]["source_materials"][0]["variant_count"] == 2
        assert calls[-1]["source_materials"][0]["variant_verified"] is False
        assert calls[-1]["facts"]["included"] == "4个托盘"
        with db.connection() as c:
            event = c.execute(
                "SELECT detail FROM audit_events WHERE action='collector.request' AND work_item_id=%s", (item,)
            ).fetchone()["detail"]
            assert event["units"] == 2 and event["provider"] == "tmapi"
    finally:
        with db.connection() as c:
            c.execute("DELETE FROM collector_configs")
