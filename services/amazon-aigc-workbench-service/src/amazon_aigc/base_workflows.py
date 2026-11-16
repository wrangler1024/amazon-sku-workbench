"""Durable, target-pinned replacement for approved native Base workflows.

Run as a separate worker on the existing service image. The trusted private
configuration contains the reviewed rule export, schemas and identity crosswalk.
The listener uses the official authenticated Feishu WebSocket transport.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import signal
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

import httpx
from psycopg.types.json import Jsonb

from .config import settings
from .db import Database
from .feishu import FeishuGateway, value
from .native_rules import Rules

EVENT_TYPE = "drive.file.bitable_record_changed_v1"
GUARDS = ("插件提交ID", "审核状态", "提交运营")


def millis(now):
    return int(now.timestamp() * 1000)


def parsed_time(item):
    if isinstance(item, (int, float)):
        return datetime.fromtimestamp(item / 1000, UTC)
    return datetime.fromisoformat(str(item))


def stable_key(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


class WorkflowGateway(FeishuGateway):
    """Separate write surface: existing artwork projection permissions stay intact."""

    def __init__(self, s, config, client=None):
        super().__init__(s, client)
        self.config = config
        if (
            config["base_id"] != s.base_id
            or next(t["id"] for t in config["tables"] if t["name"] == config["record_table"]) != s.sku_table_id
        ):
            raise ValueError("Workflow configuration target mismatch")

    def records(self, table_id, fields=None):
        if table_id not in {t["id"] for t in self.config["tables"]}:
            raise ValueError("Unapproved workflow source table")
        if fields is None:
            names = set(GUARDS)

            def collect(obj):
                if isinstance(obj, list):
                    for x in obj:
                        collect(x)
                elif isinstance(obj, dict):
                    for k in ("field_name", "watched_field_name"):
                        if obj.get(k):
                            names.add(obj[k])
                    if obj.get("value_type") == "ref":
                        names.add(obj["value"].split(".")[-1])
                    for x in obj.values():
                        collect(x)

            collect(self.config["workflows"])
            schema = next(t for t in self.config["tables"] if t["id"] == table_id)
            fields = [f["name"] for f in schema["fields"] if f["name"] in names or f["id"] in names]
        rows, page = [], None
        while True:
            params = {
                "page_size": 200,
                "user_id_type": "union_id",
                "field_names": json.dumps(fields, ensure_ascii=False),
            }
            if page:
                params["page_token"] = page
            data = self.request("GET", f"/bitable/v1/apps/{self.s.base_id}/tables/{table_id}/records", params=params)
            rows.extend({"record_id": r["record_id"], **r["fields"]} for r in data["items"])
            if not data.get("has_more"):
                return rows
            page = data["page_token"]

    def workflow_update(self, record_id, fields):
        if not re.fullmatch(r"rec[A-Za-z0-9]{6,60}", record_id):
            raise ValueError("Invalid workflow record")
        if not fields or set(fields) - set(self.config["writable_fields"]):
            raise ValueError("Unapproved workflow output field")
        if self.s.feishu_mode != "openapi":
            raise ValueError("Workflow writes require the pinned application transport")
        return self.request(
            "PUT",
            f"/bitable/v1/apps/{self.s.base_id}/tables/{self.s.sku_table_id}/records/{record_id}",
            json={"fields": self._record_fields(fields)},
            params={"user_id_type": "union_id"},
        )

    def send_notice(self, recipient, payload, message_uuid):
        if not re.fullmatch(r"on_[A-Za-z0-9]{6,80}", recipient):
            raise ValueError("Notification recipient has no verified union identity")
        r = (self.client or httpx).post(
            "https://open.feishu.cn/open-apis/im/v1/messages",
            params={"receive_id_type": "union_id"},
            headers={"Authorization": "Bearer " + self.access_token()},
            json={
                "receive_id": recipient,
                "msg_type": "post",
                "content": json.dumps(payload, ensure_ascii=False),
                "uuid": message_uuid,
            },
            timeout=20,
        )
        r.raise_for_status()
        result = r.json()
        if result.get("code") != 0:
            raise RuntimeError("Workflow notification rejected: " + str(result.get("code")))
        return result["data"]["message_id"]


class Coordinator:
    def __init__(self, db, gateway, config, now=lambda: datetime.now(UTC)):
        self.db, self.gateway, self.config, self.now = db, gateway, config, now
        self.rules = Rules(copy.deepcopy(config))
        self.record_table = config["record_table"]
        self.table_ids = {t["name"]: t["id"] for t in config["tables"]}
        self.link = (
            config["base_origin"].rstrip("/") + "/base/" + config["base_id"] + "?table=" + gateway.s.sku_table_id
        )
        self.config_hash = stable_key(config)
        self._last_poll = 0.0
        with db.connection() as c:
            namespace = c.execute("SELECT current_schema() AS namespace").fetchone()["namespace"]
        self.lock_keys = {
            purpose: int(stable_key(config["base_id"], gateway.s.sku_table_id, namespace, purpose)[:15], 16)
            for purpose in ("seed", "events", "runs")
        }

    def enabled(self):
        with self.db.connection() as c:
            r = c.execute("SELECT * FROM base_workflow_control WHERE singleton=true").fetchone()
        return bool(r and r["enabled"] and r["seeded_at"] and r["config_hash"] == self.config_hash)

    def seed(self):
        """Take a baseline only. Never replay historical approvals or notifications."""
        rows = self.gateway.records(self.gateway.s.sku_table_id)
        with self.db.connection() as c:
            c.execute("SELECT pg_advisory_xact_lock(%s)", (self.lock_keys["seed"],))
            control = c.execute("SELECT * FROM base_workflow_control WHERE singleton=true FOR UPDATE").fetchone()
            if control["enabled"] or control["seeded_at"]:
                raise ValueError("An existing workflow baseline cannot be overwritten")
            for r in rows:
                c.execute(
                    "INSERT INTO base_workflow_records(record_id,snapshot) VALUES(%s,%s)", (r["record_id"], Jsonb(r))
                )
                # Returned submissions must not be mistaken for a new submission.
                if value(r.get("插件提交ID")) and value(r.get("审核状态")) not in ("", "草稿"):
                    for w in self.rules.workflows.values():
                        if self.routing(w):
                            self.queue(c, w, r, [], self.now(), "baseline", finished=True)
            c.execute(
                "UPDATE base_workflow_control SET seeded_at=now(),config_hash=%s WHERE singleton=true",
                (self.config_hash,),
            )
        return {"seeded_records": len(rows), "config_hash": self.config_hash}

    @staticmethod
    def routing(w):
        return (
            w["trigger"]["type"] == "ChangeRecordTrigger"
            and any(
                s["type"] == "SetRecordAction" and any(v["field_name"] == "审核状态" for v in s["data"]["field_values"])
                for s in w["steps"]
            )
            and any(
                "提交校验状态" in json.dumps(g, ensure_ascii=False)
                for g in w["trigger"]["data"].get("condition_list", [])
            )
        )

    def run_key(self, w, row, cause):
        if self.routing(w):
            return stable_key(w["workflow_id"], row["record_id"], value(row.get("插件提交ID")))
        return stable_key(w["workflow_id"], row["record_id"], cause)

    def queue(self, c, w, row, actor, at, cause, finished=False):
        key = self.run_key(w, row, cause)
        execution = self.rules.initial(w, row, actor, millis(at), self.link + "&record=" + row["record_id"])
        c.execute(
            "INSERT INTO base_workflow_runs(run_key,event_id,workflow_id,record_id,submission_id,occurred_at,state,execution) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (
                key,
                cause,
                w["workflow_id"],
                row["record_id"],
                value(row.get("插件提交ID")),
                at,
                "succeeded" if finished else "queued",
                Jsonb(execution),
            ),
        )
        return key

    def ingest(self, payload):
        header, event = payload.get("header", {}), payload.get("event", {})
        if header.get("event_type") != EVENT_TYPE or header.get("app_id") != self.gateway.s.feishu_app_id:
            return False
        if (event.get("file_token"), event.get("table_id")) != (self.gateway.s.base_id, self.gateway.s.sku_table_id):
            return False
        identifier = header.get("event_id", "")
        if not isinstance(identifier, str) or not 8 <= len(identifier) <= 200:
            raise ValueError("Invalid Feishu event identifier")
        at = datetime.fromtimestamp(int(event["update_time"]), UTC)
        if not isinstance(event.get("revision"), int) or not isinstance(event.get("action_list"), list):
            raise TypeError("Incomplete record change event")
        # No tokens or authentication headers enter the event journal.
        clean = {"operator_id": event.get("operator_id") or {}, "action_list": event["action_list"]}
        with self.db.connection() as c:
            c.execute(
                "INSERT INTO base_workflow_events(event_id,revision,occurred_at,payload) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (identifier, event["revision"], at, Jsonb(clean)),
            )
            c.execute("UPDATE base_workflow_control SET last_event_at=now() WHERE singleton=true")
        return True

    def decode_fields(self, parts):
        result = {}
        schema = {f["id"]: f for t in self.rules.tables.values() for f in t["fields"]}
        for f in parts or []:
            name = self.rules.field_names.get(f["field_id"])
            if not name:
                continue
            identities = f.get("field_identity_value", {}).get("users")
            if identities is not None:
                v = [
                    {"id": u["user_id"]["union_id"], "name": u.get("name", "")}
                    for u in identities
                    if u["user_id"].get("union_id")
                ]
            else:
                try:
                    v = json.loads(f.get("field_value"))
                except (ValueError, TypeError):
                    v = f.get("field_value")
            if schema[f["field_id"]]["type"] == "select":
                choices = self.rules.atoms(v)
                v = choices if schema[f["field_id"]].get("multiple") else choices[0] if choices else None
            result[name] = v
        return result

    def dispatch(self, c, row, before, actor, at, cause, changed, added=False, timer=False):
        for w in self.rules.workflows.values():
            if w["trigger"]["type"] == "ReminderTrigger" and not timer:
                continue
            context = self.rules.initial(w, row, actor, millis(at), self.link)["context"]
            if self.rules.eligible(w, row, context, millis(at), changed, added, timer):
                if w["trigger"]["type"] == "ChangeRecordTrigger" and before:
                    prior = self.rules.initial(w, before, actor, millis(at), self.link)["context"]
                    if self.rules.eligible(w, before, prior, millis(at), changed, added, timer):
                        continue
                # A returned decision is not a second submission of the same version.
                if self.routing(w) and not value(row.get("插件提交ID")):
                    continue
                self.queue(c, w, row, actor, at, cause)

    def process_event(self):
        with self.db.connection() as lock:
            if not lock.execute("SELECT pg_try_advisory_lock(%s) AS locked", (self.lock_keys["events"],)).fetchone()[
                "locked"
            ]:
                return False
            event = None
            try:
                with self.db.connection() as c:
                    event = c.execute(
                        "SELECT * FROM base_workflow_events WHERE state='queued' AND attempts<5 AND next_attempt_at<=now() ORDER BY revision,occurred_at,event_id LIMIT 1"
                    ).fetchone()
                if not event:
                    return False
                actor_id = event["payload"]["operator_id"].get("union_id")
                actor = (
                    [{"id": actor_id, "name": self.config.get("identity_names", {}).get(actor_id, "当前操作者")}]
                    if actor_id
                    else []
                )
                for action in event["payload"]["action_list"]:
                    rid = action["record_id"]
                    if action["action"] == "record_deleted":
                        with self.db.connection() as c:
                            c.execute("DELETE FROM base_workflow_records WHERE record_id=%s", (rid,))
                        continue
                    with self.db.connection() as c:
                        saved = c.execute("SELECT * FROM base_workflow_records WHERE record_id=%s", (rid,)).fetchone()
                    if saved and saved["revision"] > event["revision"]:
                        continue
                    before = saved["snapshot"] if saved else {"record_id": rid}
                    before = {**before, **self.decode_fields(action.get("before_value", []))}
                    after = {**before, **self.decode_fields(action.get("after_value", [])), "record_id": rid}
                    changes = self.decode_fields(action.get("after_value", []))
                    # Read formulas and values omitted from the event, but retain the
                    # event's historical version for guards against a newer submit.
                    current = self.gateway.record(rid)
                    if not current:
                        continue
                    row = {**current, **after}
                    if value(row.get("提交校验状态")) == "校验中" and "提交校验状态" in changes:
                        row["__submission_at"] = event["occurred_at"].isoformat()
                        row["__submission_actor"] = actor
                    # Derived formula fields are never present in change payloads.
                    for t in self.rules.tables.values():
                        for f in t["fields"]:
                            if f["type"] in ("formula", "lookup"):
                                row[f["name"]] = current.get(f["name"])
                    with self.db.connection() as c:
                        echo = False
                        if not actor:
                            ops = c.execute(
                                "SELECT patch FROM base_workflow_operations WHERE record_id=%s AND state='applied' AND created_at>now()-interval '5 minutes'",
                                (rid,),
                            ).fetchall()
                            echo = bool(changes) and any(
                                all(
                                    k in o["patch"] and self.rules.compare(v, "is", o["patch"][k])
                                    for k, v in changes.items()
                                )
                                for o in ops
                            )
                        if not echo:
                            if "审核状态" in changes and value(row.get("审核状态")) in ("审核通过", "打回修改", "拒绝"):
                                self.authorize_decision(row, actor_id)
                            self.dispatch(
                                c,
                                row,
                                before,
                                actor,
                                event["occurred_at"],
                                event["event_id"],
                                set(changes),
                                action["action"] == "record_added",
                            )
                        c.execute(
                            "INSERT INTO base_workflow_records(record_id,snapshot,revision) VALUES(%s,%s,%s) ON CONFLICT(record_id) DO UPDATE SET snapshot=excluded.snapshot,revision=excluded.revision,updated_at=now()",
                            (rid, Jsonb(row), event["revision"]),
                        )
                with self.db.connection() as c:
                    c.execute(
                        "UPDATE base_workflow_events SET state='succeeded',attempts=attempts+1,finished_at=now(),error=NULL WHERE event_id=%s",
                        (event["event_id"],),
                    )
                return True
            except Exception as e:  # noqa: BLE001 -- Persist a per-event failure without dropping later events.
                if event:
                    with self.db.connection() as c:
                        c.execute(
                            "UPDATE base_workflow_events SET attempts=attempts+1,error=%s,state=CASE WHEN attempts>=4 THEN 'failed' ELSE 'queued' END,next_attempt_at=now()+(interval '5 seconds' * power(2,attempts)) WHERE event_id=%s",
                            (type(e).__name__ + ": " + str(e)[:180], event["event_id"]),
                        )
                return False
            finally:
                lock.execute("SELECT pg_advisory_unlock(%s)", (self.lock_keys["events"],))

    def authorize_decision(self, row, actor_id):
        """Human decisions retain the current server-side role/group boundary."""
        with self.db.connection() as c:
            user = c.execute(
                "SELECT u.* FROM users u JOIN external_identities e ON e.user_id=u.id WHERE e.provider='feishu_union' AND e.tenant=%s AND e.subject=%s",
                (self.gateway.s.tenant, actor_id or ""),
            ).fetchone()
        from .roster_sync import fresh

        if not user or not fresh(user) or not user["active"]:
            raise PermissionError("Review actor is not currently verified")
        if any(p.get("id") == actor_id for p in row.get("提交运营") or []):
            raise PermissionError("Human self-review is not allowed")
        if "admin" not in user["roles"] and not (
            "supervisor" in user["roles"] and value(row.get("运营小组")) in user["groups"]
        ):
            raise PermissionError("Review actor is outside the approved group")

    def run_one(self):
        with self.db.connection() as lock:
            if not lock.execute("SELECT pg_try_advisory_lock(%s) AS locked", (self.lock_keys["runs"],)).fetchone()[
                "locked"
            ]:
                return False
            try:
                with self.db.connection() as c:
                    run = c.execute(
                        "SELECT * FROM base_workflow_runs WHERE state='queued' AND attempts<5 AND next_attempt_at<=now() ORDER BY created_at,run_key LIMIT 1"
                    ).fetchone()
                if not run:
                    return False
                io = WorkflowIO(self, run)
                try:

                    def checkpoint(execution):
                        with self.db.connection() as c:
                            c.execute(
                                "UPDATE base_workflow_runs SET execution=%s WHERE run_key=%s",
                                (Jsonb(execution), run["run_key"]),
                            )

                    self.rules.execute(
                        self.rules.workflows[run["workflow_id"]],
                        run["execution"],
                        io,
                        millis(run["occurred_at"]),
                        checkpoint,
                    )
                    with self.db.connection() as c:
                        c.execute(
                            "UPDATE base_workflow_runs SET state='succeeded',finished_at=now(),error=NULL WHERE run_key=%s",
                            (run["run_key"],),
                        )
                except Exception as e:  # noqa: BLE001 -- Resume the durable cursor after a bounded retry.
                    with self.db.connection() as c:
                        c.execute(
                            "UPDATE base_workflow_runs SET attempts=attempts+1,error=%s,state=CASE WHEN attempts>=4 THEN 'failed' ELSE 'queued' END,next_attempt_at=now()+(interval '5 seconds' * power(2,attempts)) WHERE run_key=%s",
                            (type(e).__name__ + ": " + str(e)[:180], run["run_key"]),
                        )
                return True
            finally:
                lock.execute("SELECT pg_advisory_unlock(%s)", (self.lock_keys["runs"],))

    def notify_one(self):
        with self.db.connection() as c:
            row = c.execute(
                "SELECT * FROM base_workflow_notices WHERE state IN ('queued','failed') AND attempts<5 AND next_attempt_at<=now() ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
            ).fetchone()
            if not row:
                return False
            current = self.gateway.record(row["record_id"])
            relevant = current and all(self.rules.compare(current.get(k), "is", v) for k, v in row["guards"].items())
            if not relevant:
                c.execute(
                    "UPDATE base_workflow_notices SET state='suppressed',error='Record changed before delivery' WHERE notice_key=%s",
                    (row["notice_key"],),
                )
                return True
            try:
                mid = self.gateway.send_notice(
                    row["recipient"], row["payload"], str(uuid5(NAMESPACE_URL, row["notice_key"]))
                )
                c.execute(
                    "UPDATE base_workflow_notices SET state='sent',message_id=%s,sent_at=now(),attempts=attempts+1,error=NULL WHERE notice_key=%s",
                    (mid, row["notice_key"]),
                )
            except Exception as e:  # noqa: BLE001 -- Notification failures cannot roll back business writes.
                c.execute(
                    "UPDATE base_workflow_notices SET state='failed',attempts=attempts+1,error=%s,next_attempt_at=now()+(interval '5 seconds' * power(2,attempts)) WHERE notice_key=%s",
                    (type(e).__name__ + ": " + str(e)[:180], row["notice_key"]),
                )
            return True

    def poll(self):
        """Formula changes do not emit events. Only pending records are re-read."""
        if time.monotonic() - self._last_poll < 2:
            return
        self._last_poll = time.monotonic()
        with self.db.connection() as c:
            candidates = c.execute(
                "SELECT snapshot FROM base_workflow_records WHERE snapshot->>'提交校验状态'='校验中'"
            ).fetchall()
        for entry in candidates:
            old = entry["snapshot"]
            row = self.gateway.record(old["record_id"])
            if not row or value(row.get("插件提交ID")) != value(old.get("插件提交ID")):
                continue
            if self.rules.compare(row.get("提审公式核对"), "is", 1):
                with self.db.connection() as c:
                    original_time = parsed_time(old["__submission_at"]) if old.get("__submission_at") else self.now()
                    # Formula updates are absent from events and may have been
                    # present on the event's first read already. Force evaluation
                    # of the pending condition, with a stable per-version key.
                    self.dispatch(
                        c,
                        row,
                        {},
                        old.get("__submission_actor", []),
                        original_time,
                        "formula:" + value(row.get("插件提交ID")),
                        {"提审公式核对"},
                    )

    def reminders(self):
        now = self.now().astimezone(ZoneInfo(self.config.get("timezone", "UTC")))
        with self.db.connection() as c:
            rows = c.execute("SELECT snapshot FROM base_workflow_records").fetchall()
        for entry in rows:
            row = entry["snapshot"]
            for w in self.rules.workflows.values():
                tr = w["trigger"]
                if tr["type"] != "ReminderTrigger":
                    continue
                d = tr["data"]
                due = row.get(d["field_name"])
                if not due:
                    continue
                day = parsed_time(due).astimezone(now.tzinfo).date() + timedelta(days=d["offset"])
                scheduled = datetime.combine(day, datetime.min.time(), tzinfo=now.tzinfo).replace(
                    hour=d["hour"], minute=d["minute"]
                )
                if day != now.date() or scheduled > now:
                    continue
                ctx = self.rules.initial(w, row, [], millis(scheduled), self.link)["context"]
                if self.rules.eligible(w, row, ctx, millis(scheduled), timer=True):
                    with self.db.connection() as c:
                        self.queue(c, w, row, [], scheduled, "reminder:" + scheduled.isoformat())

    def reconcile(self):
        """Refresh timer inputs and recover new submissions after an event gap.

        Only the embedded submission timestamp is replayable. A scan must never
        invent a manual decision/withdrawal actor or an approval event time.
        """
        rows = self.gateway.records(self.gateway.s.sku_table_id)
        with self.db.connection() as c:
            control = c.execute("SELECT seeded_at FROM base_workflow_control WHERE singleton=true").fetchone()
            saved = {
                r["record_id"]: r["snapshot"]
                for r in c.execute("SELECT record_id,snapshot FROM base_workflow_records").fetchall()
            }
        for row in rows:
            rid = row["record_id"]
            old = saved.get(rid, {})
            version = value(row.get("插件提交ID"))
            match = re.search(r"-(\d{13})$", version)
            if (
                control["seeded_at"]
                and match
                and value(row.get("审核状态")) in ("", "草稿", "打回修改")
                and value(row.get("提交校验状态")) in ("校验中", "通过")
            ):
                at = datetime.fromtimestamp(int(match[1]) / 1000, UTC)
                if control["seeded_at"] <= at <= self.now() + timedelta(seconds=5):
                    self.recover(rid)
            metadata = (
                {k: v for k, v in old.items() if k.startswith("__")} if value(old.get("插件提交ID")) == version else {}
            )
            with self.db.connection() as c:
                c.execute(
                    "INSERT INTO base_workflow_records(record_id,snapshot) VALUES(%s,%s) ON CONFLICT(record_id) DO UPDATE SET snapshot=excluded.snapshot,updated_at=now()",
                    (rid, Jsonb({**metadata, **row})),
                )
        with self.db.connection() as c:
            c.execute("UPDATE base_workflow_control SET last_reconcile_at=now() WHERE singleton=true")

    def recover(self, record_id):
        row = self.gateway.record(record_id)
        version = value(row.get("插件提交ID"))
        match = re.search(r"-(\d{13})$", version)
        if not match or value(row.get("审核状态")) not in ("草稿", "打回修改", ""):
            raise ValueError("Recovery requires an unprocessed existing submission")
        if value(row.get("提交校验状态")) not in ("校验中", "通过"):
            raise ValueError("Recovery cannot submit an unsubmitted draft")
        at = datetime.fromtimestamp(int(match[1]) / 1000, UTC)
        if at > self.now() + timedelta(seconds=5):
            raise ValueError("Future submission timestamp refused")
        actor = row.get("提交运营") or []
        cause = "recovery:" + version
        with self.db.connection() as c:
            # Reconstruct the existing commit event, not an approval decision.
            self.dispatch(c, row, {}, actor, at, cause, {"提交校验状态", "插件提交ID"})
        return {"record_id": record_id, "submission_id": version, "event_time": at.isoformat()}

    def status(self):
        with self.db.connection() as c:
            control = dict(c.execute("SELECT * FROM base_workflow_control WHERE singleton=true").fetchone())
            counts = {
                name: [
                    dict(x)
                    for x in c.execute("SELECT state,count(*) AS count FROM " + name + " GROUP BY state").fetchall()
                ]
                for name in ("base_workflow_events", "base_workflow_runs", "base_workflow_notices")
            }
        return {
            "control": control,
            "counts": counts,
            "workflows": len(self.rules.workflows),
            "config_hash": self.config_hash,
        }


class WorkflowIO:
    def __init__(self, coordinator, run):
        self.q, self.run = coordinator, run
        self.rules, self.gateway = coordinator.rules, coordinator.gateway

    def rows(self, table, reference):
        if table == self.q.record_table:
            r = self.gateway.record(self.run["record_id"])
            return [r] if r else []
        return self.gateway.records(self.q.table_ids[table])

    def same_version(self, row):
        return value(row.get("插件提交ID")) == self.run["submission_id"]

    def write(self, node, patch, predicate, context=None):
        operation = stable_key(self.run["run_key"], node)
        with self.q.db.connection() as c:
            prior = c.execute("SELECT * FROM base_workflow_operations WHERE operation_key=%s", (operation,)).fetchone()
        current = self.gateway.record(self.run["record_id"])
        if not current:
            return False
        if prior:
            if prior["state"] == "applied":
                return self.same_version(current) or all(
                    self.rules.compare(current.get(k), "is", v) for k, v in prior["patch"].items()
                )
            if all(self.rules.compare(current.get(k), "is", v) for k, v in prior["patch"].items()):
                if not self.same_version(current) and "插件提交ID" not in prior["patch"]:
                    return False
                self.complete(operation, prior["before_snapshot"], current, prior["patch"], prior["created_at"])
                return True
        if not self.same_version(current) or not predicate(current) or not self.trigger_current(current, context):
            return False
        if patch.get("审核状态") == "审核通过" and value(current.get("审核状态")) != "审核通过":
            self._verify_self_exemption(current)
        # Routing must also repair the corresponding original submission time,
        # including an event received after the client's formula fallback.
        if patch.get("审核状态") in ("待审核", "审核通过") and value(current.get("审核状态")) in (
            "",
            "草稿",
            "打回修改",
        ):
            original = re.search(r"-(\d{13})$", self.run["submission_id"])
            attempt = datetime.fromtimestamp(int(original[1]) / 1000, UTC) if original else self.run["occurred_at"]
            if attempt > self.run["occurred_at"] + timedelta(seconds=5):
                raise ValueError("Submission timestamp is in the future")
            if not current.get("最近提交审核时间") or parsed_time(current["最近提交审核时间"]) < attempt - timedelta(
                seconds=2
            ):
                patch["最近提交审核时间"] = millis(attempt)
        with self.q.db.connection() as c:
            prepared = c.execute(
                "INSERT INTO base_workflow_operations(operation_key,run_key,record_id,patch,before_snapshot) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(operation_key) DO UPDATE SET patch=base_workflow_operations.patch RETURNING created_at",
                (operation, self.run["run_key"], self.run["record_id"], Jsonb(patch), Jsonb(current)),
            ).fetchone()
        # Recheck after journaling, immediately before the external mutation.
        latest = self.gateway.record(self.run["record_id"])
        if (
            not latest
            or not self.same_version(latest)
            or not predicate(latest)
            or not self.trigger_current(latest, context)
        ):
            return False
        before = copy.deepcopy(latest)
        if any(not self.rules.compare(latest.get(k), "is", v) for k, v in patch.items()):
            self.gateway.workflow_update(self.run["record_id"], patch)
        after = self.gateway.record(self.run["record_id"])
        if not after or any(not self.rules.compare(after.get(k), "is", v) for k, v in patch.items()):
            raise RuntimeError("Workflow output readback mismatch")
        self.complete(operation, before, after, patch, prepared["created_at"])
        return True

    def complete(self, operation, before, after, patch, prepared_at):
        with self.q.db.connection() as c:
            c.execute(
                "UPDATE base_workflow_operations SET state='applied',finished_at=now() WHERE operation_key=%s",
                (operation,),
            )
            saved = c.execute(
                "SELECT snapshot FROM base_workflow_records WHERE record_id=%s", (self.run["record_id"],)
            ).fetchone()
            metadata = {k: v for k, v in (saved["snapshot"] if saved else {}).items() if k.startswith("__")}
            c.execute(
                "UPDATE base_workflow_records SET snapshot=%s,updated_at=now() WHERE record_id=%s",
                (Jsonb({**metadata, **after}), self.run["record_id"]),
            )
            # Cascades are local and durable; they do not depend on an API write
            # being delivered as a second record-change event.
            effect_time = prepared_at if patch.get("审核状态") == "审核通过" else self.run["occurred_at"]
            changed = {k for k, v in patch.items() if not self.rules.compare(before.get(k), "is", v)}
            if changed:
                self.q.dispatch(c, after, before, [], effect_time, "operation:" + operation, changed)

    def trigger_current(self, current, context=None):
        """Revalidate watched values and branch inputs before a ref-only write."""
        workflow = self.rules.workflows[self.run["workflow_id"]]
        trigger = workflow["trigger"]
        original = self.run["execution"]["context"][trigger["id"]]
        if trigger["type"] == "ChangeRecordTrigger":
            ctx = self.rules.initial(workflow, current, [], millis(self.run["occurred_at"]), self.q.link)["context"]
            if not self.rules.eligible(workflow, current, ctx, millis(self.run["occurred_at"])):
                return False
        names = {x["field_name"] for x in trigger["data"].get("field_watch_info", [])}
        read_expected = {}

        # Branches that use the triggering snapshot must not overwrite a newer
        # ERP/sample/artwork update while retaining the same submission version.
        def refs(obj):
            if isinstance(obj, list):
                for x in obj:
                    refs(x)
            elif isinstance(obj, dict):
                if obj.get("value_type") == "ref":
                    parts = obj["value"].split(".")
                    if len(parts) == 3 and parts[1] == trigger["id"] and parts[2] in self.rules.field_names:
                        names.add(self.rules.field_names[parts[2]])
                    if (
                        len(parts) == 4
                        and parts[2] == "firstfieldsRecord"
                        and parts[3] in self.rules.field_names
                        and context
                        and parts[1] in context
                    ):
                        name = self.rules.field_names[parts[3]]
                        if name in self.rules.field_ids[self.q.record_table]:
                            read_expected[name] = context[parts[1]].get("firstfieldsRecord", {}).get(parts[3])
                for x in obj.values():
                    refs(x)

        for step in workflow["steps"]:
            if step["type"] in ("IfElseBranch", "SwitchBranch"):
                refs(step["data"])
        expected = {name: original.get(self.rules.field_ids[self.q.record_table][name]) for name in names}
        expected.update(read_expected)
        with self.q.db.connection() as c:
            for op in c.execute(
                "SELECT patch FROM base_workflow_operations WHERE run_key=%s AND state='applied' ORDER BY created_at",
                (self.run["run_key"],),
            ).fetchall():
                expected.update({k: v for k, v in op["patch"].items() if k in expected})
        return all(self.rules.compare(current.get(k), "is", v) for k, v in expected.items())

    def _verify_self_exemption(self, row):
        owners = row.get("提交运营") or []
        if len(owners) != 1 or value(row.get("评审路径")) != "主管自提免评审":
            raise ValueError("Only the approved supervisor self-submission path may auto-approve")
        with self.q.db.connection() as c:
            user = c.execute(
                "SELECT u.* FROM users u JOIN external_identities e ON e.user_id=u.id WHERE e.provider='feishu_union' AND e.tenant=%s AND e.subject=%s",
                (self.gateway.s.tenant, owners[0]["id"]),
            ).fetchone()
        from .roster_sync import fresh

        if (
            not user
            or not fresh(user)
            or not user["active"]
            or not ("supervisor" in user["roles"] and value(row.get("运营小组")) in user["groups"])
        ):
            raise ValueError("Supervisor self-approval qualification is not verified")

    def message(self, node, recipients, title, text, buttons, context):
        current = self.gateway.record(self.run["record_id"])
        if not current or not self.same_version(current):
            return
        workflow = self.rules.workflows[self.run["workflow_id"]]
        trigger = workflow["trigger"]
        watched = {w["field_name"] for w in trigger["data"].get("field_watch_info", [])}
        expected = {
            name: context[trigger["id"]].get(self.rules.field_ids[self.q.record_table][name]) for name in watched
        }
        with self.q.db.connection() as c:
            operations = c.execute(
                "SELECT patch FROM base_workflow_operations WHERE run_key=%s AND state='applied' ORDER BY created_at",
                (self.run["run_key"],),
            ).fetchall()
        for op in operations:
            expected.update({k: v for k, v in op["patch"].items() if k in watched})
        if any(not self.rules.compare(current.get(k), "is", v) for k, v in expected.items()):
            return
        lines = [
            [{"tag": "text", "text": text}],
            [{"tag": "a", "text": "打开登记表", "href": self.q.link + "&record=" + self.run["record_id"]}],
        ]
        links = [
            {"tag": "a", "text": b["text"], "href": b["href"]}
            for b in buttons
            if b.get("href", "").startswith(self.q.config["base_origin"].rstrip("/") + "/")
        ]
        if links:
            lines.append(links)
        payload = {"zh_cn": {"title": title, "content": lines}}
        guards = {k: current.get(k) for k in GUARDS}
        guards.update({k: current.get(k) for k in watched})
        if trigger["type"] == "ReminderTrigger":
            guards.update({k: current.get(k) for k in ("制图截止日期", "制图状态", "制图承接方", "美工")})
        for recipient in recipients:
            if not isinstance(recipient, str) or not recipient.startswith("on_"):
                raise ValueError("A configured message recipient is not mapped to a union identity")
            key = stable_key(self.run["run_key"], node, recipient)
            with self.q.db.connection() as c:
                c.execute(
                    "INSERT INTO base_workflow_notices(notice_key,run_key,record_id,recipient,payload,guards) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (key, self.run["run_key"], self.run["record_id"], recipient, Jsonb(payload), Jsonb(guards)),
                )


def listen(q, stop):
    import asyncio

    asyncio.set_event_loop(asyncio.new_event_loop())
    import lark_oapi as lark

    def receive(event):
        q.ingest(json.loads(lark.JSON.marshal(event)))

    handler = (
        lark.EventDispatcherHandler.builder("", "").register_p2_drive_file_bitable_record_changed_v1(receive).build()
    )
    while not stop.is_set():
        try:
            client = lark.ws.Client(
                q.gateway.s.feishu_app_id,
                q.gateway.s.feishu_app_secret,
                event_handler=handler,
                log_level=lark.LogLevel.ERROR,
            )
            q.listener_client = client
            with q.db.connection() as c:
                c.execute("UPDATE base_workflow_control SET listener_error=NULL WHERE singleton=true")
            client.start()
        except Exception as e:  # noqa: BLE001 -- Supervise the SDK transport and retain a diagnostic.
            with q.db.connection() as c:
                c.execute(
                    "UPDATE base_workflow_control SET listener_error=%s WHERE singleton=true", (type(e).__name__,)
                )
            stop.wait(5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("seed", "worker", "status", "enable", "disable", "recover", "health"))
    parser.add_argument("--record-id")
    args = parser.parse_args()
    path = os.environ.get("AIGC_WORKFLOW_CONFIG_FILE")
    if not path:
        raise ValueError("AIGC_WORKFLOW_CONFIG_FILE must name a server-owned configuration")
    config = json.loads(Path(path).read_text())
    s = settings()
    db = Database(s.database_url)
    db.migrate()
    q = Coordinator(db, WorkflowGateway(s, config), config)
    if args.command == "seed":
        print(json.dumps(q.seed()))
    elif args.command in ("enable", "disable"):
        with db.connection() as c:
            c.execute(
                "UPDATE base_workflow_control SET enabled=%s WHERE singleton=true AND seeded_at IS NOT NULL AND config_hash=%s",
                (args.command == "enable", q.config_hash),
            )
        print(json.dumps(q.status(), default=str))
    elif args.command == "recover":
        if not q.enabled() or not args.record_id:
            raise ValueError("Recovery requires an enabled, seeded coordinator and a record ID")
        print(json.dumps(q.recover(args.record_id)))
    elif args.command == "status":
        print(json.dumps(q.status(), default=str))
    elif args.command == "health":
        state = q.status()["control"]
        if (
            not state["heartbeat_at"]
            or (datetime.now(UTC) - state["heartbeat_at"]).total_seconds() > 90
            or not state["listener_alive"]
        ):
            raise SystemExit(1)
        print('{"healthy":true}')
    else:
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        listener = threading.Thread(target=listen, args=(q, stop), daemon=True)
        listener.start()
        last_reminder = 0.0
        while not stop.is_set():
            try:
                if q.enabled():
                    for _ in range(40):
                        worked = q.process_event()
                        worked = q.run_one() or worked
                        worked = q.notify_one() or worked
                        if not worked:
                            break
                    q.poll()
                    if time.monotonic() - last_reminder > 60:
                        q.reconcile()
                        q.reminders()
                        last_reminder = time.monotonic()
                with db.connection() as c:
                    c.execute(
                        "UPDATE base_workflow_control SET heartbeat_at=now(),listener_alive=%s WHERE singleton=true",
                        (listener.is_alive() and bool(getattr(getattr(q, "listener_client", None), "_conn", None)),),
                    )
            except Exception as e:  # noqa: BLE001 -- Keep the listener alive after an isolated processing failure.
                print(json.dumps({"workflow_error": type(e).__name__}), flush=True)
            stop.wait(0.5)
    db.close()


if __name__ == "__main__":
    main()
