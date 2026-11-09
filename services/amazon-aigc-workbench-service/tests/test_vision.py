import asyncio
import base64
import io
import json
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest
from PIL import Image, UnidentifiedImageError
from psycopg.types.json import Jsonb
from test_workflow import LISTING

from amazon_aigc import vision
from amazon_aigc.config import Settings
from amazon_aigc.jobs import run_job
from amazon_aigc.model_gateway import OpenAIProvider, generate, output_schema
from amazon_aigc.openai_relay import Config, validate_payload


def jpeg(color="red"):
    output = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(output, "JPEG")
    return output.getvalue()


def row(image_id="image-1", **changes):
    return dict(
        image_id=image_id,
        kind="selected_sku",
        label="黑色本品",
        required=True,
        state="pending",
        url="https://cbu01.alicdn.com/image.jpg",
        object_key="",
        error="",
        bindings=[{"component_id": "one"}],
        **changes,
    )


def test_selection_excludes_unselected_skus_and_binds_manual_upload(db, setup, monkeypatch):
    users, item, _ = setup
    aid = uuid4()
    with db.connection() as c:
        c.execute(
            "INSERT INTO assets(id,work_item_id,uploaded_by,object_key,name,sha256,size_bytes,mime) VALUES(%s,%s,%s,%s,%s,%s,10,%s)",
            (aid, item, users["operator"]["id"], "owned.png", "本品图", "a" * 64, "image/png"),
        )
        sources = [
            (
                {
                    "id": "supplier-1",
                    "url": "url",
                    "kind": "supplier",
                    "selected_skus": [{"sku_id": "black", "images": ["chosen.jpg"]}],
                },
                None,
                None,
                {
                    "variants": [
                        {"sku_id": "red", "images": ["unselected.jpg"]},
                        {"sku_id": "black", "images": ["chosen.jpg"]},
                    ],
                    "images": ["unselected.jpg", "general.jpg"],
                },
            )
        ]
        monkeypatch.setattr(vision, "source_rows", lambda *a: sources)
        job = {
            "work_item_id": item,
            "payload": {
                "context": {
                    "facts": {
                        "components": [
                            {
                                "id": "one",
                                "source_id": "supplier-1",
                                "supplier_sku_id": "black",
                                "quantity": 2,
                                "image_asset_ids": [str(aid)],
                            }
                        ]
                    }
                }
            },
        }
        rows = vision.candidates(c, job)
        assert [r["kind"] for r in rows] == ["manual", "selected_sku", "supplier_reference"]
        assert "unselected.jpg" not in str(rows)
        assert rows[0]["object_key"] == "owned.png"
        assert rows[1]["bindings"][0]["quantity"] == 2
        assert not rows[1]["required"]  # Uploaded own photo can replace an inaccessible supplier photo.
        job["work_item_id"] = uuid4()
        assert "不属于" in vision.candidates(c, job)[0]["error"]


def test_image_preparation_deduplicates_preserving_bindings_and_rejects_invalid_files(tmp_path, monkeypatch):
    async def fetch(*args):
        return jpeg(), "", ""

    monkeypatch.setattr(vision, "fetch_bytes", fetch)
    rows = [
        row(),
        {**row("image-2"), "kind": "manual", "bindings": [{"component_id": "two"}]},
        {**row("image-3"), "kind": "competitor", "required": False, "error": "竞品读取失败"},
    ]
    images, report = asyncio.run(vision.prepare_images(tmp_path, uuid4(), rows))
    assert len(images) == 1 and report["duplicate_count"] == 1 and report["failed_count"] == 1
    assert report["images"][0]["bindings"] == [{"component_id": "one"}, {"component_id": "two"}]
    assert "data:image" not in json.dumps(report) and "object_key" not in str(report)
    assert base64.b64decode(images[0]["data_url"].split(",")[1]).startswith(b"\xff\xd8")
    rows = [{**row(), "object_key": "../foreign.png", "url": ""}]
    images, report = asyncio.run(vision.prepare_images(tmp_path, uuid4(), rows))
    assert not images and "位置无效" in report["images"][0]["error"]
    with pytest.raises(UnidentifiedImageError):
        vision.encode_image(b"not-an-image")


