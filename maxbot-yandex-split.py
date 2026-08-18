#!/usr/bin/env python3
"""Strict split Yandex Serverless runtime for «МАКСимум мебель» MAX bot.

APP_MODE=ingress
    Public container. Exposes only GET /health, GET /ready and POST /webhook.
    Valid MAX updates are authenticated with X-Max-Bot-Api-Secret and placed in
    Yandex Message Queue. No YDB access and no business processing live here.

APP_MODE=worker
    Private container. Receives Yandex Message Queue trigger POST requests and
    runs business logic with YDB persistence. It never exposes /webhook.

The split keeps the public trust boundary intentionally small and makes webhook
activation an explicit opt-in via MAX_ACTIVATE_WEBHOOK=1.
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

BASE_DIR = Path(__file__).resolve().parent
IMPL_PATH = BASE_DIR / "maxbot-yandex.py"
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8000"))
MODE = os.environ.get("APP_MODE", "").strip().lower()
MAX_UPDATE_QUEUE_LIMIT = int(os.environ.get("MAX_UPDATE_QUEUE_LIMIT", "240000"))
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
        require("YMQ_QUEUE_URL")
        require("AWS_ACCESS_KEY_ID")
        require("AWS_SECRET_ACCESS_KEY")
    else:
        if not os.environ.get("YDB_CONNECTION_STRING") and not (
            os.environ.get("YDB_ENDPOINT") and os.environ.get("YDB_DATABASE")
        ):
            raise RuntimeError(
                "worker requires YDB_CONNECTION_STRING or both YDB_ENDPOINT and YDB_DATABASE"
            )

    # The shared core still imports its SQLite defaults. In Yandex worker mode all
    # authoritative state is replaced by YDB; ingress does not touch persistence.
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


def stable_update_key(update: dict) -> str:
    """Return a stable id for the same MAX update across YMQ retries."""
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

    # bot_added/bot_started and any future event still get a stable key because the
    # original MAX update JSON is stored unchanged in YMQ and replayed on retries.
    canonical = json.dumps(update, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"max:{update_type}:sha256:{digest}"


def install_deterministic_leads(core, storage) -> None:
    """Make lead writes idempotent for a retried terminal MAX update.

    The worker marks the update processed after business handling so failed calls can
    retry. If a process dies after saving a lead but before that mark, the retry gets
    the same deterministic lead id and therefore cannot create a second lead row.
    """

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


def create_ingress_handler(impl, core, sqs):
    class IngressHandler(BaseHTTPRequestHandler):
        server_version = "MaximumFurnitureBotYandexIngress/2.0"

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
                raw, update = read_json(self)
            except Exception:
                self.respond(400, b"Bad JSON")
                return
            if not isinstance(update, dict):
                self.respond(400, b"Bad update")
                return
            if len(raw) > MAX_UPDATE_QUEUE_LIMIT:
                self.respond(413, b"Update too large")
                return
            try:
                sqs.send_message(
                    QueueUrl=impl.YMQ_QUEUE_URL,
                    MessageBody=raw.decode("utf-8"),
                )
            except Exception as exc:
                print("YMQ enqueue error:", repr(exc), flush=True)
                # A non-200 response asks MAX to retry delivery rather than dropping it.
                self.respond(503, b"Queue unavailable")
                return
            self.respond(200, b"OK")

        def _ready(self):
            base = impl.public_base_from_request(self)
            target = base.rstrip("/") + "/webhook" if base else ""
            activation_enabled = env_true("MAX_ACTIVATE_WEBHOOK", False)
            max_api = False
            queue_ok = False
            webhook_ok = False

            try:
                status, _ = core.http_json(f"{core.MAX_BASE}/me", timeout=12)
                max_api = 200 <= status < 300
            except Exception as exc:
                print("MAX /me readiness error:", repr(exc), flush=True)

            try:
                sqs.get_queue_attributes(
                    QueueUrl=impl.YMQ_QUEUE_URL,
                    AttributeNames=["QueueArn"],
                )
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
                    if activation_enabled and not webhook_ok:
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
                        webhook_ok = (
                            200 <= status < 300
                            and result.get("success", True) is not False
                        )
                except Exception as exc:
                    print("MAX webhook readiness error:", repr(exc), flush=True)

            infrastructure_ok = max_api and queue_ok and bool(target)
            ready = infrastructure_ok and (webhook_ok if activation_enabled else True)
            self.json_response(
                200 if ready else 503,
                {
                    "ok": ready,
                    "mode": "ingress",
                    "max_api": max_api,
                    "queue": queue_ok,
                    "webhook": webhook_ok,
                    "activation_enabled": activation_enabled,
                    "public_url_configured": bool(target),
                },
            )

    return IngressHandler


def create_worker_handler(impl, core, storage):
    class WorkerHandler(BaseHTTPRequestHandler):
        server_version = "MaximumFurnitureBotYandexWorker/2.0"

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
            self._ymq_trigger()

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
                    "max_api": max_api,
                    "storage": storage_ok,
                },
            )

        def _ymq_trigger(self):
            try:
                _, event = read_json(self)
                if not isinstance(event, dict) or "messages" not in event:
                    self.respond(400, b"Bad trigger event")
                    return

                for trigger_event_id, update in impl.extract_trigger_updates(event):
                    event_key = stable_update_key(update)
                    if storage.event_processed(event_key):
                        print("Duplicate MAX update ignored", event_key, flush=True)
                        continue

                    token = CURRENT_EVENT_KEY.set(event_key)
                    try:
                        core.handle_update(update)
                        storage.mark_event_processed(event_key)
                    finally:
                        CURRENT_EVENT_KEY.reset(token)

                    if trigger_event_id:
                        print(
                            "Processed YMQ trigger event",
                            trigger_event_id,
                            "as",
                            event_key,
                            flush=True,
                        )

                self.respond(200, b"OK")
            except Exception as exc:
                print("YMQ worker error:", repr(exc), flush=True)
                # Trigger retries on non-2xx; deterministic lead ids prevent a second
                # lead row if the first attempt died after persisting the lead.
                self.respond(500, b"Worker failed")

    return WorkerHandler


def main() -> None:
    prepare_environment()
    impl = load_impl()
    core = impl.load_core()

    if MODE == "ingress":
        sqs = impl.build_sqs_client()
        handler = create_ingress_handler(impl, core, sqs)
    else:
        storage = impl.YDBStorage()
        impl.install_ydb_storage(core, storage)
        impl.install_contact_verification(core)
        install_deterministic_leads(core, storage)
        handler = create_worker_handler(impl, core, storage)

    print(f"MAX Yandex runtime starting on {HOST}:{PORT}; mode={MODE}", flush=True)
    print(f"MAX API: {core.MAX_BASE}", flush=True)
    ThreadingHTTPServer((HOST, PORT), handler).serve_forever()


if __name__ == "__main__":
    main()
