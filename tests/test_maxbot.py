import hashlib
import hmac
import importlib.util
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class MaxBotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "maxbot.db")
        os.environ["MAX_BOT_TOKEN"] = "TestToken_123456789"
        os.environ["MAX_WEBHOOK_SECRET"] = "TestWebhook_123456789"
        os.environ["DATA_DIR"] = self.tmp.name
        os.environ["DATABASE_PATH"] = self.db
        os.environ["MAX_AUTO_SUBSCRIBE"] = "0"
        os.environ.pop("BOT_TOKEN", None)
        self.core = load_module("maxbot_core_test", ROOT / "maxbot-selfhosted.py")

    def tearDown(self):
        self.tmp.cleanup()

    def test_phone_normalization(self):
        self.assertEqual(self.core.normalize_phone("8 (999) 123-45-67"), "+79991234567")
        self.assertEqual(self.core.normalize_phone("9991234567"), "+79991234567")
        self.assertEqual(self.core.normalize_phone("123"), "")

    def test_request_contact_signature(self):
        vcf = (
            "BEGIN:VCARD\r\n"
            "VERSION:3.0\r\n"
            "TEL;TYPE=cell:79991234567\r\n"
            "FN:Test User\r\n"
            "END:VCARD\r\n"
        )
        digest = hmac.new(
            os.environ["MAX_BOT_TOKEN"].encode(),
            vcf.encode(),
            hashlib.sha256,
        ).hexdigest()
        message = {
            "body": {
                "attachments": [
                    {
                        "type": "contact",
                        "payload": {"vcf_info": vcf, "hash": digest},
                    }
                ]
            }
        }
        phone, verified = self.core.parse_contact_attachment(message)
        self.assertEqual(phone, "+79991234567")
        self.assertTrue(verified)

    def test_sqlite_lead_persists(self):
        self.core.init_db()
        lead_id = self.core.save_lead(
            "chat-1",
            "user-1",
            "kitchen",
            {"name": "Иван", "city": "Нижний Новгород", "dimensions": "3x4"},
            "+79991234567",
            True,
        )
        self.assertGreater(lead_id, 0)
        with sqlite3.connect(self.db) as conn:
            row = conn.execute(
                "SELECT kind, name, city, phone, phone_verified FROM leads WHERE id=?",
                (lead_id,),
            ).fetchone()
        self.assertEqual(row, ("kitchen", "Иван", "Нижний Новгород", "+79991234567", 1))

    def test_entrypoint_accepts_hosting_bot_token_and_derives_secret(self):
        os.environ.pop("MAX_BOT_TOKEN", None)
        os.environ.pop("MAX_WEBHOOK_SECRET", None)
        os.environ["BOT_TOKEN"] = "HostingToken_abcdef123456"
        entry = load_module("maxbot_entry_test", ROOT / "maxbot-entry.py")
        entry.prepare_environment()
        self.assertEqual(os.environ["MAX_BOT_TOKEN"], "HostingToken_abcdef123456")
        secret = os.environ["MAX_WEBHOOK_SECRET"]
        self.assertRegex(secret, r"^[A-Za-z0-9_-]{5,256}$")
        self.assertGreaterEqual(len(secret), 32)


if __name__ == "__main__":
    unittest.main()
