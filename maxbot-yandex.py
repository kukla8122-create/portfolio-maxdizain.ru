#!/usr/bin/env python3
"""Yandex Cloud serverless production entrypoint for «МАКСимум мебель» MAX bot.

Architecture:
    MAX -> public Serverless Container /webhook -> Yandex Message Queue ->
    Message Queue trigger -> same Serverless Container -> MAX Bot API

Persistence:
    Yandex Managed Service for YDB (serverless database).

Why a queue is used:
- MAX requires a prompt HTTP 200 from the webhook.
- Serverless instances may be frozen after an HTTP response, so background threads
  are not a reliable place for business processing.
- Message Queue + trigger gives at-least-once processing and retries outside the
  webhook request lifecycle.

Secrets are read only from environment variables / Lockbox injections.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import boto3
import ydb
import ydb.iam

BASE_DIR = Path(__file__).resolve().parent
CORE_PATH = BASE_DIR / "maxbot-selfhosted.py"
YMQ_ENDPOINT = os.environ.get(
    "YMQ_ENDPOINT", "https://message-queue.api.cloud.yandex.net"
).strip()
YMQ_QUEUE_URL = os.environ.get("YMQ_QUEUE_URL", "").strip()
YMQ_REGION = os.environ.get("YMQ_REGION", "ru-central1").strip() or "ru-central1"
PORT = int(os.environ.get("PORT", "8000"))
HOST = "0.0.0.0"
MAX_UPDATE_QUEUE_LIMIT = int(os.environ.get("MAX_UPDATE_QUEUE_LIMIT", "240000"))


def prepare_environment() -> None:
    if not os.environ.get("MAX_BOT_TOKEN") and os.environ.get("BOT_TOKEN"):
        os.environ["MAX_BOT_TOKEN"] = os.environ["BOT_TOKEN"]

    token = os.environ.get("MAX_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("MAX_BOT_TOKEN is required")

    if not os.environ.get("MAX_WEBHOOK_SECRET"):
        os.environ["MAX_WEBHOOK_SECRET"] = hashlib.sha256(
            ("maximum-webhook-v1:" + token).encode("utf-8")
        ).hexdigest()

    if not re.fullmatch(
        r"[A-Za-z0-9_-]{5,256}", os.environ.get("MAX_WEBHOOK_SECRET", "")
    ):
        raise RuntimeError("MAX_WEBHOOK_SECRET has invalid format")

    if not YMQ_QUEUE_URL:
        raise RuntimeError("YMQ_QUEUE_URL is required")
    if not os.environ.get("AWS_ACCESS_KEY_ID"):
        raise RuntimeError("AWS_ACCESS_KEY_ID is required for Yandex Message Queue")
    if not os.environ.get("AWS_SECRET_ACCESS_KEY"):
        raise RuntimeError("AWS_SECRET_ACCESS_KEY is required for Yandex Message Queue")

    if not os.environ.get("YDB_CONNECTION_STRING") and not (
        os.environ.get("YDB_ENDPOINT") and os.environ.get("YDB_DATABASE")
    ):
        raise RuntimeError(
            "Set YDB_CONNECTION_STRING or both YDB_ENDPOINT and YDB_DATABASE"
        )

    # The self-hosted core checks these values during import. Yandex serverless uses
    # YDB instead of its SQLite path, but harmless local defaults keep the core import
    # compatible and make no customer data persistent in the container filesystem.
    os.environ.setdefault("DATA_DIR", "/tmp/maxbot")
    os.environ.setdefault("DATABASE_PATH", "/tmp/maxbot/maxbot-unused.db")
    os.environ["MAX_AUTO_SUBSCRIBE"] = "0"


def load_core():
    spec = importlib.util.spec_from_file_location("maximum_maxbot_core_yandex", CORE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load MAX bot core: {CORE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row_get(row, name, default=None):
    try:
        return row[name]
    except Exception:
        return getattr(row, name, default)


class YDBStorage:
    """Small YDB adapter matching the storage functions expected by the bot core."""

    def __init__(self):
        self._driver = None
        self._pool = None
        self._lock = threading.RLock()
        self._schema_ready = False

    def _connection(self):
        conn = os.environ.get("YDB_CONNECTION_STRING", "").strip()
        if conn:
            return {"connection_string": conn}
        return {
            "endpoint": os.environ["YDB_ENDPOINT"].strip(),
            "database": os.environ["YDB_DATABASE"].strip(),
        }

    def pool(self):
        if self._pool is not None:
            return self._pool
        with self._lock:
            if self._pool is not None:
                return self._pool
            kwargs = self._connection()
            self._driver = ydb.Driver(
                credentials=ydb.iam.MetadataUrlCredentials(),
                root_certificates=ydb.load_ydb_root_certificate(),
                **kwargs,
            )
            self._driver.wait(fail_fast=True, timeout=7)
            self._pool = ydb.QuerySessionPool(self._driver, size=4)
            return self._pool

    def execute(self, query: str, params=None, *, idempotent=False):
        settings = ydb.RetrySettings(max_retries=5, idempotent=idempotent)
        return self.pool().execute_with_retries(
            query,
            params or {},
            retry_settings=settings,
        )

    def init_schema(self):
        if self._schema_ready:
            return
        with self._lock:
            if self._schema_ready:
                return
            pool = self.pool()
            ddl = [
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    chat_id Utf8,
                    flow Utf8,
                    step Utf8,
                    data_json Utf8,
                    updated_at Int64,
                    PRIMARY KEY (chat_id)
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS leads (
                    id Utf8,
                    created_at Int64,
                    chat_id Utf8,
                    user_id Utf8,
                    kind Utf8,
                    name Utf8,
                    city Utf8,
                    details Utf8,
                    phone Utf8,
                    phone_verified Bool,
                    PRIMARY KEY (id)
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS channels (
                    chat_id Utf8,
                    is_channel Bool,
                    added_at Int64,
                    added_by_user_id Utf8,
                    PRIMARY KEY (chat_id)
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key Utf8,
                    value Utf8,
                    PRIMARY KEY (key)
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id Utf8,
                    processed_at Int64,
                    PRIMARY KEY (event_id)
                );
                """,
            ]
            for statement in ddl:
                pool.execute_with_retries(statement)
            self._schema_ready = True
        self._ensure_probe()

    def _ensure_probe(self):
        rows = self.execute(
            """
            DECLARE $key AS Utf8;
            SELECT value FROM settings WHERE key = $key LIMIT 1;
            """,
            {"$key": "storage_probe"},
            idempotent=True,
        )
        existing = rows and rows[0].rows
        if existing:
            return
        self.execute(
            """
            DECLARE $key AS Utf8;
            DECLARE $value AS Utf8;
            UPSERT INTO settings (key, value) VALUES ($key, $value);
            """,
            {"$key": "storage_probe", "$value": uuid.uuid4().hex},
            idempotent=True,
        )

    def set_session(self, chat_id, flow, step, data=None):
        self.init_schema()
        self.execute(
            """
            DECLARE $chat_id AS Utf8;
            DECLARE $flow AS Utf8;
            DECLARE $step AS Utf8;
            DECLARE $data_json AS Utf8;
            DECLARE $updated_at AS Int64;
            UPSERT INTO sessions (chat_id, flow, step, data_json, updated_at)
            VALUES ($chat_id, $flow, $step, $data_json, $updated_at);
            """,
            {
                "$chat_id": str(chat_id),
                "$flow": flow,
                "$step": step,
                "$data_json": json.dumps(data or {}, ensure_ascii=False),
                "$updated_at": int(time.time()),
            },
            idempotent=True,
        )

    def get_session(self, chat_id):
        self.init_schema()
        result = self.execute(
            """
            DECLARE $chat_id AS Utf8;
            SELECT flow, step, data_json FROM sessions
            WHERE chat_id = $chat_id LIMIT 1;
            """,
            {"$chat_id": str(chat_id)},
            idempotent=True,
        )
        if not result or not result[0].rows:
            return None
        row = result[0].rows[0]
        try:
            data = json.loads(_row_get(row, "data_json", "{}") or "{}")
        except Exception:
            data = {}
        return {
            "flow": _row_get(row, "flow", ""),
            "step": _row_get(row, "step", ""),
            "data": data,
        }

    def clear_session(self, chat_id):
        self.init_schema()
        self.execute(
            """
            DECLARE $chat_id AS Utf8;
            DELETE FROM sessions WHERE chat_id = $chat_id;
            """,
            {"$chat_id": str(chat_id)},
            idempotent=True,
        )

    def save_channel(self, chat_id, is_channel, user_id=None):
        if chat_id is None:
            return
        self.init_schema()
        self.execute(
            """
            DECLARE $chat_id AS Utf8;
            DECLARE $is_channel AS Bool;
            DECLARE $added_at AS Int64;
            DECLARE $added_by AS Utf8;
            UPSERT INTO channels (chat_id, is_channel, added_at, added_by_user_id)
            VALUES ($chat_id, $is_channel, $added_at, $added_by);
            """,
            {
                "$chat_id": str(chat_id),
                "$is_channel": bool(is_channel),
                "$added_at": int(time.time()),
                "$added_by": str(user_id or ""),
            },
            idempotent=True,
        )

    def save_lead(self, chat_id, user_id, kind, data, phone="", verified=False):
        self.init_schema()
        details = dict(data or {})
        for key in ("name", "city", "phone"):
            details.pop(key, None)
        lead_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:12]}"
        self.execute(
            """
            DECLARE $id AS Utf8;
            DECLARE $created_at AS Int64;
            DECLARE $chat_id AS Utf8;
            DECLARE $user_id AS Utf8;
            DECLARE $kind AS Utf8;
            DECLARE $name AS Utf8;
            DECLARE $city AS Utf8;
            DECLARE $details AS Utf8;
            DECLARE $phone AS Utf8;
            DECLARE $verified AS Bool;
            UPSERT INTO leads (
                id, created_at, chat_id, user_id, kind, name, city,
                details, phone, phone_verified
            ) VALUES (
                $id, $created_at, $chat_id, $user_id, $kind, $name, $city,
                $details, $phone, $verified
            );
            """,
            {
                "$id": lead_id,
                "$created_at": int(time.time()),
                "$chat_id": str(chat_id),
                "$user_id": str(user_id or ""),
                "$kind": str(kind),
                "$name": str((data or {}).get("name", "")),
                "$city": str((data or {}).get("city", "")),
                "$details": json.dumps(details, ensure_ascii=False),
                "$phone": str(phone or (data or {}).get("phone", "")),
                "$verified": bool(verified),
            },
            idempotent=True,
        )
        return lead_id

    def event_processed(self, event_id: str) -> bool:
        if not event_id:
            return False
        self.init_schema()
        result = self.execute(
            """
            DECLARE $event_id AS Utf8;
            SELECT event_id FROM processed_events
            WHERE event_id = $event_id LIMIT 1;
            """,
            {"$event_id": event_id},
            idempotent=True,
        )
        return bool(result and result[0].rows)

    def mark_event_processed(self, event_id: str):
        if not event_id:
            return
        self.init_schema()
        self.execute(
            """
            DECLARE $event_id AS Utf8;
            DECLARE $processed_at AS Int64;
            UPSERT INTO processed_events (event_id, processed_at)
            VALUES ($event_id, $processed_at);
            """,
            {"$event_id": event_id, "$processed_at": int(time.time())},
            idempotent=True,
        )

    def status(self):
        self.init_schema()
        result = self.execute(
            """
            DECLARE $key AS Utf8;
            SELECT value FROM settings WHERE key = $key LIMIT 1;
            """,
            {"$key": "storage_probe"},
            idempotent=True,
        )
        value = ""
        if result and result[0].rows:
            value = str(_row_get(result[0].rows[0], "value", ""))
        return {"ok": bool(value), "backend": "ydb", "probe": value}


