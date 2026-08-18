#!/usr/bin/env python3
"""Safe production gate for the Yandex Serverless MAX bot.

This wrapper intentionally separates infrastructure readiness from MAX webhook
activation. A GET /ready must never switch the bot from Long Polling to Webhook
unless MAX_ACTIVATE_WEBHOOK=1 is explicitly set in the active container revision.

Initial rollout:
    MAX_ACTIVATE_WEBHOOK=0
    /health -> local container
    /storage -> YDB
    /ready -> MAX API + YDB + YMQ + public URL; no subscription mutation

Cutover only after preflight:
    create a new revision with MAX_ACTIVATE_WEBHOOK=1
    /ready -> verifies/creates the exact MAX webhook subscription
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
IMPL_PATH = BASE_DIR / "maxbot-yandex.py"


def env_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def load_impl():
    spec = importlib.util.spec_from_file_location("maximum_maxbot_yandex_impl", IMPL_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load Yandex MAX bot implementation: {IMPL_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_safe_handler(impl, core, storage, sqs):
    base_handler = impl.create_handler(core, storage, sqs)

    class SafeHandler(base_handler):
        server_version = "MaximumFurnitureBotYandex/1.1"

        def _ready(self):
            base = impl.public_base_from_request(self)
            target = base + "/webhook" if base else ""
            activation_enabled = env_true("MAX_ACTIVATE_WEBHOOK")

            max_api = False
            storage_ok = False
            queue_ok = False
            webhook_ok = False

            try:
                status, _ = core.http_json(f"{core.MAX_BASE}/me", timeout=12)
                max_api = 200 <= status < 300
            except Exception as exc:
                print("MAX /me readiness error:", repr(exc), flush=True)

            try:
                storage_ok = bool(storage.status().get("ok"))
            except Exception as exc:
                print("YDB readiness error:", repr(exc), flush=True)

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
                except Exception as exc:
                    print("MAX webhook check error:", repr(exc), flush=True)

            infrastructure_ok = (
                max_api and storage_ok and queue_ok and bool(target)
            )

            # This is the only mutation point. It is impossible to subscribe merely
            # by visiting /ready while MAX_ACTIVATE_WEBHOOK remains at its safe
            # default (false).
            if activation_enabled and infrastructure_ok and not webhook_ok:
                try:
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
                    print("MAX webhook activation error:", repr(exc), flush=True)

            # During preflight, an intentionally inactive webhook is not a failure.
            # Once activation is enabled, readiness requires the webhook to exist.
            ready = infrastructure_ok and (
                webhook_ok if activation_enabled else True
            )

            self.json_response(
                200 if ready else 503,
                {
                    "ok": ready,
                    "infrastructure": infrastructure_ok,
                    "max_api": max_api,
                    "queue": queue_ok,
                    "storage": storage_ok,
                    "webhook": webhook_ok,
                    "activation_enabled": activation_enabled,
                    "public_url_configured": bool(target),
                },
            )

    return SafeHandler


def main():
    impl = load_impl()
    impl.prepare_environment()
    core = impl.load_core()
    storage = impl.YDBStorage()
    impl.install_ydb_storage(core, storage)
    impl.install_contact_verification(core)
    sqs = impl.build_sqs_client()
    handler = create_safe_handler(impl, core, storage, sqs)

    print(
        f"MAX Yandex production adapter starting on {impl.HOST}:{impl.PORT}; "
        f"webhook_activation={env_true('MAX_ACTIVATE_WEBHOOK')}",
        flush=True,
    )
    print(f"MAX API: {core.MAX_BASE}", flush=True)
    impl.ThreadingHTTPServer((impl.HOST, impl.PORT), handler).serve_forever()


if __name__ == "__main__":
    main()