def test_image_caps_skip_failed_inputs_without_blocking(tmp_path, monkeypatch):
    async def fetch(url, *args):
        return jpeg("red" if "red" in url else "blue"), "", ""

    monkeypatch.setattr(vision, "fetch_bytes", fetch)
    monkeypatch.setattr(vision, "MAX_IMAGES", 1)
    rows = [{**row(), "url": "red"}, {**row("image-2"), "url": "blue"}]
    images, report = asyncio.run(vision.prepare_images(tmp_path, uuid4(), rows))
    assert len(images) == 1 and report["failed_count"] == 1
    rows = [{**row(), "kind": "competitor", "required": False}]
    _, report = asyncio.run(vision.prepare_images(tmp_path, uuid4(), rows))
    assert report["ready_count"] == 1 and "blocked" not in report


def test_observation_receipts_are_exact_and_counts_derived():
    report = vision.public_report([{**row(), "state": "ready"}, {**row("image-2"), "state": "ready"}], "ready")
    receipts = [
        {"image_id": "image-1", "status": "recognized", "observation": "可见黑色袋体", "reason": ""},
        {"image_id": "image-2", "status": "unclear", "observation": "", "reason": "文字太小"},
    ]
    result = vision.apply_observations(report, receipts)
    assert (result["sent_count"], result["recognized_count"], result["unclear_count"]) == (2, 1, 1)
    for invalid in (
        None,
        [],
        receipts[:1],
        [receipts[0], receipts[0]],
        [None, receipts[1]],
        [receipts[0], {**receipts[1], "reason": ""}],
    ):
        with pytest.raises(ValueError):
            vision.apply_observations(report, invalid)


def test_openai_multimodal_payload_and_receipts_never_persist_base64(monkeypatch):
    report = vision.public_report([{**row(), "state": "ready"}], "ready")
    context = {
        "facts": {"included": "one pouch"},
        "operator_notes": "使用说明",
        "editor_requirements": {"requirements": "白底"},
        "_vision_report": report,
        "_vision_images": [
            {
                "image_id": "image-1",
                "detail": "high",
                "data_url": "data:image/jpeg;base64," + base64.b64encode(jpeg()).decode(),
            }
        ],
    }
    settings = Settings(
        database_url="postgresql://unused/test",
        model_enabled=True,
        openai_model="gpt-6.1-terra",
        openai_api_key="synthetic-key",
    )
    sent = []

    def post(url, **kwargs):
        sent.append(kwargs["json"])
        candidate = {
            **LISTING,
            "image_observations": [
                {"image_id": "image-1", "status": "recognized", "observation": "黑色袋体", "reason": ""}
            ],
        }
        return httpx.Response(
            200,
            json={
                "id": "resp_vision_test",
                "output": [{"content": [{"type": "output_text", "text": json.dumps(candidate)}]}],
            },
        )

    monkeypatch.setattr(httpx, "post", post)
    result = generate(settings, "openai", "listing", context)
    content = sent[0]["input"][0]["content"]
    assert content[2]["type"] == "input_image"
    text = json.loads(content[0]["text"])["data"]
    assert text["editor_requirements"] == {"requirements": "白底"} and "_vision_images" not in text
    assert result["vision"]["recognized_count"] == 1 and "image_observations" not in result["candidate"]
    assert "data:image" not in json.dumps(result) and "synthetic-key" not in json.dumps(result)
    bad = deepcopy(context)
    bad["_vision_report"]["images"][0]["image_id"] = "different"
    incomplete = generate(settings, "openai", "listing", bad)
    assert incomplete["candidate"]["title"] == LISTING["title"]
    assert incomplete["vision"]["state"] == "unverified"
    assert incomplete["vision"]["recognized_count"] == 0


@pytest.mark.parametrize("task", ["listing", "brief"])
def test_worker_generates_without_product_images(db, setup, tmp_path, monkeypatch, task):
    users, item, _ = setup
    jid = uuid4()
    with db.connection() as c:
        c.execute(
            "INSERT INTO jobs(id,kind,work_item_id,actor_id,payload) VALUES(%s,'model',%s,%s,%s)",
            (
                jid,
                item,
                users["operator"]["id"],
                Jsonb(
                    {
                        "provider": "openai",
                        "task": task,
                        "vision_enabled": True,
                        "context": {"facts": {}},
                        "revision": 0,
                    }
                ),
            ),
        )
    monkeypatch.setattr("amazon_aigc.provider_config.runtime", lambda c, s, p: s)
    monkeypatch.setattr(vision, "candidates", lambda c, j: [{**row(), "error": "规格图下载失败"}])
    calls = []

    def model(*args):
        calls.append(args[-1])
        return {
            "provider": "openai",
            "model": "test",
            "usage": {},
            "request_id": "test",
            "candidate": {"title": "generated"},
        }

    monkeypatch.setattr("amazon_aigc.jobs.generate", model)
    run_job(db, Settings(database_url="postgresql://unused/test", storage_root=tmp_path), "model")
    with db.connection() as c:
        job = c.execute("SELECT state,result FROM jobs WHERE id=%s", (jid,)).fetchone()
    assert len(calls) == 1 and job["state"] == "succeeded"
    assert not calls[0].get("_vision_images")
    assert job["result"]["vision"]["state"] == "text_only"
    assert job["result"]["timings"]["image_preparation_ms"] >= 0
    assert job["result"]["vision"]["sent_count"] == 0
    assert job["result"]["vision"]["images"][0]["error"] == "规格图下载失败"


