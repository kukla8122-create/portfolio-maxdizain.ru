import http.client
import importlib.util
import json
import os
import threading
import unittest
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_split():
    spec = importlib.util.spec_from_file_location(
        "maxbot_yandex_split_test", ROOT / "maxbot-yandex-split.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def request_once(handler_cls, method, path, body=b"", headers=None):
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        hdrs = dict(headers or {})
        if body:
            hdrs.setdefault("Content-Length", str(len(body)))
        conn.request(method, path, body=body, headers=hdrs)
        response = conn.getresponse()
        data = response.read()
        status = response.status
        conn.close()
        return status, data
    finally:
        thread.join(timeout=5)
        server.server_close()


class FakeIngressImpl:
    YMQ_QUEUE_URL = "https://queue.example/events"

    @staticmethod
    def public_base_from_request(_handler):
        return "https://ingress.example"


class FakeCore:
    WEBHOOK_SECRET = "WebhookSecret_12345"
    MAX_BASE = "https://platform-api2.max.ru"

    def __init__(self):
        self.http_calls = []
        self.subscription_reads = 0
        self.handled = []

    def http_json(self, url, method="GET", obj=None, timeout=None):
        self.http_calls.append((method, url, obj))
        return 200, {"user_id": 1}

    def subscriptions(self):
        self.subscription_reads += 1
        return 200, {"subscriptions": []}

    def handle_update(self, update):
        self.handled.append(update)


class FakeSQS:
    def __init__(self):
        self.sent = []

    def get_queue_attributes(self, **_kwargs):
        return {"Attributes": {"QueueArn": "arn:yc:ymq:test"}}

    def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return {"MessageId": "1"}


class FakeStorage:
    def __init__(self, already_processed=False):
        self.already_processed = already_processed
        self.marked = []

    def event_processed(self, _event_id):
        return self.already_processed

    def mark_event_processed(self, event_id):
        self.marked.append(event_id)

    def status(self):
        return {"ok": True}


class FakeWorkerImpl:
    @staticmethod
    def extract_trigger_updates(_event):
        yield "trigger-event-1", {
            "update_type": "message_created",
            "message": {"body": {"mid": "mid-1"}},
        }


class YandexSplitTests(unittest.TestCase):
    def setUp(self):
        self.old_activate = os.environ.get("MAX_ACTIVATE_WEBHOOK")
        self.split = load_split()

    def tearDown(self):
        if self.old_activate is None:
            os.environ.pop("MAX_ACTIVATE_WEBHOOK", None)
        else:
            os.environ["MAX_ACTIVATE_WEBHOOK"] = self.old_activate

    def test_default_queue_limit_has_trigger_safety_margin(self):
        self.assertEqual(self.split.MAX_UPDATE_QUEUE_LIMIT, 200000)
        self.assertLess(self.split.MAX_UPDATE_QUEUE_LIMIT, 230 * 1024)

    def test_stable_update_key_is_identical_for_duplicate_update(self):
        update = {
            "update_type": "message_callback",
            "callback": {"callback_id": "cb-123"},
        }
        self.assertEqual(
            self.split.stable_update_key(update),
            self.split.stable_update_key(json.loads(json.dumps(update))),
        )

    def test_ready_is_read_only_even_if_stale_activation_flag_is_one(self):
        os.environ["MAX_ACTIVATE_WEBHOOK"] = "1"
        core = FakeCore()
        sqs = FakeSQS()
        handler = self.split.create_ingress_handler(FakeIngressImpl(), core, sqs)

        status, raw = request_once(handler, "GET", "/ready")
        payload = json.loads(raw.decode("utf-8"))

        self.assertEqual(status, 200)
        self.assertTrue(payload["read_only"])
        self.assertFalse(payload["activation_enabled"])
        self.assertTrue(payload["activation_requested"])
        self.assertEqual(core.subscription_reads, 1)
        self.assertTrue(core.http_calls)
        self.assertTrue(all(method == "GET" for method, _url, _obj in core.http_calls))

    def test_webhook_rejects_wrong_secret_without_queue_write(self):
        core = FakeCore()
        sqs = FakeSQS()
        handler = self.split.create_ingress_handler(FakeIngressImpl(), core, sqs)
        body = json.dumps({"update_type": "bot_started"}).encode()

        status, _ = request_once(
            handler,
            "POST",
            "/webhook",
            body,
            {"X-Max-Bot-Api-Secret": "wrong"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(sqs.sent, [])

    def test_webhook_accepts_valid_secret_and_enqueues_exactly_once(self):
        core = FakeCore()
        sqs = FakeSQS()
        handler = self.split.create_ingress_handler(FakeIngressImpl(), core, sqs)
        update = {"update_type": "bot_started", "chat_id": 123}
        body = json.dumps(update).encode()

        status, _ = request_once(
            handler,
            "POST",
            "/webhook",
            body,
            {"X-Max-Bot-Api-Secret": core.WEBHOOK_SECRET},
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(sqs.sent), 1)
        self.assertEqual(sqs.sent[0]["MessageBody"], body.decode())

    def test_webhook_rejects_payload_above_queue_safety_limit(self):
        core = FakeCore()
        sqs = FakeSQS()
        handler = self.split.create_ingress_handler(FakeIngressImpl(), core, sqs)
        body = b"x" * (self.split.MAX_UPDATE_QUEUE_LIMIT + 1)

        status, _ = request_once(
            handler,
            "POST",
            "/webhook",
            body,
            {"X-Max-Bot-Api-Secret": core.WEBHOOK_SECRET},
        )
        self.assertEqual(status, 413)
        self.assertEqual(sqs.sent, [])

    def test_ingress_does_not_expose_worker_trigger_route(self):
        handler = self.split.create_ingress_handler(
            FakeIngressImpl(), FakeCore(), FakeSQS()
        )
        status, _ = request_once(handler, "POST", "/trigger", b"{}")
        self.assertEqual(status, 404)

    def test_worker_does_not_expose_public_webhook_route(self):
        handler = self.split.create_worker_handler(
            FakeWorkerImpl(), FakeCore(), FakeStorage()
        )
        status, _ = request_once(handler, "POST", "/webhook", b"{}")
        self.assertEqual(status, 404)

    def test_worker_ignores_duplicate_update(self):
        core = FakeCore()
        storage = FakeStorage(already_processed=True)
        handler = self.split.create_worker_handler(FakeWorkerImpl(), core, storage)
        body = json.dumps({"messages": [{}]}).encode()

        status, _ = request_once(handler, "POST", "/trigger", body)
        self.assertEqual(status, 200)
        self.assertEqual(core.handled, [])
        self.assertEqual(storage.marked, [])

    def test_worker_marks_successful_update_processed(self):
        core = FakeCore()
        storage = FakeStorage(already_processed=False)
        handler = self.split.create_worker_handler(FakeWorkerImpl(), core, storage)
        body = json.dumps({"messages": [{}]}).encode()

        status, _ = request_once(handler, "POST", "/trigger", body)
        self.assertEqual(status, 200)
        self.assertEqual(len(core.handled), 1)
        self.assertEqual(storage.marked, ["max:message_created:message:mid-1"])


if __name__ == "__main__":
    unittest.main()
