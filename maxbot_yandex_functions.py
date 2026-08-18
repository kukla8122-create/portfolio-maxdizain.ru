#!/usr/bin/env python3
"""Yandex Cloud Functions runtime for «МАКСимум мебель» MAX bot.

Production path:
    MAX HTTPS webhook -> public ingress function -> YDB Topic/Data Streams ->
    Data Streams trigger -> private worker function -> YDB + MAX Bot API.

Design goals:
- no Docker / Container Registry dependency;
- no paid Lockbox dependency;
- ingress never receives the MAX bot token;
- webhook secret is verified before accepting an update;
- the ingress waits for a YDB topic write acknowledgement before returning HTTP 200;
- worker processing is idempotent for a retried MAX update;
- GET health checks are read-only and cannot modify MAX subscriptions.
"""

from __future__ import annotations

import base64
import contextvars
import hashlib
import hmac
import importlib.util
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import ydb
import ydb.iam

BASE_DIR = Path(__file__).resolve().parent
CORE_WRAPPER_PATH = BASE_DIR / "maxbot-selfhosted-yandex.py"
MAX_UPDATE_STREAM_LIMIT = int(os.environ.get("MAX_UPDATE_STREAM_LIMIT", "900000"))
YDS_TOPIC = os.environ.get("YDS_TOPIC", "maximum-maxbot-events").strip()
CURRENT_EVENT_KEY: contextvars.ContextVar[str] = contextvars.ContextVar(
    "maximum_max_event_key", default=""
)

_ingress_lock = threading.RLock()
_ingress_driver = None
_ingress_writer = None
_worker_lock = threading.RLock()
_worker_core = None
_worker_storage = None


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _response(status: int, body: str, content_type: str = "text/plain; charset=utf-8") -> dict:
    return {
        "statusCode": int(status),
        "headers": {"Content-Type": content_type},
        "body": body,
        "isBase64Encoded": False,
    }


def _json_response(status: int, obj: dict) -> dict:
    return _response(
        status,
        json.dumps(obj, ensure_ascii=False, separators=(",", ":")),
        "application/json; charset=utf-8",
    )


def _normalized_headers(event: dict) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in (event.get("headers") or {}).items():
        result[str(key).lower()] = str(value)
    return result


def _request_body(event: dict) -> bytes:
    raw = event.get("body", "")
    if raw is None:
        raw = ""
    if not isinstance(raw, str):
        raise ValueError("body must be a string")
    if event.get("isBase64Encoded"):
        try:
            return base64.b64decode(raw, validate=True)
        except Exception as exc:
            raise ValueError("invalid base64 body") from exc
    return raw.encode("utf-8")


def _ydb_connection_string() -> str:
    return _require("YDB_CONNECTION_STRING")


def _new_ydb_driver():
    driver = ydb.Driver(
        connection_string=_ydb_connection_string(),
        credentials=ydb.iam.MetadataUrlCredentials(),
        root_certificates=ydb.load_ydb_root_certificate(),
    )
    driver.wait(fail_fast=True, timeout=10)
    return driver


def _get_ingress_writer():
    global _ingress_driver, _ingress_writer
    if _ingress_writer is not None:
        return _ingress_writer
    with _ingress_lock:
        if _ingress_writer is not None:
            return _ingress_writer
        if not YDS_TOPIC:
            raise RuntimeError("YDS_TOPIC is required")
        _ingress_driver = _new_ydb_driver()
        _ingress_writer = _ingress_driver.topic_client.writer(YDS_TOPIC)
        return _ingress_writer


