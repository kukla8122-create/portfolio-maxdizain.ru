import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_core(tmpdir):
    os.environ["MAX_BOT_TOKEN"] = "TestToken_123456789"
    os.environ["MAX_WEBHOOK_SECRET"] = "TestWebhookSecret_123456789"
    os.environ["MAX_AUTO_SUBSCRIBE"] = "0"
    os.environ["DATA_DIR"] = tmpdir
    os.environ["DATABASE_PATH"] = str(Path(tmpdir) / "maxbot.db")
    spec = importlib.util.spec_from_file_location(
        "maxbot_selfhosted_callback_test", ROOT / "maxbot-selfhosted.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class MaxCallbackAndChannelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.core = load_core(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_answer_callback_uses_official_answers_endpoint(self):
        calls = []

        def fake_http(url, method="GET", obj=None, timeout=30):
            calls.append((url, method, obj))
            return 200, {"success": True}

        self.core.http_json = fake_http
        self.core.answer_callback("callback-123")

        self.assertEqual(len(calls), 1)
        url, method, obj = calls[0]
        self.assertIn("/answers?", url)
        self.assertIn("callback_id=callback-123", url)
        self.assertEqual(method, "POST")
        self.assertEqual(obj, {})

    def test_message_callback_is_acknowledged_before_menu_action(self):
        events = []
        self.core.answer_callback = lambda callback_id: events.append(
            ("answer", callback_id)
        )
        self.core.send_menu = lambda chat_id, welcome=False: events.append(
            ("menu", chat_id)
        )

        self.core.handle_update(
            {
                "update_type": "message_callback",
                "chat_id": 42,
                "callback": {
                    "callback_id": "cb-42",
                    "payload": "menu:main",
                },
            }
        )

        self.assertEqual(events, [("answer", "cb-42"), ("menu", 42)])

    def test_bot_added_persists_channel_id(self):
        saved = []
        self.core.save_channel = lambda chat_id, is_channel, user_id=None: saved.append(
            (chat_id, is_channel, user_id)
        )

        self.core.handle_update(
            {
                "update_type": "bot_added",
                "chat_id": 777,
                "is_channel": True,
                "user": {"user_id": 99},
            }
        )

        self.assertEqual(saved, [(777, True, 99)])


if __name__ == "__main__":
    unittest.main()
