import http.client
import importlib.util
import json
import os
import threading
import unittest
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_stream_runtime():
    os.environ["YDS_STREAM_NAME"] = "/ru-central1/folder/database/maximum-maxbot-events"
    spec = importlib.util.spec_from_file_location(
        "maxbot_yandex_stream_test", ROOT / "maxbot-yandex-stream.py"
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


class FakeImpl:
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


class FakeKinesis:
    def __init__(self, stream_status="ACTIVE"):
        self.records = []
        self.describe_calls = []
        self.stream_status = stream_status

    def put_record(self, **kwargs):
        self.records.append(kwargs)
        return {"ShardId": "shard-000000", "SequenceNumber": str(len(self.records))}

    def describe_stream(self, **kwargs):
        self.describe_calls.append(kwargs)
        return {
            "StreamDescription": {
                "StreamStatus": self.stream_status,
                "Shards": [{"ShardId": "shard-000000"}],
            }
        }


class FakeStorage:
    def __init__(self, processed=None):
        self.processed = set(processed or [])
        self.marked = []
        self.queries = []
        self.schema_calls = 0

    def init_schema(self):
        self.schema_calls += 1

    def event_processed(self, event_id):
        return event_id in self.processed

    def mark_event_processed(self, event_id):
        self.marked.append(event_id)
        self.processed.add(event_id)

    def execute(self, query, params=None, **kwargs):
        self.queries.append((query, params or {}, kwargs))
        return []

    def status(self):
        return {"ok": True}


class OrderedDataStreamsTests(unittest.TestCase):
    def setUp(self):
        self.stream = load_stream_runtime()

    def test_partition_key_prefers_chat_id(self):
        self.assertEqual(
            self.stream.event_partition_key(
                {
                    "update_type": "message_created",
                    "message": {"recipient": {"chat_id": 321, "chat_type": "dialog"}},
                }
            ),
            "chat:321",
        )

    def test_partition_key_uses_callback_message_chat(self):
        self.assertEqual(
            self.stream.event_partition_key(
                {
                    "update_type": "message_callback",
                    "callback": {
                        "message": {"recipient": {"chat_id": 99, "chat_type": "dialog"}}
                    },
                }
            ),
            "chat:99",
        )

    def test_webhook_writes_exact_json_to_data_stream(self):
        core = FakeCore()
        kinesis = FakeKinesis()
        handler = self.stream.create_ingress_handler(FakeImpl(), core, kinesis)
        update = {
            "update_type": "message_created",
            "chat_id": 42,
            "message": {"recipient": {"chat_id": 42, "chat_type": "dialog"}},
        }
        body = json.dumps(update).encode()

        status, _ = request_once(
            handler,
            "POST",
            "/webhook",
            body,
            {"X-Max-Bot-Api-Secret": core.WEBHOOK_SECRET},
        )

        self.assertEqual(status, 200)
        self.assertEqual(len(kinesis.records), 1)
        self.assertEqual(kinesis.records[0]["Data"], body)
        self.assertEqual(kinesis.records[0]["PartitionKey"], "chat:42")
        self.assertEqual(kinesis.records[0]["StreamName"], self.stream.YDS_STREAM_NAME)

    def test_webhook_wrong_secret_never_writes_stream(self):
        core = FakeCore()
        kinesis = FakeKinesis()
        handler = self.stream.create_ingress_handler(FakeImpl(), core, kinesis)
        body = b'{"update_type":"bot_started","chat_id":42}'

        status, _ = request_once(
            handler,
            "POST",
            "/webhook",
            body,
            {"X-Max-Bot-Api-Secret": "wrong"},
        )

        self.assertEqual(status, 403)
        self.assertEqual(kinesis.records, [])

    def test_webhook_rejects_record_above_stream_safety_limit(self):
        core = FakeCore()
        kinesis = FakeKinesis()
        handler = self.stream.create_ingress_handler(FakeImpl(), core, kinesis)

        # The handler rejects an oversized Content-Length before reading the body.
        # Sending an actually huge body can race the early 413 response and produce
        # BrokenPipeError in http.client on fast CI runners. A small body with the
        # deliberately oversized header tests the exact server guard deterministically.
        body = b"x"
        headers = {
            "X-Max-Bot-Api-Secret": core.WEBHOOK_SECRET,
            "Content-Length": str(self.stream.MAX_UPDATE_STREAM_LIMIT + 1),
        }
        status, _ = request_once(handler, "POST", "/webhook", body, headers)

        self.assertEqual(status, 413)
        self.assertEqual(kinesis.records, [])

    def test_ready_verifies_active_stream_read_only(self):
        core = FakeCore()
        kinesis = FakeKinesis(stream_status="ACTIVE")
        handler = self.stream.create_ingress_handler(FakeImpl(), core, kinesis)

        status, raw = request_once(handler, "GET", "/ready")
        payload = json.loads(raw.decode())

        self.assertEqual(status, 200)
        self.assertTrue(payload["read_only"])
        self.assertEqual(payload["transport"], "data-streams")
        self.assertTrue(payload["stream_configured"])
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["stream_status"], "ACTIVE")
        self.assertFalse(payload["activation_enabled"])
        self.assertEqual(kinesis.records, [])
        self.assertEqual(
            kinesis.describe_calls,
            [{"StreamName": self.stream.YDS_STREAM_NAME, "Limit": 1}],
        )
        self.assertTrue(all(method == "GET" for method, _url, _obj in core.http_calls))

    def test_ready_fails_if_stream_is_not_active(self):
        core = FakeCore()
        kinesis = FakeKinesis(stream_status="CREATING")
        handler = self.stream.create_ingress_handler(FakeImpl(), core, kinesis)

        status, raw = request_once(handler, "GET", "/ready")
        payload = json.loads(raw.decode())

        self.assertEqual(status, 503)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["stream_status"], "CREATING")
        self.assertEqual(kinesis.records, [])

    def test_documented_trigger_messages_are_processed_in_array_order(self):
        core = FakeCore()
        storage = FakeStorage()
        handler = self.stream.create_worker_handler(FakeImpl(), core, storage)
        first = {
            "update_type": "message_created",
            "message": {"body": {"mid": "mid-1"}},
        }
        second = {
            "update_type": "message_created",
            "message": {"body": {"mid": "mid-2"}},
        }
        body = json.dumps({"messages": [first, second]}).encode()

        status, _ = request_once(handler, "POST", "/trigger", body)

        self.assertEqual(status, 200)
        self.assertEqual(core.handled, [first, second])
        self.assertEqual(
            storage.marked,
            ["max:message_created:message:mid-1", "max:message_created:message:mid-2"],
        )

    def test_duplicate_stream_record_is_ignored(self):
        key = "max:message_created:message:mid-1"
        core = FakeCore()
        storage = FakeStorage(processed={key})
        handler = self.stream.create_worker_handler(FakeImpl(), core, storage)
        update = {
            "update_type": "message_created",
            "message": {"body": {"mid": "mid-1"}},
        }

        status, _ = request_once(
            handler, "POST", "/trigger", json.dumps({"messages": [update]}).encode()
        )

        self.assertEqual(status, 200)
        self.assertEqual(core.handled, [])
        self.assertEqual(storage.marked, [])

    def test_bot_removed_deletes_stored_channel_before_marking_processed(self):
        core = FakeCore()
        storage = FakeStorage()
        handler = self.stream.create_worker_handler(FakeImpl(), core, storage)
        update = {"update_type": "bot_removed", "chat_id": 777}

        status, _ = request_once(
            handler, "POST", "/trigger", json.dumps({"messages": [update]}).encode()
        )

        self.assertEqual(status, 200)
        self.assertTrue(any("DELETE FROM channels" in query for query, _p, _k in storage.queries))
        delete_params = next(
            params for query, params, _kwargs in storage.queries if "DELETE FROM channels" in query
        )
        self.assertEqual(delete_params["$chat_id"], "777")
        self.assertEqual(len(storage.marked), 1)


if __name__ == "__main__":
    unittest.main()