def ingress_handler(event: dict, context: Any) -> dict:
    """Public HTTPS MAX webhook.

    Cloud Functions converts HTTPS requests into an event with httpMethod, headers,
    body and isBase64Encoded. Only POST is accepted for business delivery. GET is a
    read-only health endpoint and never touches MAX or the stream.
    """

    method = str((event or {}).get("httpMethod") or "").upper()
    if method == "GET":
        return _json_response(
            200,
            {
                "ok": True,
                "service": "maximum-max-bot",
                "platform": "yandex-cloud-functions",
                "mode": "ingress",
                "transport": "data-streams",
                "read_only": True,
                "max_token_present": bool(os.environ.get("MAX_BOT_TOKEN")),
            },
        )
    if method != "POST":
        return _response(405, "Method not allowed")

    expected = _require("MAX_WEBHOOK_SECRET")
    if not re.fullmatch(r"[A-Za-z0-9_-]{5,256}", expected):
        raise RuntimeError("MAX_WEBHOOK_SECRET has invalid format")
    got = _normalized_headers(event).get("x-max-bot-api-secret", "")
    if not hmac.compare_digest(got, expected):
        return _response(403, "Forbidden")

    try:
        raw = _request_body(event)
    except ValueError:
        return _response(400, "Bad body")
    if not raw or len(raw) > MAX_UPDATE_STREAM_LIMIT:
        return _response(413, "Invalid body size")

    try:
        update = json.loads(raw.decode("utf-8"))
    except Exception:
        return _response(400, "Bad JSON")
    if not isinstance(update, dict) or not update.get("update_type"):
        return _response(400, "Bad update")

    # The Python YDB SDK's writer.write() only queues data in a client-side buffer.
    # Cloud Functions may freeze the runtime immediately after return, so production
    # waits for the server acknowledgement before telling MAX HTTP 200.
    try:
        _get_ingress_writer().write_with_ack(raw.decode("utf-8"))
    except Exception as exc:
        print("Data Streams write_with_ack failed:", repr(exc), flush=True)
        return _response(503, "Stream unavailable")

    return _response(200, "OK")


def _row_get(row, name, default=None):
    try:
        return row[name]
    except Exception:
        return getattr(row, name, default)


