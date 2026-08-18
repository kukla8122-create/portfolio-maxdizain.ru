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

    def test_bootstrap_never_activates_or_deletes_max_webhook(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertNotIn("MAX_ACTIVATE_WEBHOOK=1", text)
        self.assertNotIn('POST "$MAX_API/subscriptions"', text)
        self.assertNotIn('DELETE "$MAX_API/subscriptions"', text)
        self.assertIn("MAX webhook activation: OFF", text)

    def test_bootstrap_uses_lockbox_for_runtime_credentials(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertIn('MAX_SECRET_NAME="maximum-maxbot-max"', text)
        self.assertIn('YDS_SECRET_NAME="maximum-maxbot-yds"', text)
        self.assertNotIn("maximum-maxbot-ymq", text)
        self.assertIn("environment-variable=MAX_BOT_TOKEN", text)
        self.assertIn("environment-variable=MAX_WEBHOOK_SECRET", text)
        self.assertIn("environment-variable=AWS_ACCESS_KEY_ID", text)
        self.assertIn("environment-variable=AWS_SECRET_ACCESS_KEY", text)
        self.assertNotIn("APP_MODE=worker,MAX_BOT_TOKEN=", text)
        self.assertNotIn("APP_MODE=ingress,MAX_BOT_TOKEN=", text)

    def test_bootstrap_uses_data_streams_as_only_business_event_transport(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertIn('STREAM_NAME="maximum-maxbot-events"', text)
        self.assertIn("topic create", text)
        self.assertIn("topic alter", text)
        self.assertIn("--partitions-count 1", text)
        self.assertIn("--partition-write-speed-kbps 128", text)
        self.assertIn("--retention-period 1h", text)
        self.assertIn("--metering-mode reserved-capacity", text)
        self.assertIn("YDS_STREAM_ID=\"/$REGION/$FOLDER_ID/$YDB_ID/$STREAM_NAME\"", text)
        self.assertIn("create yds", text)
        self.assertNotIn("create message-queue", text)
        self.assertNotIn("RedrivePolicy", text)

    def test_message_queue_is_dlq_only(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertIn('DLQ_NAME="maximum-maxbot-dlq"', text)
        self.assertIn("MessageRetentionPeriod", text)
        self.assertIn("--dlq-queue-id", text)
        self.assertIn("--new-container-dlq-queue-id", text)
        self.assertNotIn('QUEUE_NAME="maximum-maxbot-events"', text)

    def test_bootstrap_uses_current_data_stream_trigger_controls(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertIn('grant_folder "$ING_SA" yds.writer', text)
        self.assertIn('grant_folder "$ING_SA" yds.auditor', text)
        self.assertIn('grant_folder "$TRG_SA" yds.admin', text)
        self.assertIn('grant_folder "$TRG_SA" ymq.writer', text)
        self.assertIn("serverless-containers.containerInvoker", text)
        self.assertIn("serverless.containers.invoker", text)
        self.assertIn("--batch-size 1b", text)
        self.assertIn("--batch-cutoff 1s", text)
        self.assertIn("--retry-attempts 5", text)
        self.assertIn("--retry-interval 10s", text)
        self.assertIn("--new-container-retry-attempts 5", text)
        self.assertIn("--new-container-retry-interval 10s", text)

    def test_bootstrap_does_not_require_cloud_status_field(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertIn("Cloud.Get no longer exposes a cloud status field", text)
        self.assertNotIn('CJ" | jget status', text)
        self.assertIn('FJ" | jget status', text)

    def test_bootstrap_tests_and_builds_before_service_account_mutations(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        tests = text.index('"$VENV/bin/python" -m unittest discover -s tests -v')
        build = text.index("sudo docker build --pull -f Dockerfile.yandex")
        service_accounts = text.index('say "Create/reuse dedicated service accounts')
        self.assertLess(tests, service_accounts)
        self.assertLess(build, service_accounts)

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
