import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC
from datetime import datetime as RealDatetime
from uuid import uuid4

import psycopg
import pytest
from fastapi import HTTPException
from psycopg import sql
from test_concurrency import insert_job
from test_workflow import FACTS, prepare

from amazon_aigc.capacity import daily_usage
from amazon_aigc.config import Settings
from amazon_aigc.db import Database
from amazon_aigc.domain import detail
from amazon_aigc.jobs import enqueue, run_job
from amazon_aigc.openai_relay import DailyCounter
from amazon_aigc.schemas import JobRequest
from amazon_aigc.workers import serve


@pytest.fixture
def db(db):
    # Dedicated queue: prior tests' pending jobs cannot contaminate the load sample.
    url = os.environ.get("AIGC_TEST_DATABASE_URL", os.environ.get("DATABASE_URL"))
    from urllib.parse import urlsplit
    assert urlsplit(url).hostname in ("127.0.0.1", "localhost", "postgres")
    schema = "test_team_capacity_" + uuid4().hex
    with psycopg.connect(url) as c:
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    isolated = Database(url + "?options=-csearch_path%3D" + schema)
    isolated.migrate()
    yield isolated
    isolated.close()
    with psycopg.connect(url) as c:
        c.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_twenty_operators_forty_jobs_fair_and_bounded(db, monkeypatch):
    ids = []
    with db.connection() as c:
        for index in range(20):
            uid, iid = uuid4(), uuid4()
            c.execute(
                "INSERT INTO users(id,name,roles,groups,active,internal) VALUES(%s,%s,ARRAY['operator'],%s,true,true)",
                (uid, "Load-" + str(index), ["group-" + str(index // 10)]),
            )
            c.execute(
                "INSERT INTO work_items(id,environment,site,code,name,owner_id,group_name,review_status) VALUES(%s,'test','US',%s,'load',%s,%s,'approved')",
                (iid, str(iid), uid, "group-" + str(index // 10)),
            )
            for task in range(2):
                ids.append(insert_job(c, iid, uid, index=index * 2 + task))
    settings = Settings(
        database_url="postgresql://unused/test", model_concurrency=8, source_concurrency=4, model_user_concurrency=2
    )
    monkeypatch.setattr("amazon_aigc.provider_config.runtime", lambda c, s, p: s)
    stop = threading.Event()
    ready = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    active = {}
    starts = []
    done = []
    peaks = {"all": 0, "user": 0}

    def generate(s, p, t, context):
        marker = context["marker"]
        actor = marker // 2
        with lock:
            starts.append(actor)
            n = len(starts)
            active[actor] = active.get(actor, 0) + 1
            peaks["all"] = max(peaks["all"], sum(active.values()))
            peaks["user"] = max(peaks["user"], active[actor])
            if sum(active.values()) == 8:
                ready.set()
        if n <= 8:
            assert release.wait(10)
        time.sleep(0.03)
        with lock:
            active[actor] -= 1
            done.append(marker)
            if len(done) == 40:
                stop.set()
        return {
            "provider": p,
            "model": "controlled-load",
            "usage": {},
            "request_id": str(marker),
            "candidate": {"marker": marker},
        }

    monkeypatch.setattr("amazon_aigc.jobs.generate", generate)
    # Two independent worker pools share the same database-wide limit of 8.
    workers = [
        threading.Thread(target=serve, args=(lambda: run_job(db, settings, "model"), 8, stop, 0.01)) for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    try:
        assert ready.wait(10)
        with db.connection() as c:
            rows = c.execute(
                "SELECT actor_id,state,count(*) n FROM jobs WHERE id=ANY(%s) GROUP BY actor_id,state", (ids,)
            ).fetchall()
        assert sum(r["n"] for r in rows if r["state"] == "running") == 8
        assert len(set(starts[:8])) == 8
        release.set()
        for worker in workers:
            worker.join(15)
        assert not any(worker.is_alive() for worker in workers)
        assert peaks["all"] == 8 and peaks["user"] <= 2 and sorted(done) == list(range(40))
        with db.connection() as c:
            results = c.execute("SELECT attempts,state,payload,result FROM jobs WHERE id=ANY(%s)", (ids,)).fetchall()
            calls = c.execute("SELECT count(*) n FROM model_calls WHERE job_id=ANY(%s)", (ids,)).fetchone()["n"]
        assert calls == 40 and all(
            r["state"] == "succeeded"
            and r["attempts"] == 1
            and r["result"]["candidate"]["marker"] == r["payload"]["context"]["marker"]
            for r in results
        )
    finally:
        stop.set()
        release.set()
        for worker in workers:
            worker.join(15)


def test_same_operator_only_two_inflight_even_with_eight_lanes(db, setup, monkeypatch):
    users, item, _ = setup
    with db.connection() as c:
        ids = [insert_job(c, item, users["operator"]["id"], index=i) for i in range(8)]
    settings = Settings(database_url="postgresql://unused/test", model_concurrency=8, model_user_concurrency=2)
    monkeypatch.setattr("amazon_aigc.provider_config.runtime", lambda c, s, p: s)
    gate = threading.Barrier(3)
    release = threading.Event()

    def generate(*args):
        gate.wait(8)
        assert release.wait(8)
        return {"provider": "openai", "model": "fixture", "usage": {}, "request_id": str(uuid4()), "candidate": {}}

    monkeypatch.setattr("amazon_aigc.jobs.generate", generate)
    with ThreadPoolExecutor(max_workers=8) as pool:
        calls = [pool.submit(run_job, db, settings, "model") for _ in range(8)]
        try:
            gate.wait(8)
            deadline = time.monotonic() + 5
            while sum(f.done() for f in calls) < 6 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert sum(f.done() for f in calls) == 6
            with db.connection() as c:
                assert (
                    c.execute("SELECT count(*) n FROM jobs WHERE id=ANY(%s) AND state='running'", (ids,)).fetchone()[
                        "n"
                    ]
                    == 2
                )
        finally:
            release.set()
        assert sum(f.result(8) for f in calls) == 2


def test_shared_daily_quota_is_atomic_across_providers(db, setup, monkeypatch):
    users, item, _ = setup
    user = users["operator"]
    prepare(db, user, item, "facts", FACTS)
    settings = Settings(
        database_url="postgresql://unused/test", model_enabled=True, model_daily_limit=100, model_total_daily_limit=2
    )
    monkeypatch.setattr("amazon_aigc.provider_config.runtime", lambda c, s, p: s)
    with db.connection() as c:
        rev = detail(c, user, item)["document"]["revision"]

    def submit(pair):
        provider, task = pair
        try:
            with db.connection() as c:
                return enqueue(
                    c,
                    user,
                    item,
                    JobRequest(kind="model", revision=rev, payload={"provider": provider, "task": task}),
                    settings,
                )["state"]
        except HTTPException as e:
            return e.status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(submit, [(p, t) for p in ("openai", "deepseek") for t in ("listing", "brief")]))
    assert results.count("waiting_sources") == 2 and results.count(429) == 2
    with db.connection() as c:
        assert daily_usage(c)["total"] == 2


def test_relay_daily_counter_uses_configured_business_timezone(tmp_path, monkeypatch):
    class Clock:
        @classmethod
        def now(cls, zone):
            return RealDatetime(2026, 11, 5, 16, 1, tzinfo=UTC).astimezone(zone)

    monkeypatch.setattr("amazon_aigc.openai_relay.datetime", Clock)
    path = tmp_path / "count.json"
    path.write_text(json.dumps({"day": "2026-11-06", "count": 10}))
    counter = DailyCounter(path, 2000, "Asia/Shanghai")
    assert counter.snapshot()["used"] == 10 and counter.snapshot()["day"] == "2026-11-06"
    assert counter.consume() and counter.snapshot()["used"] == 11


def test_unlimited_daily_calls_allow_both_providers_after_old_ceiling(db, setup, monkeypatch):
    users, item, _ = setup
    user = users["operator"]
    prepare(db, user, item, "facts", FACTS)
    settings = Settings(
        database_url="postgresql://unused/test", model_enabled=True, model_daily_limit=0, model_total_daily_limit=0
    )
    monkeypatch.setattr("amazon_aigc.provider_config.runtime", lambda c, s, p: s)
    with db.connection() as c:
        rev = detail(c, user, item)["document"]["revision"]
        for provider in ("openai", "deepseek"):
            c.execute(
                "INSERT INTO jobs(id,kind,work_item_id,actor_id,state,payload) "
                "SELECT gen_random_uuid(),'model',%s,%s,'succeeded',jsonb_build_object('provider',%s::text) "
                "FROM generate_series(1,2001)",
                (item, user["id"], provider),
            )
        assert daily_usage(c)["total"] == 4002
        for provider in ("openai", "deepseek"):
            for task in ("listing", "brief"):
                result = enqueue(
                    c,
                    user,
                    item,
                    JobRequest(kind="model", revision=rev, payload={"provider": provider, "task": task}),
                    settings,
                )
                assert result["state"] == "waiting_sources"
        assert daily_usage(c) == {"total": 4006, "openai": 2003, "deepseek": 2003}


def test_capacity_admin_view_is_explicit_and_permission_checked(db, setup):
    from fastapi.testclient import TestClient

    from amazon_aigc.api import create_app
    from amazon_aigc.auth import new_session

    users, _, _ = setup
    settings = Settings(
        database_url="postgresql://unused/test",
        model_concurrency=8,
        source_concurrency=4,
        model_user_concurrency=2,
        model_total_daily_limit=1500,
        capacity_team_size=20,
    )
    client = TestClient(create_app(settings, db))
    with db.connection() as c:
        token = new_session(c, users["operator"]["id"])
    headers = {"Authorization": "Bearer " + token}
    assert client.get("/api/admin/capacity", headers=headers).status_code == 403
    with db.connection() as c:
        c.execute("UPDATE users SET roles=ARRAY['admin'] WHERE id=%s", (users["operator"]["id"],))
    result = client.get("/api/admin/capacity", headers=headers).json()
    assert result["team_size"] == 20 and result["model_concurrency"] == 8 and result["source_concurrency"] == 4
    assert result["per_operator_concurrency"] == 2 and result["model_daily_total"] == 1500
    assert result["used_today"]["total"] == 0 and result["timezone"] == "Asia/Shanghai"
