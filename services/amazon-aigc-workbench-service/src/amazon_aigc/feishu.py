"""Target-pinned transport. CLI user identity locally; app credentials on isolated server."""

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlsplit

import httpx

from .config import valid_target


class FeishuGateway:
    def __init__(self, settings, client=None):
        self.s = settings
        self.client = client
        self._token_lock = Lock()
        self.token = ""
        self.expires = 0
        if not valid_target(settings.environment, settings.base_id, settings.sku_table_id):
            raise ValueError("Configured Base required")

    def cli(self, args):
        proc = subprocess.run(
            ["lark-cli", *args, "--as", "user"],
            text=True,
            check=False,
            capture_output=True,
            timeout=60,
            env={**os.environ, "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1", "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1"},
        )
        if proc.returncode:
            raise RuntimeError(f"Feishu CLI request failed (code {proc.returncode})")
        result = json.loads(proc.stdout)
        if not result.get("ok"):
            raise RuntimeError("Feishu CLI request rejected")
        return result["data"]

    def access_token(self):
        # One refresh per process even when concurrent logins need a token.
        with self._token_lock:
            return self._access_token()

    def _access_token(self):
        if self.token and self.expires > time.time():
            return self.token
        if not self.s.feishu_app_id or not self.s.feishu_app_secret:
            raise RuntimeError("Feishu application credentials not configured")
        response = (self.client or httpx).post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.s.feishu_app_id, "app_secret": self.s.feishu_app_secret},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError("Feishu application authentication failed")
        self.token = data["tenant_access_token"]
        self.expires = time.time() + data.get("expire", 7200) - 120
        return self.token

    def request(self, method, path, **kwargs):
        # No caller can supply a different Base or arbitrary absolute destination.
        if not path.startswith(f"/bitable/v1/apps/{self.s.base_id}/"):
            raise ValueError("Base target refused")
        r = (self.client or httpx).request(
            method,
            "https://open.feishu.cn/open-apis" + path,
            headers={"Authorization": "Bearer " + self.access_token()},
            timeout=kwargs.pop("timeout", 45),
            **kwargs,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError("Feishu request failed: " + str(data.get("code")))
        return data["data"]

    def records(self, table_id, fields=None):
        if self.s.feishu_mode == "cli":
            with tempfile.TemporaryDirectory(prefix="aigc-feishu-") as temp:
                args = [
                    "lark-cli",
                    "base",
                    "+record-list",
                    "--base-token",
                    self.s.base_id,
                    "--table-id",
                    table_id,
                    "--format",
                    "ndjson",
                    "--output",
                    "records.ndjson",
                    "--limit",
                    "2000",
                    "--as",
                    "user",
                ]
                for f in fields or []:
                    args += ["--field-id", f]
                rows = []
                offset = 0
                while True:
                    run = subprocess.run(
                        args + ["--offset", str(offset), "--overwrite"],
                        cwd=temp,
                        text=True,
                        check=False,
                        capture_output=True,
                        timeout=90,
                        env={
                            **os.environ,
                            "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1",
                            "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1",
                        },
                    )
                    if run.returncode:
                        raise RuntimeError("Feishu record read failed")
                    batch = [json.loads(x) for x in (Path(temp) / "records.ndjson").read_text().splitlines() if x]
                    rows.extend(batch)
                    if len(batch) < 2000:
                        return rows
                    offset += len(batch)
        rows = []
        page = None
        while True:
            params = {"page_size": 500, "user_id_type": "union_id"}
            if page:
                params["page_token"] = page
            data = self.request("GET", f"/bitable/v1/apps/{self.s.base_id}/tables/{table_id}/records", params=params)
            for r in data["items"]:
                rows.append({"record_id": r["record_id"], **r["fields"]})
            if not data.get("has_more"):
                return rows
            page = data["page_token"]

    def update(self, record_id, fields):
        allowed = {
            "需求提交时间",
            "编辑资料",
            "刊登状态",
            "制图加急",
            "制图需求说明",
            "计划制图数量",
            "美工",
            "制图状态",
            "制图截止日期",
            "制图派单时间",
            "首次开工时间",
            "成品交付日期",
            "验收结果",
            "验收意见",
            "制图进度留存",
            "承接方留存",
            "美工留存",
            "制图承接方",
            "成品图",
        }
        if set(fields) - allowed:
            raise ValueError("Projection field not allowed")
        if self.s.feishu_mode == "cli":
            return self.cli(
                [
                    "base",
                    "+record-batch-update",
                    "--base-token",
                    self.s.base_id,
                    "--table-id",
                    self.s.sku_table_id,
                    "--json",
                    json.dumps({"update_records": {record_id: fields}}, ensure_ascii=False),
                ]
            )
        converted = {
            k: (v[0] if isinstance(v, list) and len(v) == 1 and isinstance(v[0], str) else v) for k, v in fields.items()
        }
        return self.request(
            "PUT",
            f"/bitable/v1/apps/{self.s.base_id}/tables/{self.s.sku_table_id}/records/{record_id}",
            json={"fields": converted},
            params={"user_id_type": "union_id"},
        )

    def record(self, record_id):
        if self.s.feishu_mode == "openapi":
            data = self.request("GET", f"/bitable/v1/apps/{self.s.base_id}/tables/{self.s.sku_table_id}/records/{record_id}", params={"user_id_type": "union_id"})["record"]
            return {"record_id": data["record_id"], **data["fields"]}
        return next((r for r in self.records(self.s.sku_table_id) if r["record_id"] == record_id), None)

    def product_image(self, attachment, max_bytes=20_000_000):
        """Read a pinned Base attachment without exposing credentials or temporary URLs."""
        token = attachment.get("file_token", "")
        if not re.fullmatch(r"[A-Za-z0-9]{10,100}", token):
            raise ValueError("Invalid product image attachment")
        # The API's extra permission context is needed for advanced-permission Bases.
        # Never request the supplied URL itself; rebuild the fixed Feishu endpoint.
        extra = parse_qs(urlsplit(attachment.get("url", "")).query).get("extra", [""])[0]
        params = {}
        if extra:
            context = json.loads(extra)
            if context.get("bitablePerm", {}).get("tableId") != self.s.sku_table_id:
                raise ValueError("Product image table refused")
            params["extra"] = extra
        with httpx.stream(
            "GET",
            f"https://open.feishu.cn/open-apis/drive/v1/medias/{token}/download",
            params=params,
            headers={"Authorization": "Bearer " + self.access_token()},
            timeout=20,
        ) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError("Product image download did not return content")
            chunks = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("Product image exceeds 20MB")
                chunks.append(chunk)
            return b"".join(chunks)


def value(x):
    if isinstance(x, str):
        return x
    if isinstance(x, list):
        return "".join(value(v) for v in x)
    if isinstance(x, dict):
        return str(x.get("text") or x.get("name") or x.get("link") or "")
    return "" if x is None else str(x)


def user_profile(gateway, union_id):
    import re

    if not re.fullmatch(r"on_[a-zA-Z0-9]+", union_id):
        raise ValueError("Invalid union identity")
    r = httpx.get(
        "https://open.feishu.cn/open-apis/contact/v3/users/" + union_id,
        params={"user_id_type": "union_id"},
        headers={"Authorization": "Bearer " + gateway.access_token()},
        timeout=20,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("code") != 0:
        raise RuntimeError("Employee status read failed: " + str(body.get("code")))
    return body["data"]["user"]


def upload_asset(gateway, asset, storage_root):
    path = storage_root / asset["object_key"]
    with path.open("rb") as stream:
        r = httpx.post(
            "https://open.feishu.cn/open-apis/drive/v1/medias/upload_all",
            headers={"Authorization": "Bearer " + gateway.access_token()},
            data={
                "file_name": asset["name"],
                "parent_type": "bitable_file",
                "parent_node": gateway.s.base_id,
                "size": str(asset["size_bytes"]),
            },
            files={"file": (asset["name"], stream, asset["mime"])},
            timeout=90,
        )
    r.raise_for_status()
    body = r.json()
    if body.get("code") != 0:
        raise RuntimeError("Original image upload failed: " + str(body.get("code")))
    return body["data"]["file_token"]