def install_ydb_storage(core, storage: YDBStorage):
    core.init_db = storage.init_schema
    core.set_session = storage.set_session
    core.get_session = storage.get_session
    core.clear_session = storage.clear_session
    core.save_channel = storage.save_channel
    core.save_lead = storage.save_lead


def install_contact_verification(core):
    """Harden MAX request_contact parsing and HMAC verification."""

    def parse_contact_attachment(message):
        body = message.get("body") or {}
        for item in body.get("attachments") or []:
            if item.get("type") != "contact":
                continue
            payload = item.get("payload") or {}
            raw_vcf = str(payload.get("vcf_info") or "")
            claimed_hash = str(payload.get("hash") or "")
            normalized = raw_vcf.replace("\\r\\n", "\r\n").replace("\\n", "\n")
            phone = ""
            match = re.search(r"(?im)^TEL(?:;[^:]*)?:(.+)$", normalized)
            if match:
                phone = core.normalize_phone(match.group(1))

            verified = False
            if raw_vcf and claimed_hash:
                candidates = [raw_vcf]
                if normalized != raw_vcf:
                    candidates.append(normalized)
                for candidate in candidates:
                    digest = hmac.new(
                        core.MAX_TOKEN.encode("utf-8"),
                        candidate.encode("utf-8"),
                        hashlib.sha256,
                    ).hexdigest()
                    if hmac.compare_digest(digest.lower(), claimed_hash.lower()):
                        verified = True
                        break
            return phone, verified
        return "", False

    core.parse_contact_attachment = parse_contact_attachment


