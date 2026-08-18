#!/usr/bin/env python3
"""Safe entrypoint for the production MAX bot.

Purpose:
- keep secrets out of Git;
- support hosting panels that inject the MAX token as BOT_TOKEN;
- derive a valid webhook secret from the bot token when a custom secret is not available;
- add lightweight idempotency for repeated webhook deliveries;
- then run the approved maxbot-selfhosted.py implementation unchanged.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import threading
from collections import deque
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CORE_PATH = BASE_DIR / "maxbot-selfhosted.py"


def prepare_environment() -> None:
    # Bothost examples for MAX use BOT_TOKEN. Our core uses MAX_BOT_TOKEN.
    if not os.environ.get("MAX_BOT_TOKEN") and os.environ.get("BOT_TOKEN"):
        os.environ["MAX_BOT_TOKEN"] = os.environ["BOT_TOKEN"]

    token = os.environ.get("MAX_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "MAX bot token is missing. Set MAX_BOT_TOKEN or use the hosting panel Bot Token field."
        )

    # MAX allows a webhook secret of 5-256 chars matching [A-Za-z0-9_-].
    # Deriving it means a free hosting plan does not need a second custom secret field.
    # A manually supplied MAX_WEBHOOK_SECRET always takes precedence.
    if not os.environ.get("MAX_WEBHOOK_SECRET"):
        derived = hashlib.sha256(("maximum-webhook-v1:" + token).encode("utf-8")).hexdigest()
        os.environ["MAX_WEBHOOK_SECRET"] = derived

    # Keep SQLite in the hosting data directory by default.
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


def install_idempotency(core) -> None:
    """Ignore obvious duplicate webhook events in the lifetime of one container.

    MAX retries failed webhook deliveries. The HTTP handler returns 200 quickly, but
    a network retry can still produce a duplicate. We prefer a small in-memory guard
    to duplicate client replies or duplicate lead records.
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

        # System events do not always have a message id. timestamp + chat/user is the
        # best stable tuple exposed by Update for these event types.
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


def main() -> None:
    prepare_environment()
    core = load_core()
    install_idempotency(core)
    core.main()


if __name__ == "__main__":
    main()
