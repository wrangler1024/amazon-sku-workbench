import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from uuid import uuid4

import httpx
import pytest
from psycopg.types.json import Jsonb
from test_openai_relay import relay_config, valid_payload

from amazon_aigc import openai_relay
from amazon_aigc.config import Settings
from amazon_aigc.jobs import run_job
from amazon_aigc.workers import serve


def insert_job(c, item, user, *, index=0, kind="model", **extra):
    jid = uuid4()
    payload = {"provider": "openai", "task": "listing", "context": {"marker": index}, "revision": 0, **extra}
    c.execute(
        "INSERT INTO jobs(id,kind,work_item_id,actor_id,payload) VALUES(%s,%s,%s,%s,%s)",
        (jid, kind, item, user, Jsonb(payload)),
    )
    return jid


def test_four_lanes_claim_each_job_once_and_keep_results_separate(db, setup, monkeypatch):
    users, item, _ = setup
    with db.connection() as c:
        ids = [insert_job(c, item, users["operator"]["id"], index=i) for i in range(6)]
    stop = threading.Event()
    filled = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    counters = {"active": 0, "peak": 0}
    seen = []
    monkeypatch.setattr("amazon_aigc.provider_config.runtime", lambda c, s, p: s)

    def generate(settings, provider, task, context):
        with lock:
            seen.append(context["marker"])
            order = len(seen)
            counters["active"] += 1
            counters["peak"] = max(counters["peak"], counters["active"])
            if counters["active"] == 4:
                filled.set()
        if order <= 4:
            assert release.wait(5)
        with lock:
            counters["active"] -= 1
            if len(seen) == 6:
                stop.set()
        return {
            "provider": provider,
            "model": "fixture",
            "usage": {},
            "request_id": str(context["marker"]),
            "candidate": {"marker": context["marker"]},
        }

    monkeypatch.setattr("amazon_aigc.jobs.generate", generate)
    settings = Settings(
        database_url="postgresql://unused/test", model_concurrency=4, source_concurrency=2, model_user_concurrency=4
    )
    worker = threading.Thread(target=serve, args=(lambda: run_job(db, settings, "model"), 4, stop, 0.01))
    worker.start()
    try:
        assert filled.wait(5)
        with db.connection() as c:
            states = c.execute(
                "SELECT state,count(*) AS n FROM jobs WHERE id=ANY(%s) GROUP BY state", (ids,)
            ).fetchall()
        assert {r["state"]: r["n"] for r in states} == {"running": 4, "queued": 2}
        release.set()
        worker.join(8)
        assert not worker.is_alive() and counters["peak"] == 4 and sorted(seen) == list(range(6))
        with db.connection() as c:
            rows = c.execute("SELECT state,attempts,payload,result FROM jobs WHERE id=ANY(%s)", (ids,)).fetchall()
            calls = c.execute("SELECT count(*) n FROM model_calls WHERE job_id=ANY(%s)", (ids,)).fetchone()["n"]
        assert calls == 6
        assert all(
            r["state"] == "succeeded"
            and r["attempts"] == 1
            and r["result"]["candidate"]["marker"] == r["payload"]["context"]["marker"]
            for r in rows
        )
    finally:
        stop.set()
        release.set()
        worker.join(8)


def test_worker_lane_survives_error_and_drains_on_stop():
    stop = threading.Event()
    calls = []

    def once():
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("synthetic")
        stop.set()
        return True

    serve(once, 1, stop, poll_seconds=0.001)
    assert len(calls) == 2
    with pytest.raises(ValueError):
        serve(once, 9, stop)


def test_two_source_jobs_collect_concurrently_without_mixing_snapshots(db, setup, monkeypatch):
    users, item, _ = setup
    barrier = threading.Barrier(2)

    async def collect(url, storage, key):
        barrier.wait(timeout=5)
        return {
            "title": url,
            "method": "tmapi",
            "status": "complete",
            "snapshot_key": "fixture",
            "parser_version": "fixture",
            "downloads": [],
        }

    monkeypatch.setattr("amazon_aigc.collector_config.reserve", lambda *a: "synthetic")
    monkeypatch.setattr("amazon_aigc.tmapi_collector.collect", collect)
    with db.connection() as c:
        ids = [
            insert_job(
                c,
                item,
                users["operator"]["id"],
                kind="source",
                url=f"https://detail.1688.com/offer/{i}.html",
                source_id=f"s{i}",
                collector="tmapi",
            )
            for i in (1, 2)
        ]
    settings = Settings(
        database_url="postgresql://unused/test", model_concurrency=4, source_concurrency=2, model_user_concurrency=4
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(run_job, db, settings, "source") for _ in ids]
        assert all(f.result(timeout=8) for f in results)
    with db.connection() as c:
        rows = c.execute("SELECT state,payload,result FROM jobs WHERE id=ANY(%s)", (ids,)).fetchall()
        snapshots = c.execute(
            "SELECT source_id,url,body FROM source_snapshots WHERE work_item_id=%s", (item,)
        ).fetchall()
    assert len(snapshots) == 2 and all(
        r["state"] == "succeeded" and r["payload"]["url"] == r["result"]["title"] for r in rows
    )
    assert all(r["url"] == r["body"]["title"] for r in snapshots)


