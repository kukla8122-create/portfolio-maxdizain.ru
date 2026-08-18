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

    def test_bootstrap_never_activates_max_webhook(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertNotIn("MAX_ACTIVATE_WEBHOOK=1", text)
        self.assertNotIn("POST \"$MAX_API/subscriptions\"", text)
        self.assertIn("MAX webhook activation: OFF", text)

    def test_bootstrap_uses_lockbox_for_runtime_credentials(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertIn("maximum-maxbot-max", text)
        self.assertIn("maximum-maxbot-ymq", text)
        self.assertIn("environment-variable=MAX_BOT_TOKEN", text)
        self.assertIn("environment-variable=MAX_WEBHOOK_SECRET", text)
        self.assertIn("environment-variable=AWS_ACCESS_KEY_ID", text)
        self.assertIn("environment-variable=AWS_SECRET_ACCESS_KEY", text)
        self.assertNotIn("APP_MODE=worker,MAX_BOT_TOKEN=", text)
        self.assertNotIn("APP_MODE=ingress,MAX_BOT_TOKEN=", text)

    def test_bootstrap_has_standard_dlq_redrive_policy(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertIn("maximum-maxbot-events", text)
        self.assertIn("maximum-maxbot-dlq", text)
        self.assertIn("RedrivePolicy", text)
        self.assertIn("maxReceiveCount", text)

    def test_bootstrap_does_not_require_cloud_status_field(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertIn("Cloud.Get no longer exposes a cloud status field", text)
        self.assertNotIn('CJ" | jget status', text)
        self.assertIn('FJ" | jget status', text)

    def test_bootstrap_matches_current_ymq_container_trigger_roles(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        # The current Yandex Serverless Containers YMQ-trigger concept page
        # explicitly requires editor on the source-queue folder for the trigger SA.
        self.assertIn('grant_folder "$TRG_SA" editor', text)
        # Container invocation remains constrained to the worker container itself.
        self.assertIn("serverless-containers.containerInvoker", text)
        self.assertIn("dedicated trigger SA", text)

    def test_activation_is_yandex_native_and_explicit(self):
        text = self.read("deploy/activate-max-webhook.sh")
        self.assertIn("maximum-maxbot-ingress", text)
        self.assertIn("https://platform-api2.max.ru", text)
        self.assertNotIn("bot.portfolio-maxdizain.ru", text)
        self.assertIn("Type ACTIVATE", text)
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
