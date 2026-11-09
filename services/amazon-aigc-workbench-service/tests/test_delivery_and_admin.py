import hashlib
import io
from uuid import uuid4

import pytest
from fastapi import HTTPException
from PIL import Image
from test_workflow import pool

from amazon_aigc.config import Settings
from amazon_aigc.domain import task_command
from amazon_aigc.provider_config import runtime, save, status


def test_delivery_revision_preserves_first_result(db, setup, tmp_path):
    users, item, _ = setup
    task = pool(db, setup)
    artist = users["artist"]
    operator = users["operator"]
    with db.connection() as c:
        task_command(c, artist, task["id"], "claim", 0, {})
    with db.connection() as c:
        task_command(c, artist, task["id"], "start", 1, {})

    def upload(color):
        image = Image.new("RGB", (32, 32), color)
        b = io.BytesIO()
        image.save(b, "PNG")
        data = b.getvalue()
        aid = uuid4()
        with db.connection() as c:
            c.execute(
                "INSERT INTO assets(id,work_item_id,uploaded_by,object_key,name,sha256,size_bytes,mime,width,height) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,32,32)",
                (
                    aid,
                    item,
                    artist["id"],
                    str(aid),
                    "synthetic.png",
                    hashlib.sha256(data).hexdigest(),
                    len(data),
                    "image/png",
                ),
            )
        return str(aid)

    first = upload("red")
    with db.connection() as c:
        result = task_command(c, artist, task["id"], "deliver", 2, {"asset_ids": [first]})
        assert result["tasks"][0]["state"] == "pending"
    with db.connection() as c:
        result = task_command(c, operator, task["id"], "review", 3, {"result": "rejected", "note": "合成返修测试"})
        assert result["tasks"][0]["first_review_result"] == "rejected"
    second = upload("blue")
    with db.connection() as c:
        task_command(c, artist, task["id"], "deliver", 4, {"asset_ids": [second]})
    with db.connection() as c:
        result = task_command(c, operator, task["id"], "review", 5, {"result": "accepted", "note": "核对通过"})
        assert result["tasks"][0]["state"] == "accepted"
        assert result["tasks"][0]["first_review_result"] == "rejected"
        assert len(result["deliveries"]) == 2
        assert result["deliveries"][1]["asset_ids"] == [first]


def test_admin_secret_encrypted_and_never_returned(db, setup, tmp_path):
    users, _item, _ = setup
    settings = Settings(database_url="postgresql://unused/test", storage_root=tmp_path / "assets")
    with pytest.raises(HTTPException), db.connection() as c:
        save(
            c,
            users["operator"],
            settings,
            "openai",
            {"api_key": "synthetic-test-key", "model": "test", "enabled": True},
        )
    admin = {**users["operator"], "roles": ["admin"]}
    with db.connection() as c:
        output = save(
            c,
            admin,
            settings,
            "openai",
            {"api_key": "synthetic-test-key", "model": "test", "enabled": True, "daily_limit": 7},
        )
        assert "synthetic-test-key" not in str(output)
        row = c.execute("SELECT key_ciphertext FROM provider_configs WHERE provider='openai'").fetchone()
        assert row["key_ciphertext"] != "synthetic-test-key"
        assert runtime(c, settings, "openai").openai_api_key == "synthetic-test-key"
        output = save(
            c, admin, settings, "openai", {"api_key": "", "model": "test2", "enabled": False, "daily_limit": 7}
        )
        assert runtime(c, settings, "openai").openai_api_key == "synthetic-test-key"
        assert runtime(c, settings, "openai").model_enabled is False
        audit = c.execute("SELECT detail FROM audit_events WHERE action='provider.configure'").fetchall()
        assert "synthetic-test-key" not in str(audit)


def test_connection_check_redacts_failures_and_blocks_nonadmin(db, setup, tmp_path, monkeypatch):
    import httpx

    from amazon_aigc.provider_config import check_connection

    users, _, _ = setup
    settings = Settings(database_url="postgresql://unused/test", storage_root=tmp_path / "assets")
    admin = {**users["operator"], "roles": ["admin"]}
    with db.connection() as c:
        save(c, admin, settings, "deepseek", {"api_key": "synthetic-secret", "model": "test-model", "enabled": False})
    with pytest.raises(HTTPException), db.connection() as c:
        check_connection(c, users["artist"], settings, "deepseek")

    def failed(*args, **kwargs):
        raise httpx.ConnectError("synthetic-secret must never reach UI")

    monkeypatch.setattr(httpx, "get", failed)
    with db.connection() as c:
        result = check_connection(c, admin, settings, "deepseek")
        assert result["ok"] is False
        assert "synthetic-secret" not in str(result)
        assert "synthetic-secret" not in str(status(c))


def test_openai_connection_check_uses_configured_gateway(db, setup, tmp_path, monkeypatch):
    import httpx

    from amazon_aigc.provider_config import check_connection

    users, _, _ = setup
    settings = Settings(
        database_url="postgresql://unused/test",
        storage_root=tmp_path / "assets",
        openai_base_url="https://relay.example.test/amazon-aigc/openai",
    )
    admin = {**users["operator"], "roles": ["admin"]}
    with db.connection() as c:
        save(
            c,
            admin,
            settings,
            "openai",
            {"api_key": "synthetic-relay-token", "model": "gpt-6.1-terra", "enabled": True},
        )
    requested = []

    def connected(url, **kwargs):
        requested.append((url, kwargs))
        return httpx.Response(200, json={"data": [{"id": "gpt-6.1-terra"}]})

    monkeypatch.setattr(httpx, "get", connected)
    with db.connection() as c:
        result = check_connection(c, admin, settings, "openai")
    assert result["ok"] is True
    assert requested[0][0] == "https://relay.example.test/amazon-aigc/openai/v1/models"
    assert "synthetic-relay-token" not in str(result)


@pytest.mark.parametrize("provider", ["openai", "deepseek"])
def test_admin_can_remove_daily_ceiling_without_resetting_key(db, setup, tmp_path, provider):
    users, _, _ = setup
    admin = {**users["operator"], "roles": ["admin"]}
    settings = Settings(database_url="postgresql://unused/test", storage_root=tmp_path / "assets")
    with db.connection() as c:
        save(
            c,
            admin,
            settings,
            provider,
            {"api_key": "synthetic-unlimited-key", "model": "test", "enabled": True, "daily_limit": 1500},
        )
        before = c.execute("SELECT key_ciphertext FROM provider_configs WHERE provider=%s", (provider,)).fetchone()
        save(c, admin, settings, provider, {"api_key": "", "model": "test", "enabled": True, "daily_limit": 0})
        # Omitted quota during a later edit must preserve unlimited, never silently reset it.
        save(c, admin, settings, provider, {"api_key": "", "model": "test", "enabled": True})
        current = c.execute(
            "SELECT key_ciphertext,daily_limit FROM provider_configs WHERE provider=%s", (provider,)
        ).fetchone()
        assert current["key_ciphertext"] == before["key_ciphertext"] and current["daily_limit"] == 0
        assert runtime(c, settings, provider).model_daily_limit == 0
