#!/usr/bin/env python3
"""Ordered Yandex production runtime for «МАКСимум мебель» MAX bot.

Architecture:
    MAX Webhook -> public ingress -> Yandex Data Streams -> private worker -> YDB

Why Data Streams instead of Message Queue for the business-event transport:
MAX conversations are state machines. A Standard Message Queue trigger does not
preserve message order, while Data Streams is designed for ordered records within
a shard/partition. Production uses exactly one stream partition and a trigger
batch size of one, so the small business bot gets the simplest deterministic
processing path. Yandex Message Queue remains only as the trigger DLQ.

GET /ready is strictly read-only. Webhook activation is always a separate,
explicit cutover action.
"""

from __future__ import annotations

import contextvars
import hashlib
import hmac
import importlib.util
import json
import os
import re
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import boto3

BASE_DIR = Path(__file__).resolve().parent
IMPL_PATH = BASE_DIR / "maxbot-yandex.py"
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8000"))
MODE = os.environ.get("APP_MODE", "").strip().lower()
YDS_ENDPOINT = os.environ.get(
    "YDS_ENDPOINT", "https://yds.serverless.yandexcloud.net"
).strip()
YDS_REGION = os.environ.get("YDS_REGION", "ru-central1").strip() or "ru-central1"
YDS_STREAM_NAME = os.environ.get("YDS_STREAM_NAME", "").strip()
# PutRecord supports up to 1 MiB. Keep headroom for encoding/service overhead.
MAX_UPDATE_STREAM_LIMIT = int(os.environ.get("MAX_UPDATE_STREAM_LIMIT", "900000"))
CURRENT_EVENT_KEY: contextvars.ContextVar[str] = contextvars.ContextVar(
    "maximum_max_event_key", default=""
)