def test_relay_preserves_image_blocks_and_rejects_remote_urls_or_limit_bypass(tmp_path):
    _, schema = output_schema("listing")
    context = {
        "_vision_images": [
            {
                "image_id": "image-1",
                "detail": "high",
                "data_url": "data:image/jpeg;base64," + base64.b64encode(jpeg()).decode(),
            }
        ]
    }
    payload = OpenAIProvider().payload("gpt-6.1-terra", "listing", context, schema)
    config = Config(openai_api_key="synthetic", relay_token="synthetic", models=frozenset(["gpt-6.1-terra"]))
    assert validate_payload(payload, config)["input"] == payload["input"]
    for url in (
        "https://example.com/private.jpg",
        "file:///etc/passwd",
        "data:image/jpeg;base64,invalid",
        "data:image/jpeg;base64," + base64.b64encode(b"fake").decode(),
    ):
        bad = deepcopy(payload)
        bad["input"][0]["content"][2]["image_url"] = url
        with pytest.raises(ValueError):
            validate_payload(bad, config)
    bad = deepcopy(payload)
    bad["input"][0]["content"] += bad["input"][0]["content"][1:] * 32
    with pytest.raises(ValueError):
        validate_payload(bad, config)


def test_resume_images_and_explicit_text_only_are_distinct(db, setup, tmp_path, monkeypatch):
    from test_workflow import FACTS, prepare

    from amazon_aigc.domain import detail
    from amazon_aigc.jobs import enqueue
    from amazon_aigc.schemas import Command, JobRequest
    from amazon_aigc.source_pipeline import resume

    users, item, _ = setup
    user = users["operator"]
    prepare(db, user, item, "facts", FACTS)
    settings = Settings(database_url="postgresql://unused/test", storage_root=tmp_path, model_enabled=True)
    monkeypatch.setattr("amazon_aigc.provider_config.runtime", lambda c, s, p: s)
    monkeypatch.setattr(vision, "candidates", lambda c, j: [{**row(), "error": "test missing image"}])
    calls = []

    def model(*args):
        calls.append(args[-1])
        return {"provider": "openai", "model": "test", "usage": {}, "request_id": "test", "candidate": LISTING}

    monkeypatch.setattr("amazon_aigc.jobs.generate", model)
    with db.connection() as c:
        rev = detail(c, user, item)["document"]["revision"]
        job = enqueue(
            c,
            user,
            item,
            JobRequest(kind="model", revision=rev, payload={"provider": "openai", "task": "listing"}),
            settings,
        )
    for mode in ("retry_images", "text_only"):
        with db.connection() as c:
            c.execute(
                "UPDATE jobs SET state='waiting_input',result=%s WHERE id=%s",
                (Jsonb({"vision": {"state": "waiting_images"}}), job["job_id"]),
            )
        with db.connection() as c:
            resume(c, user, item, job["job_id"], Command(revision=rev, data={"mode": mode}))
        run_job(db, settings, "model")
        with db.connection() as c:
            r = c.execute("SELECT state,result FROM jobs WHERE id=%s", (job["job_id"],)).fetchone()
        assert r["state"] == "succeeded"
    assert len(calls) == 2 and not any(call.get("_vision_images") for call in calls)
    assert r["result"]["vision"]["state"] == "text_only" and r["result"]["vision"]["sent_count"] == 0


def test_slow_images_are_skipped_within_budget(tmp_path, monkeypatch):
    async def slow(*args):
        await asyncio.sleep(1)

    monkeypatch.setattr(vision, "fetch_bytes", slow)
    monkeypatch.setattr(vision, "IMAGE_FETCH_SECONDS", 0.01)
    monkeypatch.setattr(vision, "IMAGE_PREPARATION_SECONDS", 0.03)
    images, report = asyncio.run(vision.prepare_images(tmp_path, uuid4(), [row()]))
    assert not images and report["failed_count"] == 1
    assert report["preparation_ms"] < 500 and "自动" in report["reason"]
