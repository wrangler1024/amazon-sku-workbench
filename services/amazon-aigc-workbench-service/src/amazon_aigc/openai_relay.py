"""Minimal supported-region OpenAI relay for the Amazon AIGC workbench.

This module intentionally uses only the Python standard library so it can run
as a small systemd service without exposing a general-purpose proxy.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import os
import signal
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_TOKENS = 16000
ALLOWED_FIELDS = {"model", "instructions", "input", "store", "max_output_tokens", "text"}
TASK_SCHEMAS = {
    "listing": "amazon_listing",
    "brief": "amazon_brief",
    "facts": "amazon_facts",
}


@dataclass(frozen=True)
class Config:
    openai_api_key: str = field(repr=False)
    relay_token: str = field(repr=False)
    models: frozenset[str]
    host: str = "127.0.0.1"
    port: int = 8799
    daily_limit: int = 100
    max_concurrency: int = 1
    daily_timezone: str = "UTC"
    timeout_seconds: int = 180
    state_file: Path = Path("/var/lib/amazon-aigc-openai-relay/daily-count.json")

    @classmethod
    def from_env(cls):
        models = frozenset(x.strip() for x in os.environ.get("AIGC_OPENAI_ALLOWED_MODELS", "").split(",") if x.strip())
        try:
            max_concurrency = int(os.environ.get("AIGC_RELAY_MAX_CONCURRENCY", "1"))
            port = int(os.environ.get("AIGC_RELAY_PORT", "8799"))
            daily_limit = int(os.environ.get("AIGC_RELAY_DAILY_LIMIT", "100"))
            timeout_seconds = int(os.environ.get("AIGC_RELAY_TIMEOUT_SECONDS", "180"))
        except ValueError as exc:
            raise RuntimeError("relay numeric configuration is invalid") from exc
        config = cls(
            openai_api_key=os.environ.get("AIGC_OPENAI_API_KEY", "").strip(),
            relay_token=os.environ.get("AIGC_RELAY_TOKEN", "").strip(),
            models=models,
            max_concurrency=max_concurrency,
            daily_timezone=os.environ.get("AIGC_RELAY_DAILY_TIMEZONE", "UTC"),
            host=os.environ.get("AIGC_RELAY_HOST", "127.0.0.1").strip(),
            port=port,
            daily_limit=daily_limit,
            timeout_seconds=timeout_seconds,
            state_file=Path(
                os.environ.get(
                    "AIGC_RELAY_STATE_FILE",
                    "/var/lib/amazon-aigc-openai-relay/daily-count.json",
                )
            ),
        )
        if not config.openai_api_key or not config.relay_token or not config.models:
            raise RuntimeError("relay credentials and allowed models are required")
        if len(config.relay_token) < 32 or len(config.openai_api_key) < 20:
            raise RuntimeError("relay credential format is invalid")
        if config.host not in ("127.0.0.1", "::1", "localhost"):
            raise RuntimeError("relay must bind to a loopback address")
        if not 1 <= config.port <= 65535 or not 0 <= config.daily_limit <= 10000:
            raise RuntimeError("relay numeric configuration is out of range")
        if not 1 <= config.max_concurrency <= 8:
            raise RuntimeError("relay concurrency is out of range")
        if config.daily_timezone not in ("UTC", "Asia/Shanghai"):
            raise RuntimeError("relay timezone is unsupported")
        if not 10 <= config.timeout_seconds <= 180:
            raise RuntimeError("relay timeout is out of range")
        return config


def bearer_authorized(header: str | None, expected: str):
    if not header or not expected:
        return False
    scheme, separator, credential = header.partition(" ")
    return bool(separator and scheme.lower() == "bearer" and hmac.compare_digest(credential, expected))


def validate_payload(payload: Any, config: Config):
    if not isinstance(payload, dict):
        raise TypeError("request body must be a JSON object")
    extra = set(payload) - ALLOWED_FIELDS
    if extra:
        raise ValueError("request contains unsupported fields")
    if payload.get("model") not in config.models:
        raise ValueError("model is not allowed")
    if payload.get("store") is not False:
        raise ValueError("store must be false")
    max_output_tokens = payload.get("max_output_tokens")
    if not isinstance(max_output_tokens, int) or not 1 <= max_output_tokens <= MAX_OUTPUT_TOKENS:
        raise ValueError("max_output_tokens is out of range")
    instructions = payload.get("instructions")
    raw_input = payload.get("input")
    if not isinstance(instructions, str) or not instructions or len(instructions) > 30000:
        raise ValueError("instructions are invalid")
    if isinstance(raw_input, list):
        if (
            len(raw_input) != 1
            or not isinstance(raw_input[0], dict)
            or set(raw_input[0]) != {"role", "content"}
            or raw_input[0]["role"] != "user"
        ):
            raise ValueError("image input must contain one user message")
        content = raw_input[0]["content"]
        if not isinstance(content, list) or not 2 <= len(content) <= 65:
            raise ValueError("image input content is invalid")
        text_parts = []
        images = 0
        decoded_bytes = 0
        for part in content:
            if not isinstance(part, dict):
                raise TypeError("image input part is invalid")
            if part.get("type") == "input_text" and set(part) == {"type", "text"} and isinstance(part["text"], str):
                text_parts.append(part["text"])
            elif (
                part.get("type") == "input_image"
                and set(part) == {"type", "image_url", "detail"}
                and part["detail"] in ("low", "high", "auto")
            ):
                url = part["image_url"]
                prefix = "data:image/jpeg;base64,"
                if not isinstance(url, str) or not url.startswith(prefix):
                    raise ValueError("only inline validated JPEG images are allowed")
                try:
                    data = base64.b64decode(url[len(prefix) :], validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise ValueError("invalid image encoding") from exc
                if (
                    not 16 <= len(data) <= 2_000_000
                    or not data.startswith(b"\xff\xd8\xff")
                    or not data.endswith(b"\xff\xd9")
                ):
                    raise ValueError("invalid or oversized JPEG image")
                decoded_bytes += len(data)
                images += 1
                if images > 32 or decoded_bytes > 10_000_000:
                    raise ValueError("image count or byte limit exceeded")
            else:
                raise ValueError("unsupported multimodal content")
        if (
            not images
            or content[0].get("type") != "input_text"
            or not text_parts
            or sum(len(t.encode()) for t in text_parts) > 320 * 1024
        ):
            raise ValueError("multimodal task text is invalid")
        raw_input = text_parts[0]
    if not isinstance(raw_input, str) or not raw_input or len(raw_input.encode()) > 320 * 1024:
        raise ValueError("input is invalid")
    try:
        task_input = json.loads(raw_input)
    except (TypeError, ValueError) as exc:
        raise ValueError("input must contain a JSON task") from exc
    task = task_input.get("task") if isinstance(task_input, dict) else None
    text = payload.get("text")
    if not isinstance(text, dict) or set(text) != {"format"}:
        raise ValueError("structured output format is required")
    output_format = text.get("format")
    if not isinstance(output_format, dict):
        raise TypeError("structured output format is invalid")
    if output_format.get("type") != "json_schema" or output_format.get("strict") is not True:
        raise ValueError("strict JSON Schema output is required")
    expected_name = TASK_SCHEMAS.get(task)
    if not expected_name or output_format.get("name") != expected_name:
        raise ValueError("task and output schema do not match")
    if not isinstance(output_format.get("schema"), dict):
        raise TypeError("output schema is invalid")
    return payload


class DailyCounter:
    def __init__(self, path: Path, limit: int, timezone="UTC"):
        self.path = path
        self.limit = limit
        self._lock = threading.Lock()
        self.zone = ZoneInfo(timezone)
        self._day = datetime.now(self.zone).date().isoformat()
        self._count = 0
        if path.exists():
            try:
                state = json.loads(path.read_text())
                if state.get("day") == self._day and isinstance(state.get("count"), int):
                    self._count = max(0, state["count"])
            except (OSError, ValueError, TypeError) as exc:
                raise RuntimeError("daily counter state is unreadable") from exc

    def consume(self):
        with self._lock:
            today = datetime.now(self.zone).date().isoformat()
            if today != self._day:
                self._day = today
                self._count = 0
            if self.limit > 0 and self._count >= self.limit:
                return False
            self._count += 1
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temporary = tempfile.mkstemp(prefix=".daily-count-", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w") as out:
                    json.dump({"day": self._day, "count": self._count}, out)
                    out.flush()
                    os.fsync(out.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return True

    def snapshot(self):
        with self._lock:
            return {"day": self._day, "used": self._count, "limit": self.limit, "timezone": str(self.zone)}


class OpenAIUpstream:
    def __init__(self, config: Config):
        self.config = config
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path: str, method: str = "GET", payload: dict | None = None):
        if path not in ("/v1/models", "/v1/responses"):
            raise ValueError("unsupported upstream path")
        body = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        request = urllib.request.Request(
            "https://api.openai.com" + path,
            data=body,
            method=method,
            headers={
                "Authorization": "Bearer " + self.config.openai_api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "amazon-aigc-openai-relay/0.1.0",
            },
        )
        try:
            response = self.opener.open(request, timeout=self.config.timeout_seconds)
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            return status, b'{"error":{"type":"upstream_error"}}'
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ConnectionError("upstream request failed") from exc
        with response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("upstream response is too large")
            return response.status, body


class RequestSlots:
    def __init__(self, limit):
        self.limit = limit
        self.semaphore = threading.BoundedSemaphore(limit)
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def acquire(self):
        if not self.semaphore.acquire(blocking=False):
            return False
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        return True

    def release(self):
        with self.lock:
            self.active -= 1
        self.semaphore.release()

    def snapshot(self):
        with self.lock:
            return {"max_concurrency": self.limit, "active_requests": self.active, "peak_requests": self.peak}


def make_handler(config: Config):
    counter = DailyCounter(config.state_file, config.daily_limit, config.daily_timezone)
    upstream = OpenAIUpstream(config)
    active_request = RequestSlots(config.max_concurrency)

    class Handler(BaseHTTPRequestHandler):
        server_version = "AmazonAIGCRelay/0.1"
        sys_version = ""

        def log_message(self, fmt, *args):
            # Default access logs do not include headers or request bodies.
            super().log_message(fmt, *args)

        def send_json(self, status: int, value: Any, retry_after=None):
            body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
            self.send_response(status)
            if retry_after is not None:
                self.send_header("Retry-After", str(retry_after))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def send_error_json(self, status: int, error_type: str):
            retry_after = {"relay_busy": 2, "upstream_rate_limited": 15}.get(error_type)
            self.send_json(status, {"error": {"type": error_type}}, retry_after=retry_after)

        def authorized(self):
            if bearer_authorized(self.headers.get("Authorization"), config.relay_token):
                return True
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.send_header("WWW-Authenticate", "Bearer")
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            return False

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/health":
                self.send_json(
                    HTTPStatus.OK,
                    {"ok": True, "provider": "openai", **active_request.snapshot(), **counter.snapshot()},
                )
                return
            if path != "/v1/models":
                self.send_error_json(HTTPStatus.NOT_FOUND, "not_found")
                return
            if not self.authorized():
                return
            try:
                status, body = upstream.request("/v1/models")
            except (ConnectionError, ValueError):
                self.send_error_json(HTTPStatus.BAD_GATEWAY, "upstream_unavailable")
                return
            if status != HTTPStatus.OK:
                self.send_error_json(HTTPStatus.BAD_GATEWAY, "upstream_error")
                return
            try:
                value = json.loads(body)
                value["data"] = [item for item in value.get("data", []) if item.get("id") in config.models]
            except (ValueError, TypeError, AttributeError):
                self.send_error_json(HTTPStatus.BAD_GATEWAY, "upstream_invalid_response")
                return
            self.send_json(HTTPStatus.OK, value)

        def do_POST(self):
            if urlsplit(self.path).path != "/v1/responses":
                self.send_error_json(HTTPStatus.NOT_FOUND, "not_found")
                return
            if not self.authorized():
                return
            if not active_request.acquire():
                self.send_error_json(HTTPStatus.TOO_MANY_REQUESTS, "relay_busy")
                return
            try:
                self.handle_response_request()
            finally:
                active_request.release()

        def handle_response_request(self):
            if urlsplit(self.path).path != "/v1/responses":
                self.send_error_json(HTTPStatus.NOT_FOUND, "not_found")
                return
            if not self.authorized():
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self.send_error_json(HTTPStatus.LENGTH_REQUIRED, "content_length_required")
                return
            if length <= 0 or length > MAX_REQUEST_BYTES:
                self.send_error_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request_too_large")
                return
            if self.headers.get_content_type() != "application/json":
                self.send_error_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "json_required")
                return
            try:
                payload = validate_payload(json.loads(self.rfile.read(length)), config)
            except (ValueError, TypeError, UnicodeDecodeError):
                self.send_error_json(HTTPStatus.BAD_REQUEST, "invalid_request")
                return
            if not counter.consume():
                self.send_error_json(HTTPStatus.TOO_MANY_REQUESTS, "daily_limit_reached")
                return
            try:
                status, body = upstream.request("/v1/responses", method="POST", payload=payload)
            except (ConnectionError, ValueError):
                self.send_error_json(HTTPStatus.BAD_GATEWAY, "upstream_unavailable")
                return
            if status == HTTPStatus.TOO_MANY_REQUESTS:
                try:
                    error = json.loads(body).get("error") or {}
                    exhausted = error.get("code") == "insufficient_quota" or error.get("type") == "insufficient_quota"
                except (ValueError, AttributeError, TypeError):
                    exhausted = False
                self.send_error_json(
                    HTTPStatus.TOO_MANY_REQUESTS, "upstream_quota_exhausted" if exhausted else "upstream_rate_limited"
                )
                return
            if not 200 <= status < 300:
                self.send_error_json(HTTPStatus.BAD_GATEWAY, "upstream_error")
                return
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main():
    config = Config.from_env()
    server = ThreadingHTTPServer((config.host, config.port), make_handler(config))
    server.daemon_threads = False
    server.block_on_close = True

    def stop(_signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server.serve_forever(poll_interval=0.5)
    server.server_close()


if __name__ == "__main__":
    main()