def env_true(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def prepare_environment() -> None:
    if not os.environ.get("MAX_BOT_TOKEN") and os.environ.get("BOT_TOKEN"):
        os.environ["MAX_BOT_TOKEN"] = os.environ["BOT_TOKEN"]

    require("MAX_BOT_TOKEN")
    secret = require("MAX_WEBHOOK_SECRET")
    if not re.fullmatch(r"[A-Za-z0-9_-]{5,256}", secret):
        raise RuntimeError(
            "MAX_WEBHOOK_SECRET must contain 5-256 characters: A-Z a-z 0-9 _ -"
        )

    if MODE not in {"ingress", "worker"}:
        raise RuntimeError("APP_MODE must be exactly 'ingress' or 'worker'")

    if MODE == "ingress":
        require("YDS_STREAM_NAME")
        require("AWS_ACCESS_KEY_ID")
        require("AWS_SECRET_ACCESS_KEY")
    else:
        if not os.environ.get("YDB_CONNECTION_STRING") and not (
            os.environ.get("YDB_ENDPOINT") and os.environ.get("YDB_DATABASE")
        ):
            raise RuntimeError(
                "worker requires YDB_CONNECTION_STRING or both YDB_ENDPOINT and YDB_DATABASE"
            )

    os.environ.setdefault("DATA_DIR", "/tmp/maxbot")
    os.environ.setdefault("DATABASE_PATH", "/tmp/maxbot/maxbot-unused.db")
    os.environ["MAX_AUTO_SUBSCRIBE"] = "0"


def load_impl():
    spec = importlib.util.spec_from_file_location("maximum_maxbot_yandex_base", IMPL_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load Yandex adapter: {IMPL_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_stream_client():
    return boto3.client(
        "kinesis",
        endpoint_url=YDS_ENDPOINT,
        region_name=YDS_REGION,
        aws_access_key_id=require("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=require("AWS_SECRET_ACCESS_KEY"),
    )


def response_helpers(handler):
    def respond(status, body=b"OK", content_type="text/plain; charset=utf-8"):
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def json_response(status, obj):
        respond(
            status,
            json.dumps(obj, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    return respond, json_response


def read_json(handler, max_size=1024 * 1024):
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except ValueError as exc:
        raise ValueError("bad content length") from exc
    if length <= 0 or length > max_size:
        raise ValueError("invalid body size")
    raw = handler.rfile.read(length)
    return raw, json.loads(raw.decode("utf-8"))


def event_partition_key(update: dict) -> str:
    """Choose a stable dialog key; one-partition production still uses it explicitly."""
    chat_id = update.get("chat_id")
    if chat_id is None:
        message = update.get("message") or (update.get("callback") or {}).get("message") or {}
        recipient = message.get("recipient") or {}
        chat_id = recipient.get("chat_id") or message.get("chat_id")
    if chat_id is not None and str(chat_id):
        return f"chat:{chat_id}"

    user = update.get("user") or (update.get("callback") or {}).get("user") or {}
    user_id = user.get("user_id")
    if user_id is not None and str(user_id):
        return f"user:{user_id}"

    update_type = str(update.get("update_type") or "unknown")
    return f"global:{update_type}"


def stable_update_key(update: dict) -> str:
    """Return a stable id for the same MAX update across trigger retries."""
    update_type = str(update.get("update_type") or "unknown")
    callback = update.get("callback") or {}
    callback_id = callback.get("callback_id")
    if callback_id:
        return f"max:{update_type}:callback:{callback_id}"

    message = update.get("message") or callback.get("message") or {}
    body = message.get("body") or {}
    message_id = body.get("mid") or message.get("mid") or message.get("message_id")
    if message_id:
        return f"max:{update_type}:message:{message_id}"

    canonical = json.dumps(update, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"max:{update_type}:sha256:{digest}"


def extract_stream_updates(event: dict):
    """Yield MAX updates from the documented Data Streams trigger JSON envelope.

    Current Yandex documentation states that a Data Streams trigger forwards JSON
    stream messages inside the top-level ``messages`` array. Keep a narrow legacy
    decoder too so an old queued YMQ probe cannot crash a rollback/redeploy test.
    """
    messages = event.get("messages") or []
    if not isinstance(messages, list):
        return
    for item in messages:
        if not isinstance(item, dict):
            continue
        if item.get("update_type"):
            yield "", item
            continue

        # Legacy YMQ-trigger shape: details.message.body contains the original body.
        details = item.get("details") or {}
        msg = details.get("message") or {}
        body = msg.get("body")
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except Exception:
                continue
        if isinstance(body, dict) and body.get("update_type"):
            event_id = str((item.get("event_metadata") or {}).get("event_id") or "")
            yield event_id, body


def install_deterministic_leads(core, storage) -> None:
    """Make terminal lead writes idempotent for a retried MAX update."""

    def save_lead(chat_id, user_id, kind, data, phone="", verified=False):
        storage.init_schema()
        event_key = CURRENT_EVENT_KEY.get()
        if event_key:
            material = f"{event_key}|{chat_id}|{kind}".encode("utf-8")
            lead_id = "lead-" + hashlib.sha256(material).hexdigest()[:40]
        else:
            lead_id = f"lead-{uuid.uuid4().hex}"

        existing = storage.execute(
            """
            DECLARE $id AS Utf8;
            SELECT id FROM leads WHERE id = $id LIMIT 1;
            """,
            {"$id": lead_id},
            idempotent=True,
        )
        if existing and existing[0].rows:
            return lead_id

        details = dict(data or {})
        for key in ("name", "city", "phone"):
            details.pop(key, None)

        storage.execute(
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

    storage.save_lead = save_lead
    core.save_lead = save_lead


def remove_channel(storage, chat_id) -> None:
    """Honor MAX bot_removed migration guidance by deleting the stored chat id."""
    if chat_id is None:
        return
    storage.init_schema()
    storage.execute(
        """
        DECLARE $chat_id AS Utf8;
        DELETE FROM channels WHERE chat_id = $chat_id;
        """,
        {"$chat_id": str(chat_id)},
        idempotent=True,
    )


def create_ingress_handler(impl, core, stream_client):
    class IngressHandler(BaseHTTPRequestHandler):
        server_version = "MaximumFurnitureBotYandexIngress/3.0"

        def log_message(self, fmt, *args):
            print("INGRESS", fmt % args, flush=True)

        def respond(self, *args, **kwargs):
            respond, _ = response_helpers(self)
            return respond(*args, **kwargs)

        def json_response(self, *args, **kwargs):
            _, json_response = response_helpers(self)
            return json_response(*args, **kwargs)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/health":
                self.json_response(
                    200,
                    {
                        "ok": True,
                        "service": "maximum-max-bot",
                        "platform": "yandex-serverless",
                        "mode": "ingress",
                        "transport": "data-streams",
                    },
                )
                return
            if path == "/ready":
                self._ready()
                return
            self.respond(404, b"Not found")

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if path != "/webhook":
                self.respond(404, b"Not found")
                return
            self._max_webhook()

        def _max_webhook(self):
            got = self.headers.get("X-Max-Bot-Api-Secret", "")
            if not hmac.compare_digest(got, core.WEBHOOK_SECRET):
                self.respond(403, b"Forbidden")
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.respond(400, b"Bad content length")
                return
            if length > MAX_UPDATE_STREAM_LIMIT:
                self.respond(413, b"Update too large")
                return

            try:
                raw, update = read_json(self, max_size=MAX_UPDATE_STREAM_LIMIT)
            except Exception:
                self.respond(400, b"Bad JSON")
                return
            if not isinstance(update, dict):
                self.respond(400, b"Bad update")
                return

            try:
                stream_client.put_record(
                    StreamName=YDS_STREAM_NAME,
                    Data=raw,
                    PartitionKey=event_partition_key(update),
                )
            except Exception as exc:
                print("Data Streams put_record error:", repr(exc), flush=True)
                # Non-2xx tells MAX to retry instead of losing the update.
                self.respond(503, b"Stream unavailable")
                return
            self.respond(200, b"OK")

        def _ready(self):
            """Read-only readiness probe; never writes to the stream or MAX."""
            base = impl.public_base_from_request(self)
            target = base.rstrip("/") + "/webhook" if base else ""
            max_api = False
            webhook_present = False

            try:
                status, _ = core.http_json(f"{core.MAX_BASE}/me", timeout=12)
                max_api = 200 <= status < 300
            except Exception as exc:
                print("MAX /me readiness error:", repr(exc), flush=True)

            if max_api and target:
                try:
                    status, result = core.subscriptions()
                    if 200 <= status < 300:
                        webhook_present = any(
                            isinstance(item, dict) and item.get("url") == target
                            for item in (result.get("subscriptions") or [])
                        )
                except Exception as exc:
                    print("MAX subscriptions readiness error:", repr(exc), flush=True)

            stream_configured = bool(YDS_STREAM_NAME and YDS_ENDPOINT)
            ready = max_api and stream_configured and bool(target)
            self.json_response(
                200 if ready else 503,
                {
                    "ok": ready,
                    "mode": "ingress",
                    "transport": "data-streams",
                    "max_api": max_api,
                    "stream_configured": stream_configured,
                    "webhook": webhook_present,
                    "activation_enabled": False,
                    "activation_requested": env_true("MAX_ACTIVATE_WEBHOOK", False),
                    "public_url_configured": bool(target),
                    "read_only": True,
                },
            )

    return IngressHandler


def create_worker_handler(impl, core, storage):
    class WorkerHandler(BaseHTTPRequestHandler):
        server_version = "MaximumFurnitureBotYandexWorker/3.0"

        def log_message(self, fmt, *args):
            print("WORKER", fmt % args, flush=True)

        def respond(self, *args, **kwargs):
            respond, _ = response_helpers(self)
            return respond(*args, **kwargs)

        def json_response(self, *args, **kwargs):
            _, json_response = response_helpers(self)
            return json_response(*args, **kwargs)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/health":
                self.json_response(
                    200,
                    {
                        "ok": True,
                        "service": "maximum-max-bot",
                        "platform": "yandex-serverless",
                        "mode": "worker",
                        "transport": "data-streams",
                    },
                )
                return
            if path == "/ready":
                self._ready()
                return
            self.respond(404, b"Not found")

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if path not in {"/", "/trigger"}:
                self.respond(404, b"Not found")
                return
            self._stream_trigger()

        def _ready(self):
            max_api = False
            storage_ok = False
            try:
                status, _ = core.http_json(f"{core.MAX_BASE}/me", timeout=12)
                max_api = 200 <= status < 300
            except Exception as exc:
                print("MAX /me readiness error:", repr(exc), flush=True)
            try:
                storage_ok = bool(storage.status().get("ok"))
            except Exception as exc:
                print("YDB readiness error:", repr(exc), flush=True)
            ready = max_api and storage_ok
            self.json_response(
                200 if ready else 503,
                {
                    "ok": ready,
                    "mode": "worker",
                    "transport": "data-streams",
                    "max_api": max_api,
                    "storage": storage_ok,
                },
            )

        def _stream_trigger(self):
            try:
                _, event = read_json(self)
                if not isinstance(event, dict) or not isinstance(event.get("messages"), list):
                    self.respond(400, b"Bad trigger event")
                    return

                for trigger_event_id, update in extract_stream_updates(event):
                    event_key = stable_update_key(update)
                    if storage.event_processed(event_key):
                        print("Duplicate MAX update ignored", event_key, flush=True)
                        continue

                    token = CURRENT_EVENT_KEY.set(event_key)
                    try:
                        if update.get("update_type") == "bot_removed":
                            remove_channel(storage, update.get("chat_id"))
                        core.handle_update(update)
                        storage.mark_event_processed(event_key)
                    finally:
                        CURRENT_EVENT_KEY.reset(token)

                    print(
                        "Processed Data Streams event",
                        trigger_event_id or "ordered-record",
                        "as",
                        event_key,
                        flush=True,
                    )

                self.respond(200, b"OK")
            except Exception as exc:
                print("Data Streams worker error:", repr(exc), flush=True)
                self.respond(500, b"Worker failed")

    return WorkerHandler


def main() -> None:
    prepare_environment()
    impl = load_impl()
    core = impl.load_core()

    if MODE == "ingress":
        handler = create_ingress_handler(impl, core, build_stream_client())
    else:
        storage = impl.YDBStorage()
        impl.install_ydb_storage(core, storage)
        impl.install_contact_verification(core)
        install_deterministic_leads(core, storage)
        handler = create_worker_handler(impl, core, storage)

    print(
        f"MAX Yandex runtime starting on {HOST}:{PORT}; mode={MODE}; transport=data-streams",
        flush=True,
    )
    print(f"MAX API: {core.MAX_BASE}", flush=True)
    ThreadingHTTPServer((HOST, PORT), handler).serve_forever()


if __name__ == "__main__":
    main()
