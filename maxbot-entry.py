#!/usr/bin/env python3
"""Safe entrypoint for the production MAX bot.

Purpose:
- keep secrets out of Git;
- support hosting panels that inject the MAX token as BOT_TOKEN;
- derive a valid webhook secret from the bot token when a custom secret is not available;
- normalize and verify MAX request_contact payloads defensively;
- add lightweight idempotency for repeated webhook deliveries;
- expose a non-secret /ready check for MAX API + webhook status;
- then run the approved maxbot-selfhosted.py implementation.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import re
import threading
from collections import deque
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CORE_PATH = BASE_DIR / "maxbot-selfhosted.py"


def prepare_environment() -> None:
    # Some hosting panels expose the bot token as BOT_TOKEN. Our core uses MAX_BOT_TOKEN.
    if not os.environ.get("MAX_BOT_TOKEN") and os.environ.get("BOT_TOKEN"):
        os.environ["MAX_BOT_TOKEN"] = os.environ["BOT_TOKEN"]

    token = os.environ.get("MAX_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "MAX bot token is missing. Set MAX_BOT_TOKEN or use the hosting panel Bot Token field."
        )

    # MAX accepts a webhook secret matching [A-Za-z0-9_-]{5,256}.
    # Deriving it lets a restrictive free hosting plan work with only the Bot Token field.
    # A manually supplied MAX_WEBHOOK_SECRET always takes precedence.
    if not os.environ.get("MAX_WEBHOOK_SECRET"):
        derived = hashlib.sha256(("maximum-webhook-v1:" + token).encode("utf-8")).hexdigest()
        os.environ["MAX_WEBHOOK_SECRET"] = derived

    os.environ.setdefault("DATA_DIR", "/app/data")
    os.environ.setdefault("DATABASE_PATH", "/app/data/maxbot.db")
    os.environ.setdefault("MAX_AUTO_SUBSCRIBE", "1")


def load_core():
    spec = importlib.util.spec_from_file_location("maximum_maxbot_core", CORE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load MAX bot core: {CORE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install_contact_verification(core) -> None:
    """Use MAX's documented HMAC-SHA256 verification for request_contact.

    Depending on the JSON/client path, vcf_info may already contain real CRLF
    characters or may still contain literal ``\\r\\n`` sequences. We normalize the
    latter for parsing and accept a signature only when it matches the exact raw or
    documented-normalized VCF payload using the current bot access token.
    """

    def parse_contact_attachment(message):
        body = message.get("body") or {}
        for item in body.get("attachments") or []:
            if item.get("type") != "contact":
                continue

            payload = item.get("payload") or {}
            raw_vcf = str(payload.get("vcf_info") or "")
            claimed_hash = str(payload.get("hash") or "")

            normalized_vcf = raw_vcf.replace("\\r\\n", "\r\n")
            normalized_vcf = normalized_vcf.replace("\\n", "\n")

            phone = ""
            match = re.search(r"(?im)^TEL(?:;[^:]*)?:(.+)$", normalized_vcf)
            if match:
                phone = core.normalize_phone(match.group(1))

            verified = False
            if raw_vcf and claimed_hash:
                candidates = [raw_vcf]
                if normalized_vcf != raw_vcf:
                    candidates.append(normalized_vcf)
                for candidate in candidates:
                    calculated = hmac.new(
                        core.MAX_TOKEN.encode("utf-8"),
                        candidate.encode("utf-8"),
                        hashlib.sha256,
                    ).hexdigest()
                    if hmac.compare_digest(calculated.lower(), claimed_hash.lower()):
                        verified = True
                        break

            return phone, verified
        return "", False

    core.parse_contact_attachment = parse_contact_attachment


def install_idempotency(core) -> None:
    """Ignore obvious duplicate webhook events in the lifetime of one container.

    MAX retries failed webhook deliveries. The HTTP handler returns 200 quickly, but
    a network retry can still produce a duplicate. This guard prevents duplicate
    client replies and duplicate lead records during the current process lifetime.
    """

    seen = deque(maxlen=5000)
    seen_set: set[str] = set()
    lock = threading.Lock()

    def event_key(update: dict) -> str:
        update_type = str(update.get("update_type") or "")
        message = update.get("message") or {}
        body = message.get("body") or {}
        callback = update.get("callback") or {}

        stable = (
            callback.get("callback_id")
            or body.get("mid")
            or message.get("mid")
            or update.get("message_id")
        )
        if stable:
            return f"{update_type}:{stable}"

        chat_id = update.get("chat_id") or (message.get("recipient") or {}).get("chat_id") or ""
        user_id = (update.get("user") or {}).get("user_id") or (message.get("sender") or {}).get("user_id") or ""
        timestamp = update.get("timestamp") or ""
        return f"{update_type}:{chat_id}:{user_id}:{timestamp}"

    def first_time(update: dict) -> bool:
        key = event_key(update)
        with lock:
            if key in seen_set:
                return False
            if len(seen) == seen.maxlen:
                old = seen.popleft()
                seen_set.discard(old)
            seen.append(key)
            seen_set.add(key)
            return True

    def worker() -> None:
        while True:
            update = core.work_queue.get()
            try:
                if first_time(update):
                    core.handle_update(update)
                else:
                    print("duplicate webhook ignored", event_key(update), flush=True)
            except Exception as exc:
                print("update error:", repr(exc), flush=True)
            finally:
                core.work_queue.task_done()

    core.worker = worker


def install_readiness(core) -> None:
    """Expose a safe readiness check without leaking bot/account identifiers.

    /health answers whether the local process is alive.
    /ready additionally verifies that MAX accepts the token and that the exact
    configured webhook URL is present in GET /subscriptions.
    """

    base_handler = core.Handler

    class ReadyHandler(base_handler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path != "/ready":
                return super().do_GET()

            public_base = (core.PUBLIC_BASE_URL or "").rstrip("/")
            target = public_base + "/webhook" if public_base else ""
            api_ok = False
            webhook_ok = False

            try:
                status, result = core.http_json(f"{core.MAX_BASE}/me", method="GET", timeout=15)
                api_ok = 200 <= status < 300 and isinstance(result, dict)
            except Exception:
                api_ok = False

            if api_ok and target:
                try:
                    status, result = core.subscriptions()
                    if 200 <= status < 300:
                        webhook_ok = any(
                            isinstance(item, dict) and item.get("url") == target
                            for item in (result.get("subscriptions") or [])
                        )
                except Exception:
                    webhook_ok = False

            ready = api_ok and bool(target) and webhook_ok
            payload = json.dumps(
                {
                    "ok": ready,
                    "max_api": api_ok,
                    "webhook": webhook_ok,
                    "public_url_configured": bool(target),
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.respond(200 if ready else 503, payload, "application/json; charset=utf-8")

    core.Handler = ReadyHandler


def main() -> None:
    prepare_environment()
    core = load_core()
    install_contact_verification(core)
    install_idempotency(core)
    install_readiness(core)
    core.main()


if __name__ == "__main__":
    main()
