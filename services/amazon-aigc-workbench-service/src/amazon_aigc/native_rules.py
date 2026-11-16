"""Bounded interpreter for an explicitly approved, server-owned Base rule export.

No HTTP actions, executable expressions, arbitrary target tables or broadcast
messages are supported. Tenant definitions and identity mappings stay private.
"""

from __future__ import annotations

import copy
import math
from datetime import datetime
from typing import Any

NODES = {
    "AddRecordTrigger",
    "SetRecordTrigger",
    "ChangeRecordTrigger",
    "ReminderTrigger",
    "FindRecordAction",
    "SetRecordAction",
    "IfElseBranch",
    "SwitchBranch",
    "LarkMessageAction",
}
OPERATORS = {"is", "isNot", "isEmpty", "isNotEmpty", "containsAny", "doesNotContainAny", "isLess"}


class RuleError(ValueError):
    pass


class Rules:
    def __init__(self, config: dict):
        self.config = config
        self.tables = {t["name"]: t for t in config["tables"]}
        self.identities = config.get("identities", {})
        self.workflows = {w["workflow_id"]: w for w in config["workflows"]}
        self.field_names = {f["id"]: f["name"] for t in self.tables.values() for f in t["fields"]}
        self.field_ids = {t: {f["name"]: f["id"] for f in v["fields"]} for t, v in self.tables.items()}
        self.option_names = {
            o["id"]: o["name"]
            for t in self.tables.values()
            for f in t["fields"]
            for o in f.get("options", [])
            if o.get("id")
        }
        self.option_names.update(config.get("option_names", {}))
        if not self.workflows or len(self.workflows) > 100:
            raise RuleError("An explicit bounded workflow set is required")
        for w in self.workflows.values():
            steps = w["steps"]
            ids = {s["id"] for s in steps}
            if len(ids) != len(steps) or not 1 <= len(ids) <= 100:
                raise RuleError("Invalid workflow graph")
            triggers = [s for s in steps if s["type"].endswith("Trigger")]
            if len(triggers) != 1:
                raise RuleError("Exactly one trigger is required")
            w["trigger"] = triggers[0]
            for s in steps:
                if s["type"] not in NODES:
                    raise RuleError("Unsupported workflow node: " + s["type"])
                d = s["data"]
                if d.get("table_name") and d["table_name"] not in self.tables:
                    raise RuleError("Unapproved source table")
                if s["type"] == "SetRecordAction":
                    if d["table_name"] != config["record_table"]:
                        raise RuleError("Writes are restricted to the approved record table")
                    if {v["field_name"] for v in d["field_values"]} - set(config["writable_fields"]):
                        raise RuleError("Unapproved workflow output field")
                if s["type"] == "LarkMessageAction" and d.get("send_to_everyone"):
                    raise RuleError("Broadcast is not supported")
                destinations = [s.get("next"), *[l.get("to") for l in s.get("children", {}).get("links", [])]]
                if any(x and x not in ids for x in destinations):
                    raise RuleError("Unknown workflow destination")
            self._validate_values(steps)

    def _validate_values(self, obj):
        if isinstance(obj, list):
            for x in obj:
                self._validate_values(x)
        elif isinstance(obj, dict):
            if "operator" in obj and obj["operator"] not in OPERATORS:
                raise RuleError("Unsupported comparison")
            if obj.get("value_type") not in (None, "option", "ref", "text", "user", "date"):
                raise RuleError("Unsupported value type")
            if obj.get("value_type") == "ref":
                path = obj["value"].split(".")
                if path[0] != "$" or not 3 <= len(path) <= 4 or any(not p.replace("_", "").isalnum() for p in path[1:]):
                    raise RuleError("Invalid reference path")
            if obj.get("value_type") == "option":
                self.option_names[obj["value"]["id"]] = obj["value"]["name"]
            for v in obj.values():
                self._validate_values(v)

    def identity(self, value):
        return self.identities.get(value, value)

    def atoms(self, value: Any) -> list:
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [a for part in value for a in self.atoms(part)]
        if isinstance(value, dict):
            if value.get("id"):
                return [self.option_names.get(value["id"], self.identity(value["id"]))]
            for key in ("text", "name", "value"):
                if key in value:
                    return self.atoms(value[key])
            return []
        return [self.option_names.get(value, self.identity(value)) if isinstance(value, str) else value]

    def text(self, value):
        if isinstance(value, dict) and value.get("name"):
            return value["name"]
        if isinstance(value, list):
            return "、".join(self.text(x) for x in value if self.text(x))
        if isinstance(value, dict):
            return str(value.get("text") or value.get("name") or value.get("link") or value.get("id") or "")
        return "" if value is None else str(value)

    def resolve(self, values, context, timestamp):
        if isinstance(values, list):
            return [self.resolve(x, context, timestamp) for x in values]
        kind, v = values["value_type"], values.get("value")
        if kind == "ref":
            result = context
            for p in v.split(".")[1:]:
                result = result.get(p) if isinstance(result, dict) else None
            return result
        if kind == "option":
            return v["name"]
        if kind == "date":
            if v != "now":
                raise RuleError("Only the original event time is supported")
            return timestamp
        if kind == "user":
            return {"id": self.identity(v["id"]), "name": v.get("name", "")}
        return v

    def compare(self, left, operator, right=None):
        a, b = self.atoms(left), self.atoms(right)
        if operator == "isEmpty":
            return not a
        if operator == "isNotEmpty":
            return bool(a)
        if operator in ("is", "isNot"):
            # Native formula predicates encode numeric operands as text. Keep
            # identifiers/text strict, but accept the documented numeric shape.
            if len(a) == len(b) == 1 and any(
                isinstance(x, (int, float)) and not isinstance(x, bool) for x in (a[0], b[0])
            ):
                try:
                    a, b = [float(a[0])], [float(b[0])]
                    if not all(math.isfinite(x) for x in (a[0], b[0])):
                        return operator == "isNot"
                except (ValueError, TypeError):
                    pass
            result = sorted(a, key=str) == sorted(b, key=str)
            return result if operator == "is" else not result
        if operator in ("containsAny", "doesNotContainAny"):
            result = any(x == y for x in a for y in b)
            return result if operator == "containsAny" else not result
        if operator == "isLess":
            if len(a) != 1 or len(b) != 1:
                return False

            def number(x):
                try:
                    return float(x)
                except (ValueError, TypeError):
                    return datetime.fromisoformat(str(x)).timestamp() * 1000

            return number(a[0]) < number(b[0])
        raise RuleError("Unsupported comparison")

    def condition(self, obj, context, timestamp, row=None):
        if not obj:
            return True
        if "conditions" in obj:
            results = [self.condition(x, context, timestamp, row) for x in obj["conditions"]]
            return any(results) if obj.get("conjunction") == "or" else all(results)
        left = (
            row.get(obj["field_name"])
            if "field_name" in obj and row is not None
            else self.resolve(obj["left_value"], context, timestamp)
        )
        values = obj.get("value", obj.get("right_value", []))
        right = self.resolve(values, context, timestamp)
        return self.compare(left, obj["operator"], right)

    def row_context(self, table, row):
        return {fid: copy.deepcopy(row.get(name)) for name, fid in self.field_ids[table].items()}

    def eligible(self, workflow, row, context, timestamp, changed=None, added=False, timer=False):
        trigger = workflow["trigger"]
        d, kind = trigger["data"], trigger["type"]
        if kind == "ReminderTrigger" and not timer or kind == "AddRecordTrigger" and not added:
            return False
        if kind == "SetRecordTrigger":
            watches = d.get("field_watch_info", [])
            if not watches or not any(
                w["field_name"] in (changed or set())
                and ("operator" not in w or self.condition(w, context, timestamp, row))
                for w in watches
            ):
                return False
        return (
            any(self.condition(x, context, timestamp, row) for x in d["condition_list"])
            if d.get("condition_list")
            else True
        )

    def initial(self, workflow, row, actor, timestamp, record_link):
        tr = workflow["trigger"]
        fields = self.row_context(tr["data"]["table_name"], row)
        fields.update(recordModifiedTime=timestamp, recordModifiedUser=actor, recordLink=record_link)
        return {"context": {tr["id"]: fields}, "stack": [tr.get("next")] if tr.get("next") else [], "steps": 0}

    def execute(self, workflow, state, io, timestamp, checkpoint=lambda _: None):
        """Resume at a durable node boundary. IO journals each external write first."""
        state = copy.deepcopy(state)
        nodes = {s["id"]: s for s in workflow["steps"]}
        while state["stack"]:
            if state["steps"] >= 200:
                raise RuleError("Workflow execution bound exceeded")
            sid = state["stack"][-1]
            node = nodes[sid]
            d, kind, ctx = node["data"], node["type"], state["context"]
            next_nodes = [node["next"]] if node.get("next") else []
            if kind == "FindRecordAction":
                candidates = io.rows(d["table_name"], d.get("ref_info"))
                found = [r for r in candidates if self.condition(d.get("filter_info"), ctx, timestamp, r)]
                records = [self.row_context(d["table_name"], r) for r in found]
                ctx[sid] = {"firstfieldsRecord": records[0] if records else {}, "fieldRecords": records}
                if not found and not d.get("should_proceed_when_no_results"):
                    next_nodes = []
            elif kind == "SetRecordAction":
                patch = {}
                for f in d["field_values"]:
                    result = self.resolve(f["value"], ctx, timestamp)
                    patch[f["field_name"]] = (
                        None if result == [] else result[0] if isinstance(result, list) and len(result) == 1 else result
                    )
                if not io.write(
                    sid, patch, lambda r, d=d, ctx=ctx: self.condition(d.get("filter_info"), ctx, timestamp, r), ctx
                ):
                    # A concurrent newer version or decision invalidates the
                    # remainder of this branch, including its notifications.
                    next_nodes = []
                    state["stack"] = [sid]
            elif kind == "IfElseBranch":
                match = self.condition(d["condition"], ctx, timestamp)
                link = next(
                    (
                        x.get("to")
                        for x in node.get("children", {}).get("links", [])
                        if x["kind"] == ("if_true" if match else "if_false")
                    ),
                    None,
                )
                if link and link not in next_nodes:
                    next_nodes.insert(0, link)
            elif kind == "SwitchBranch":
                links = {x["label"]: x.get("to") for x in node.get("children", {}).get("links", [])}
                selected = []
                for i, branch in enumerate(d["child_branch_list"], 1):
                    if self.condition(branch["condition"], ctx, timestamp):
                        if links.get("branch_" + str(i)):
                            selected.append(links["branch_" + str(i)])
                        if d.get("mode") != "parallel":
                            break
                if not selected and links.get("default"):
                    selected.append(links["default"])
                next_nodes = selected + next_nodes
            elif kind == "LarkMessageAction":
                title = "".join(self.text(self.resolve(x, ctx, timestamp)) for x in d["title"])
                content = "".join(self.text(self.resolve(x, ctx, timestamp)) for x in d["content"])
                recipients = self.atoms(self.resolve(d["receiver"], ctx, timestamp))
                links = [
                    {
                        "text": b["text"],
                        "href": "".join(self.text(self.resolve(x, ctx, timestamp)) for x in b.get("link", [])),
                    }
                    for b in d.get("btn_list", [])
                    if b.get("btn_action") == "openLink"
                ]
                io.message(sid, sorted(set(recipients)), title, content, links, ctx)
            else:
                raise RuleError("A trigger cannot be an executable node")
            state["stack"].pop()
            state["stack"].extend(reversed(next_nodes))
            state["steps"] += 1
            checkpoint(state)
        return state