class YDBStorage:
    """YDB persistence adapter for sessions, leads, channels and update deduplication."""

    def __init__(self):
        self._driver = None
        self._pool = None
        self._lock = threading.RLock()
        self._schema_ready = False

    def pool(self):
        if self._pool is not None:
            return self._pool
        with self._lock:
            if self._pool is not None:
                return self._pool
            self._driver = _new_ydb_driver()
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
            for statement in (
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    chat_id Utf8, flow Utf8, step Utf8, data_json Utf8,
                    updated_at Int64, PRIMARY KEY (chat_id)
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS leads (
                    id Utf8, created_at Int64, chat_id Utf8, user_id Utf8,
                    kind Utf8, name Utf8, city Utf8, details Utf8, phone Utf8,
                    phone_verified Bool, PRIMARY KEY (id)
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS channels (
                    chat_id Utf8, is_channel Bool, added_at Int64,
                    added_by_user_id Utf8, PRIMARY KEY (chat_id)
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key Utf8, value Utf8, PRIMARY KEY (key)
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id Utf8, processed_at Int64, PRIMARY KEY (event_id)
                );
                """,
            ):
                self.pool().execute_with_retries(statement)
            self._schema_ready = True

    def set_session(self, chat_id, flow, step, data=None):
        self.init_schema()
        self.execute(
            """
            DECLARE $chat_id AS Utf8; DECLARE $flow AS Utf8; DECLARE $step AS Utf8;
            DECLARE $data_json AS Utf8; DECLARE $updated_at AS Int64;
            UPSERT INTO sessions (chat_id,flow,step,data_json,updated_at)
            VALUES ($chat_id,$flow,$step,$data_json,$updated_at);
            """,
            {
                "$chat_id": str(chat_id),
                "$flow": str(flow),
                "$step": str(step),
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
            SELECT flow,step,data_json FROM sessions WHERE chat_id=$chat_id LIMIT 1;
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
            "DECLARE $chat_id AS Utf8; DELETE FROM sessions WHERE chat_id=$chat_id;",
            {"$chat_id": str(chat_id)},
            idempotent=True,
        )

    def save_channel(self, chat_id, is_channel, user_id=None):
        if chat_id is None:
            return
        self.init_schema()
        self.execute(
            """
            DECLARE $chat_id AS Utf8; DECLARE $is_channel AS Bool;
            DECLARE $added_at AS Int64; DECLARE $added_by AS Utf8;
            UPSERT INTO channels (chat_id,is_channel,added_at,added_by_user_id)
            VALUES ($chat_id,$is_channel,$added_at,$added_by);
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
        return _save_lead_idempotent(self, chat_id, user_id, kind, data, phone, verified)

    def event_processed(self, event_id: str) -> bool:
        if not event_id:
            return False
        self.init_schema()
        result = self.execute(
            """
            DECLARE $event_id AS Utf8;
            SELECT event_id FROM processed_events WHERE event_id=$event_id LIMIT 1;
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
            DECLARE $event_id AS Utf8; DECLARE $processed_at AS Int64;
            UPSERT INTO processed_events (event_id,processed_at)
            VALUES ($event_id,$processed_at);
            """,
            {"$event_id": event_id, "$processed_at": int(time.time())},
            idempotent=True,
        )

    def remove_channel(self, chat_id):
        if chat_id is None:
            return
        self.init_schema()
        self.execute(
            "DECLARE $chat_id AS Utf8; DELETE FROM channels WHERE chat_id=$chat_id;",
            {"$chat_id": str(chat_id)},
            idempotent=True,
        )


def stable_update_key(update: dict) -> str:
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


def _save_lead_idempotent(storage, chat_id, user_id, kind, data, phone="", verified=False):
    storage.init_schema()
    event_key = CURRENT_EVENT_KEY.get()
    if event_key:
        material = f"{event_key}|{chat_id}|{kind}".encode("utf-8")
        lead_id = "lead-" + hashlib.sha256(material).hexdigest()[:40]
    else:
        lead_id = "lead-" + uuid.uuid4().hex

    existing = storage.execute(
        "DECLARE $id AS Utf8; SELECT id FROM leads WHERE id=$id LIMIT 1;",
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
        DECLARE $id AS Utf8; DECLARE $created_at AS Int64;
        DECLARE $chat_id AS Utf8; DECLARE $user_id AS Utf8;
        DECLARE $kind AS Utf8; DECLARE $name AS Utf8; DECLARE $city AS Utf8;
        DECLARE $details AS Utf8; DECLARE $phone AS Utf8; DECLARE $verified AS Bool;
        UPSERT INTO leads (id,created_at,chat_id,user_id,kind,name,city,details,phone,phone_verified)
        VALUES ($id,$created_at,$chat_id,$user_id,$kind,$name,$city,$details,$phone,$verified);
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


def _install_contact_verification(core):
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


def _configure_max_tls():
    """Combine system roots with the additional Russian trusted CA bundle."""
    extra = BASE_DIR / "max-russian-ca.pem"
    system = Path("/etc/ssl/certs/ca-certificates.crt")
    if not extra.exists() or not system.exists():
        return
    target = Path("/tmp/maximum-maxbot-ca.pem")
    if not target.exists():
        target.write_bytes(system.read_bytes() + b"\n" + extra.read_bytes())
    os.environ["SSL_CERT_FILE"] = str(target)


def _load_core_wrapper():
    if not CORE_WRAPPER_PATH.exists():
        raise RuntimeError(f"Missing core wrapper: {CORE_WRAPPER_PATH}")
    spec = importlib.util.spec_from_file_location("maximum_maxbot_function_core", CORE_WRAPPER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load MAX core wrapper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _worker_runtime():
    global _worker_core, _worker_storage
    if _worker_core is not None and _worker_storage is not None:
        return _worker_core, _worker_storage
    with _worker_lock:
        if _worker_core is not None and _worker_storage is not None:
            return _worker_core, _worker_storage

        _require("MAX_BOT_TOKEN")
        _require("MAX_WEBHOOK_SECRET")
        _configure_max_tls()
        os.environ.setdefault("DATA_DIR", "/tmp/maxbot")
        os.environ.setdefault("DATABASE_PATH", "/tmp/maxbot/maxbot-unused.db")
        os.environ["MAX_AUTO_SUBSCRIBE"] = "0"

        core = _load_core_wrapper()
        storage = YDBStorage()
        core.init_db = storage.init_schema
        core.set_session = storage.set_session
        core.get_session = storage.get_session
        core.clear_session = storage.clear_session
        core.save_channel = storage.save_channel
        core.save_lead = storage.save_lead
        _install_contact_verification(core)
        _worker_core = core
        _worker_storage = storage
        return core, storage


def extract_stream_updates(event: dict):
    """Yield JSON objects delivered by a Yandex Data Streams function trigger."""
    messages = (event or {}).get("messages") or []
    if not isinstance(messages, list):
        raise ValueError("messages must be a list")
    for item in messages:
        if isinstance(item, dict) and item.get("update_type"):
            yield item
        else:
            raise ValueError("Data Streams trigger message is not a MAX JSON update")


def worker_handler(event: dict, context: Any):
    """Private Data Streams trigger target. Raise on failure so Yandex retries/DLQs."""
    core, storage = _worker_runtime()
    processed = 0
    duplicates = 0
    for update in extract_stream_updates(event):
        event_key = stable_update_key(update)
        if storage.event_processed(event_key):
            duplicates += 1
            continue

        token = CURRENT_EVENT_KEY.set(event_key)
        try:
            if update.get("update_type") == "bot_removed":
                storage.remove_channel(update.get("chat_id"))
            core.handle_update(update)
            storage.mark_event_processed(event_key)
            processed += 1
        finally:
            CURRENT_EVENT_KEY.reset(token)

    return {"ok": True, "processed": processed, "duplicates": duplicates}