def build_sqs_client():
    return boto3.client(
        "sqs",
        endpoint_url=YMQ_ENDPOINT,
        region_name=YMQ_REGION,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    )


def extract_trigger_updates(event: dict):
    """Yield (trigger_event_id, MAX update) pairs from YMQ trigger payload."""
    for envelope in event.get("messages") or []:
        metadata = envelope.get("event_metadata") or {}
        event_id = str(metadata.get("event_id") or "")
        message = ((envelope.get("details") or {}).get("message") or {})
        raw = message.get("body")
        if not isinstance(raw, str) or not raw:
            raise ValueError("YMQ trigger message body is empty")
        update = json.loads(raw)
        if not isinstance(update, dict):
            raise ValueError("Queued MAX update must be a JSON object")
        yield event_id, update


def public_base_from_request(handler) -> str:
    configured = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    host = (handler.headers.get("X-Forwarded-Host") or handler.headers.get("Host") or "").strip()
    if not host:
        return ""
    return "https://" + host


def create_handler(core, storage: YDBStorage, sqs):
    class Handler(BaseHTTPRequestHandler):
        server_version = "MaximumFurnitureBotYandex/1.0"

        def log_message(self, fmt, *args):
            print("WEB", fmt % args, flush=True)

        def respond(self, status, body=b"OK", content_type="text/plain; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def json_response(self, status, obj):
            self.respond(
                status,
                json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def read_json(self, max_size=1024 * 1024):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("bad content length") from exc
            if length <= 0 or length > max_size:
                raise ValueError("invalid body size")
            raw = self.rfile.read(length)
            return raw, json.loads(raw.decode("utf-8"))

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/health":
                self.json_response(
                    200,
                    {"ok": True, "service": "maximum-max-bot", "platform": "yandex-serverless"},
                )
                return

            if path == "/storage":
                try:
                    self.json_response(200, storage.status())
                except Exception as exc:
                    print("YDB status error:", repr(exc), flush=True)
                    self.json_response(503, {"ok": False, "backend": "ydb"})
                return

            if path == "/ready":
                self._ready()
                return

            self.respond(404, b"Not found")

        def _ready(self):
            base = public_base_from_request(self)
            target = base + "/webhook" if base else ""
            max_api = False
            ydb_ok = False
            queue_ok = False
            webhook_ok = False

            try:
                status, _ = core.http_json(f"{core.MAX_BASE}/me", timeout=12)
                max_api = 200 <= status < 300
            except Exception as exc:
                print("MAX /me readiness error:", repr(exc), flush=True)

            try:
                ydb_ok = bool(storage.status().get("ok"))
            except Exception as exc:
                print("YDB readiness error:", repr(exc), flush=True)

            try:
                sqs.get_queue_attributes(QueueUrl=YMQ_QUEUE_URL, AttributeNames=["QueueArn"])
                queue_ok = True
            except Exception as exc:
                print("YMQ readiness error:", repr(exc), flush=True)

            if max_api and target:
                try:
                    status, result = core.subscriptions()
                    if 200 <= status < 300:
                        webhook_ok = any(
                            isinstance(item, dict) and item.get("url") == target
                            for item in (result.get("subscriptions") or [])
                        )
                    if not webhook_ok:
                        body = {
                            "url": target,
                            "update_types": [
                                "bot_added",
                                "bot_removed",
                                "bot_started",
                                "message_created",
                                "message_callback",
                            ],
                            "secret": core.WEBHOOK_SECRET,
                        }
                        status, result = core.http_json(
                            f"{core.MAX_BASE}/subscriptions",
                            method="POST",
                            obj=body,
                            timeout=20,
                        )
                        webhook_ok = 200 <= status < 300 and result.get("success", True) is not False
                except Exception as exc:
                    print("MAX webhook readiness error:", repr(exc), flush=True)

            ready = max_api and ydb_ok and queue_ok and webhook_ok and bool(target)
            self.json_response(
                200 if ready else 503,
                {
                    "ok": ready,
                    "max_api": max_api,
                    "queue": queue_ok,
                    "storage": ydb_ok,
                    "webhook": webhook_ok,
                    "public_url_configured": bool(target),
                },
            )

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if path == "/webhook":
                self._max_webhook()
                return
            # Yandex Message Queue triggers invoke the container with POST and put a
            # JSON event containing `messages` in the request body.
            self._ymq_trigger()

        def _max_webhook(self):
            got = self.headers.get("X-Max-Bot-Api-Secret", "")
            if not hmac.compare_digest(got, core.WEBHOOK_SECRET):
                self.respond(403, b"Forbidden")
                return
            try:
                raw, update = self.read_json(max_size=1024 * 1024)
            except Exception:
                self.respond(400, b"Bad JSON")
                return
            if not isinstance(update, dict):
                self.respond(400, b"Bad update")
                return
            if len(raw) > MAX_UPDATE_QUEUE_LIMIT:
                # Message Queue supports up to 256 KB. MAX update payloads contain
                # attachment metadata rather than binary media and are normally well
                # below this guardrail.
                self.respond(413, b"Update too large")
                return
            try:
                sqs.send_message(
                    QueueUrl=YMQ_QUEUE_URL,
                    MessageBody=raw.decode("utf-8"),
                )
            except Exception as exc:
                print("YMQ enqueue error:", repr(exc), flush=True)
                # MAX will retry delivery when the webhook is temporarily unavailable.
                self.respond(503, b"Queue unavailable")
                return
            # Business processing is now owned by YMQ + trigger. No background thread
            # is required after this response.
            self.respond(200, b"OK")

        def _ymq_trigger(self):
            try:
                _, event = self.read_json(max_size=1024 * 1024)
                if not isinstance(event, dict) or "messages" not in event:
                    self.respond(404, b"Not found")
                    return
                for event_id, update in extract_trigger_updates(event):
                    if event_id and storage.event_processed(event_id):
                        print("YMQ duplicate trigger event ignored", event_id, flush=True)
                        continue
                    core.handle_update(update)
                    if event_id:
                        storage.mark_event_processed(event_id)
                # Trigger treats 2xx as success and deletes messages from the queue.
                self.respond(200, b"OK")
            except Exception as exc:
                print("YMQ worker error:", repr(exc), flush=True)
                # Non-2xx causes the trigger to return the message to the queue after
                # its visibility timeout, preserving at-least-once delivery.
                self.respond(500, b"Worker failed")

    return Handler


def main():
    prepare_environment()
    core = load_core()
    storage = YDBStorage()
    install_ydb_storage(core, storage)
    install_contact_verification(core)
    sqs = build_sqs_client()
    handler = create_handler(core, storage, sqs)
    print(f"MAX Yandex serverless adapter starting on {HOST}:{PORT}", flush=True)
    print(f"MAX API: {core.MAX_BASE}", flush=True)
    ThreadingHTTPServer((HOST, PORT), handler).serve_forever()


if __name__ == "__main__":
    main()
