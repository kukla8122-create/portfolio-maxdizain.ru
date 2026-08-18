import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class YandexDeploymentGuardrailTests(unittest.TestCase):
    def read(self, relative):
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_deployment_scripts_pass_bash_syntax_check(self):
        for relative in (
            "deploy/yandex-preflight.sh",
            "deploy/yandex-bootstrap.sh",
            "deploy/activate-max-webhook.sh",
            "deploy/rollback-max-webhook.sh",
        ):
            result = subprocess.run(
                ["bash", "-n", str(ROOT / relative)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, f"{relative}: {result.stderr}")

    def test_bootstrap_launcher_is_immutable_and_integrity_checked(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertIn(
            'BASE_COMMIT="751aa5765e570f05762df416043c6d374f7c4441"', text
        )
        self.assertIn(
            'BASE_BLOB="e06f0829dd43ec66d86d17b836e83c207475a708"', text
        )
        self.assertIn('ACTUAL_BLOB="$(git hash-object "$BASE")"', text)
        self.assertIn('[ "$ACTUAL_BLOB" = "$BASE_BLOB" ]', text)
        self.assertIn("--proto '=https' --tlsv1.2", text)
        self.assertIn('bash -n "$PATCHED"', text)
        self.assertIn('exec bash "$PATCHED"', text)

    def test_bootstrap_launcher_applies_documented_stream_id_correction(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertIn(
            'YDS_STREAM_ID="/$REGION/$FOLDER_ID/$YDB_ID/$STREAM_NAME"', text
        )
        self.assertIn(
            'YDS_STREAM_ID="/$REGION/$CLOUD_ID/$YDB_ID/$STREAM_NAME"', text
        )
        self.assertIn("Data Streams ID", text)
        self.assertIn("Reviewed Data Streams ID correction missing", text)

    def test_bootstrap_launcher_uses_admin_only_temporarily_for_dlq_configuration(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertIn(
            "('grant_folder \"$ING_SA\" ymq.writer',\n        'grant_folder \"$ING_SA\" ymq.admin'",
            text,
        )
        self.assertIn(
            "('--role ymq.writer --service-account-id \"$ING_SA\"',\n        '--role ymq.admin --service-account-id \"$ING_SA\"'",
            text,
        )
        self.assertIn('grant_folder "$TRG_SA" ymq.writer', text)
        self.assertIn('text.replace(needle, needle + "sleep 5\\n", 1)', text)

    def test_bootstrap_launcher_uses_daemonless_buildah_in_cloud_shell(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertIn("apt-get install -y -qq buildah", text)
        self.assertIn("buildah --storage-driver vfs info", text)
        self.assertIn(
            "buildah --storage-driver vfs build --pull=always --isolation chroot --format docker",
            text,
        )
        self.assertIn("buildah login --username iam --password-stdin cr.yandex", text)
        self.assertIn('buildah --storage-driver vfs push "$IMG" "docker://$IMG"', text)
        self.assertIn('grep -Fc \'Docker daemon unavailable\' "$PATCHED"', text)
        self.assertIn('grep -Fc \'dockerd\' "$PATCHED"', text)
        self.assertNotIn("sudo nohup dockerd", text)

    def test_bootstrap_launcher_cannot_activate_max_webhook(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertNotIn('curl -fsS -X POST "$MAX_API/subscriptions"', text)
        self.assertNotIn('curl -fsS -G -X DELETE "$MAX_API/subscriptions"', text)
        self.assertIn("MAX webhook activation: OFF", text)

    def test_activation_requires_live_ordered_stream_before_cutover(self):
        text = self.read("deploy/activate-max-webhook.sh")
        self.assertIn('d.get("transport") == "data-streams"', text)
        self.assertIn('d.get("stream") is True', text)
        self.assertIn('d.get("stream_status") == "ACTIVE"', text)
        self.assertIn('d.get("read_only") is True', text)
        self.assertIn('d.get("activation_enabled") is False', text)
        self.assertNotIn('d.get("queue")', text)

    def test_activation_is_explicit_and_channel_guarded(self):
        text = self.read("deploy/activate-max-webhook.sh")
        self.assertIn("maximum-maxbot-ingress", text)
        self.assertIn("https://platform-api2.max.ru", text)
        self.assertNotIn("bot.portfolio-maxdizain.ru", text)
        self.assertIn("Type ACTIVATE", text)
        self.assertIn('MAX_CHANNEL_LINK="channel_maxmebel_52"', text)
        self.assertIn("/members/me", text)
        self.assertIn("read_all_messages", text)
        self.assertIn("write", text)
        self.assertLess(text.index('say "Verify MAX channel identity'), text.index("Type ACTIVATE"))
        for event_type in (
            "bot_added",
            "bot_removed",
            "bot_started",
            "message_created",
            "message_callback",
        ):
            self.assertIn(event_type, text)

    def test_rollback_deletes_only_exact_target_url(self):
        text = self.read("deploy/rollback-max-webhook.sh")
        self.assertIn("Type ROLLBACK", text)
        self.assertIn('--data-urlencode "url=$WEBHOOK_URL"', text)
        self.assertIn("Unrelated MAX webhook exists", text)


if __name__ == "__main__":
    unittest.main()