def test_429_is_deferred_bounded_and_success_clears_error(db, setup, monkeypatch):
    users, item, _ = setup
    settings = Settings(
        database_url="postgresql://unused/test", model_enabled=True, openai_api_key="synthetic", openai_model="test"
    )
    monkeypatch.setattr("amazon_aigc.provider_config.runtime", lambda c, s, p: s)
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(429, json={"error": {"type": "relay_busy"}}, headers={"Retry-After": "2"}),
    )
    with db.connection() as c:
        jid = insert_job(c, item, users["operator"]["id"])
    run_job(db, settings, "model")
    with db.connection() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=%s", (jid,)).fetchone()
        assert row["state"] == "queued" and row["attempts"] == 1 and row["lease_token"] is None
        assert row["result"]["retry_after_seconds"] >= 15
    assert run_job(db, settings, "model") is False
    for attempt in (2, 3):
        with db.connection() as c:
            c.execute("UPDATE jobs SET run_after=now() WHERE id=%s", (jid,))
        run_job(db, settings, "model")
        with db.connection() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=%s", (jid,)).fetchone()
        assert row["attempts"] == attempt and row["state"] == ("queued" if attempt == 2 else "failed")
    with db.connection() as c:
        second = insert_job(c, item, users["operator"]["id"])
    run_job(db, settings, "model")
    monkeypatch.setattr(
        "amazon_aigc.jobs.generate",
        lambda *a: {"provider": "openai", "model": "test", "usage": {}, "request_id": "ok", "candidate": {}},
    )
    with db.connection() as c:
        c.execute("UPDATE jobs SET run_after=now() WHERE id=%s", (second,))
    run_job(db, settings, "model")
    with db.connection() as c:
        row = c.execute("SELECT state,error FROM jobs WHERE id=%s", (second,)).fetchone()
    assert row == {"state": "succeeded", "error": None}


@pytest.mark.parametrize("code", ["daily_limit_reached", "insufficient_quota", "upstream_quota_exhausted"])
def test_permanent_quota_error_is_not_retried(db, setup, monkeypatch, code):
    users, item, _ = setup
    settings = Settings(
        database_url="postgresql://unused/test", model_enabled=True, openai_api_key="synthetic", openai_model="test"
    )
    monkeypatch.setattr("amazon_aigc.provider_config.runtime", lambda c, s, p: s)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(429, json={"error": {"type": code}}))
    with db.connection() as c:
        jid = insert_job(c, item, users["operator"]["id"])
    run_job(db, settings, "model")
    with db.connection() as c:
        row = c.execute("SELECT state,attempts FROM jobs WHERE id=%s", (jid,)).fetchone()
    assert row == {"state": "failed", "attempts": 1}


@pytest.mark.parametrize("slots", [4, 8])
def test_relay_four_slots_rejects_excess_without_consuming_quota(tmp_path, monkeypatch, slots):
    entered = threading.Barrier(slots + 1)
    release = threading.Event()

    class Upstream:
        def __init__(self, c):
            pass

        def request(self, *args, **kwargs):
            entered.wait(5)
            assert release.wait(5)
            return 200, b'{"status":"completed"}'

    monkeypatch.setattr(openai_relay, "OpenAIUpstream", Upstream)
    config = replace(relay_config(tmp_path, daily_limit=10), max_concurrency=slots)
    server = ThreadingHTTPServer(("127.0.0.1", 0), openai_relay.make_handler(config))
    thread = threading.Thread(target=server.serve_forever)
    thread.start()

    def request(path="/v1/responses", authorized=True):
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=8)
        headers = {"Content-Type": "application/json"}
        if authorized:
            headers["Authorization"] = "Bearer " + config.relay_token
        conn.request(
            "POST" if path.endswith("responses") else "GET",
            path,
            body=json.dumps(valid_payload()) if path.endswith("responses") else None,
            headers=headers,
        )
        r = conn.getresponse()
        raw = r.read()
        result = (r.status, json.loads(raw) if raw else {}, r.getheader("Retry-After"))
        conn.close()
        return result

    try:
        with ThreadPoolExecutor(max_workers=slots) as pool:
            calls = [pool.submit(request) for _ in range(slots)]
            entered.wait(5)
            assert request(authorized=False)[0] == 401
            status, body, retry = request()
            assert status == 429 and body["error"]["type"] == "relay_busy" and retry == "2"
            health = request("/health")[1]
            assert health["active_requests"] == health["peak_requests"] == slots and health["used"] == slots
            release.set()
            assert all(f.result(timeout=8)[0] == 200 for f in calls)
        health = request("/health")[1]
        assert health["active_requests"] == 0 and health["used"] == slots
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(5)
