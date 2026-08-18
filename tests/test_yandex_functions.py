import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    fake_ydb = types.ModuleType("ydb")
    fake_iam = types.ModuleType("ydb.iam")

    class FakeCredentials:
        pass

    class FakeDriver:
        def __init__(self, *args, **kwargs):
            self.topic_client = types.SimpleNamespace(writer=lambda _topic: None)

        def wait(self, *args, **kwargs):
            return None

    fake_iam.MetadataUrlCredentials = FakeCredentials
    fake_ydb.iam = fake_iam
    fake_ydb.Driver = FakeDriver
    fake_ydb.load_ydb_root_certificate = lambda: b"cert"
    fake_ydb.QuerySessionPool = object
    fake_ydb.RetrySettings = lambda **kwargs: kwargs
    sys.modules["ydb"] = fake_ydb
    sys.modules["ydb.iam"] = fake_iam

    os.environ["YDB_CONNECTION_STRING"] = "grpcs://example/?database=/db"
    os.environ["MAX_WEBHOOK_SECRET"] = "Secret_12345"
    spec = importlib.util.spec_from_file_location(
        "maxbot_yandex_functions_test", ROOT / "maxbot_yandex_functions.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class FakeWriter:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def write_with_ack(self, value):
        self.calls.append(value)
        if self.fail:
            raise RuntimeError("stream down")


class FakeStorage:
    def __init__(self):
        self.done = set()
        self.marked = []
        self.removed = []

    def event_processed(self, key):
        return key in self.done

    def mark_event_processed(self, key):
        self.done.add(key)
        self.marked.append(key)

    def remove_channel(self, chat_id):
        self.removed.append(chat_id)


class FakeCore:
    def __init__(self):
        self.updates = []

    def handle_update(self, update):
        self.updates.append(update)


class CloudFunctionsRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.m = load_module()
        self.m._ingress_driver = None
        self.m._ingress_writer = None
        self.m._worker_core = None
        self.m._worker_storage = None
        os.environ.pop("MAX_BOT_TOKEN", None)
        os.environ["MAX_WEBHOOK_SECRET"] = "Secret_12345"

    def test_get_health_is_read_only_and_ingress_has_no_max_token(self):
        response = self.m.ingress_handler({"httpMethod": "GET"}, None)
        payload = json.loads(response["body"])
        self.assertEqual(response["statusCode"], 200)
        self.assertTrue(payload["read_only"])
        self.assertEqual(payload["transport"], "data-streams")
        self.assertFalse(payload["max_token_present"])

    def test_wrong_webhook_secret_never_writes_stream(self):
        writer = FakeWriter()
        self.m._ingress_writer = writer
        response = self.m.ingress_handler(
            {
                "httpMethod": "POST",
                "headers": {"X-Max-Bot-Api-Secret": "wrong"},
                "body": '{"update_type":"bot_started"}',
            },
            None,
        )
        self.assertEqual(response["statusCode"], 403)
        self.assertEqual(writer.calls, [])

    def test_ingress_waits_for_write_ack_before_http_200(self):
        writer = FakeWriter()
        self.m._ingress_writer = writer
        update = {"update_type": "__maximum_healthcheck__", "nonce": "abc"}
        response = self.m.ingress_handler(
            {
                "httpMethod": "POST",
                "headers": {"x-max-bot-api-secret": "Secret_12345"},
                "body": json.dumps(update),
            },
            None,
        )
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(len(writer.calls), 1)
        self.assertEqual(json.loads(writer.calls[0]), update)

    def test_ingress_returns_503_when_stream_write_is_not_acknowledged(self):
        self.m._ingress_writer = FakeWriter(fail=True)
        response = self.m.ingress_handler(
            {
                "httpMethod": "POST",
                "headers": {"x-max-bot-api-secret": "Secret_12345"},
                "body": '{"update_type":"bot_started"}',
            },
            None,
        )
        self.assertEqual(response["statusCode"], 503)

    def test_worker_preserves_trigger_array_order_and_ignores_duplicate(self):
        core = FakeCore()
        storage = FakeStorage()
        self.m._worker_core = core
        self.m._worker_storage = storage
        first = {"update_type": "message_created", "message": {"body": {"mid": "m1"}}}
        second = {"update_type": "message_created", "message": {"body": {"mid": "m2"}}}
        result = self.m.worker_handler({"messages": [first, second, first]}, None)
        self.assertEqual(core.updates, [first, second])
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["duplicates"], 1)

    def test_bot_removed_cleans_stored_channel(self):
        core = FakeCore()
        storage = FakeStorage()
        self.m._worker_core = core
        self.m._worker_storage = storage
        update = {"update_type": "bot_removed", "chat_id": 777}
        self.m.worker_handler({"messages": [update]}, None)
        self.assertEqual(storage.removed, [777])
        self.assertEqual(core.updates, [update])

    def test_stable_key_uses_message_mid_and_callback_id(self):
        self.assertEqual(
            self.m.stable_update_key(
                {"update_type": "message_created", "message": {"body": {"mid": "m1"}}}
            ),
            "max:message_created:message:m1",
        )
        self.assertEqual(
            self.m.stable_update_key(
                {"update_type": "message_callback", "callback": {"callback_id": "c1"}}
            ),
            "max:message_callback:callback:c1",
        )


if __name__ == "__main__":
    unittest.main()
