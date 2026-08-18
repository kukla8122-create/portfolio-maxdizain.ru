import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_yandex_core(tmpdir):
    app = Path(tmpdir) / "app"
    app.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "maxbot-selfhosted.py", app / "maxbot-selfhosted-core.py")
    shutil.copyfile(ROOT / "maxbot-selfhosted-yandex.py", app / "maxbot-selfhosted-yandex.py")

    os.environ["MAX_BOT_TOKEN"] = "TestToken_123456789"
    os.environ["MAX_WEBHOOK_SECRET"] = "TestWebhookSecret_123456789"
    os.environ["MAX_AUTO_SUBSCRIBE"] = "0"
    os.environ["DATA_DIR"] = str(Path(tmpdir) / "data")
    os.environ["DATABASE_PATH"] = str(Path(tmpdir) / "data" / "maxbot.db")

    spec = importlib.util.spec_from_file_location(
        "maxbot_selfhosted_yandex_dialog_test", app / "maxbot-selfhosted-yandex.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    module._core.init_db()
    return module


def message_update(chat_type, text="/start", chat_id=42):
    return {
        "update_type": "message_created",
        "chat_id": chat_id,
        "message": {
            "recipient": {"chat_id": chat_id, "chat_type": chat_type},
            "sender": {"user_id": 7, "is_bot": False},
            "body": {"mid": "mid-1", "text": text},
        },
    }


def callback_update(chat_type, chat_id=42):
    return {
        "update_type": "message_callback",
        "chat_id": chat_id,
        "message": {
            "recipient": {"chat_id": chat_id, "chat_type": chat_type},
            "sender": {"user_id": 999, "is_bot": True},
            "body": {"mid": "mid-callback", "text": "menu"},
        },
        "callback": {
            "callback_id": "cb-42",
            "payload": "menu:main",
            "user": {"user_id": 7, "is_bot": False},
        },
    }


class DialogIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bot = load_yandex_core(self.tmp.name)
        self.events = []
        self.bot._core.send_menu = lambda chat_id, welcome=False: self.events.append(
            ("menu", chat_id, welcome)
        )
        self.bot._core.send_message = lambda *args, **kwargs: self.events.append(
            ("message", args, kwargs)
        )
        self.bot._core.answer_callback = lambda callback_id: self.events.append(
            ("answer", callback_id)
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_dialog_message_runs_client_flow(self):
        self.bot.handle_update(message_update("dialog"))
        self.assertEqual(self.events, [("menu", 42, True)])

    def test_group_chat_message_is_ignored(self):
        self.bot.handle_update(message_update("chat"))
        self.assertEqual(self.events, [])

    def test_channel_post_is_ignored(self):
        update = message_update("channel")
        update["message"]["url"] = "https://max.ru/channel/post"
        update["message"]["stat"] = {"views": 1}
        self.bot.handle_update(update)
        self.assertEqual(self.events, [])

    def test_missing_chat_type_fails_closed(self):
        update = message_update("dialog")
        update["message"]["recipient"].pop("chat_type")
        self.bot.handle_update(update)
        self.assertEqual(self.events, [])

    def test_dialog_callback_is_acknowledged_and_runs_action(self):
        self.bot.handle_update(callback_update("dialog"))
        self.assertEqual(
            self.events,
            [("answer", "cb-42"), ("menu", 42, False)],
        )

    def test_group_callback_is_acknowledged_but_business_action_is_blocked(self):
        self.bot.handle_update(callback_update("chat"))
        self.assertEqual(self.events, [("answer", "cb-42")])

    def test_channel_callback_is_acknowledged_but_business_action_is_blocked(self):
        self.bot.handle_update(callback_update("channel"))
        self.assertEqual(self.events, [("answer", "cb-42")])

    def test_bot_started_still_opens_private_welcome_menu(self):
        self.bot.handle_update(
            {"update_type": "bot_started", "chat_id": 42, "user": {"user_id": 7}}
        )
        self.assertEqual(self.events, [("menu", 42, True)])


if __name__ == "__main__":
    unittest.main()
