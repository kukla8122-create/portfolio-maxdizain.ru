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
                ["bash", "-n", str(ROOT / relative)], capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0, f"{relative}: {result.stderr}")

    def test_bootstrap_uses_cloud_functions_not_container_registry_or_lockbox(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertIn('INGRESS_FN="maximum-maxbot-ingress-fn"', text)
        self.assertIn('WORKER_FN="maximum-maxbot-worker-fn"', text)
        self.assertIn("yc serverless function version create", text)
        self.assertIn("--runtime python312", text)
        self.assertIn("maxbot_yandex_functions.ingress_handler", text)
        self.assertIn("maxbot_yandex_functions.worker_handler", text)
        lower = text.lower()
        self.assertNotIn("yc container registry", lower)
        self.assertNotIn("yc lockbox", lower)
        self.assertNotIn("docker build", lower)
        self.assertNotIn("buildah", lower)

    def test_public_ingress_is_token_free(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        start = text.index('say "Deploy token-free public ingress function"')
        end = text.index('say "Deploy private worker function with no MAX credentials')
        ingress = text[start:end]
        self.assertIn("MAX_WEBHOOK_SECRET", ingress)
        self.assertNotIn("MAX_BOT_TOKEN=", ingress)
        self.assertIn("allow-unauthenticated-invoke", ingress)
        self.assertIn("max_token_present", text)

    def test_worker_is_private_and_max_credentials_are_not_function_metadata(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        start = text.index('say "Deploy private worker function with no MAX credentials')
        end = text.index('say "Pause exact legacy triggers')
        worker = text[start:end]
        self.assertIn('--environment "YDB_CONNECTION_STRING=$YDB_CS"', worker)
        self.assertNotIn("MAX_BOT_TOKEN=", worker)
        self.assertNotIn("MAX_WEBHOOK_SECRET=", worker)
        self.assertIn("deny-unauthenticated-invoke", worker)
        self.assertIn("functions.functionInvoker", worker)

    def test_bootstrap_persists_token_and_stable_webhook_secret_in_ydb(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertIn("CREATE TABLE IF NOT EXISTS deployment_secrets", text)
        self.assertIn('"max_bot_token", $token', text)
        self.assertIn('"max_webhook_secret", $secret', text)
        self.assertIn("secrets.token_urlsafe(36)", text)
        self.assertIn("read-webhook-secret.sql", text)
        self.assertIn("MAX token: stored only in protected YDB data", text)

    def test_runtime_reads_max_token_from_ydb_before_loading_core(self):
        text = self.read("maxbot_yandex_functions.py")
        get_token = text.index('storage.get_deployment_secret("max_bot_token")')
        set_env = text.index('os.environ["MAX_BOT_TOKEN"] = max_token')
        load_core = text.index("core = _load_core_wrapper()")
        self.assertLess(get_token, set_env)
        self.assertLess(set_env, load_core)
        self.assertIn('storage.get_deployment_secret("max_webhook_secret")', text)

    def test_bootstrap_uses_one_partition_ordered_data_stream(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertIn("--partitions-count 1", text)
        self.assertIn("--retention-period 1h", text)
        self.assertIn("--partition-write-speed-kbps 128", text)
        self.assertIn("--metering-mode reserved-capacity", text)
        self.assertIn('[ "$PARTITIONS" = 1 ]', text)
        self.assertIn("--batch-size 1b", text)
        self.assertIn("--batch-cutoff 1s", text)

    def test_static_ymq_key_is_temporary_only(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertIn("yc iam access-key create", text)
        self.assertIn('yc iam access-key delete "$TEMP_ACCESS_KEY_RESOURCE_ID"', text)
        self.assertIn("Temporary YMQ access key: deleted", text)
        deployed = text[text.index('say "Deploy token-free public ingress function"'):]
        self.assertNotIn("AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID", deployed)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY", deployed)

    def test_bootstrap_has_dlq_and_retries(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertIn('DLQ_NAME="maximum-maxbot-dlq"', text)
        self.assertIn("--retry-attempts 5", text)
        self.assertIn("--retry-interval 10s", text)
        self.assertIn("--dlq-queue-id", text)
        self.assertIn("--new-function-dlq-queue-id", text)

    def test_bootstrap_never_changes_max_webhook(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertNotIn('-X POST "$MAX_API/subscriptions"', text)
        self.assertNotIn('-X DELETE "$MAX_API/subscriptions"', text)
        self.assertIn("MAX webhook activation: OFF", text)

    def test_bootstrap_runs_end_to_end_synthetic_check(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertIn("__maximum_healthcheck__", text)
        self.assertIn("processed_events", text)
        self.assertIn("YANDEX_FUNCTIONS_INFRA_READY_FOR_CUTOVER", text)

    def test_activation_uses_stored_identity_and_explicit_cutover(self):
        text = self.read("deploy/activate-max-webhook.sh")
        self.assertIn('INGRESS_FN="maximum-maxbot-ingress-fn"', text)
        self.assertIn("http_invoke_url", text)
        self.assertIn("deployment_secrets", text)
        self.assertIn('max_bot_token', text)
        self.assertIn('max_webhook_secret', text)
        self.assertIn('This token does not match the MAX bot token stored by bootstrap', text)
        self.assertIn("Type ACTIVATE", text)
        self.assertIn("channel_maxmebel_52", text)
        self.assertIn("read_all_messages", text)
        self.assertIn("write", text)
        self.assertNotIn("lockbox", text.lower())
        self.assertNotIn("serverless container", text.lower())

    def test_rollback_deletes_only_exact_function_url_for_deployed_bot(self):
        text = self.read("deploy/rollback-max-webhook.sh")
        self.assertIn('INGRESS_FN="maximum-maxbot-ingress-fn"', text)
        self.assertIn("deployment_secrets", text)
        self.assertIn("This token does not match the deployed MAX bot", text)
        self.assertIn("http_invoke_url", text)
        self.assertIn("Type ROLLBACK", text)
        self.assertIn('--data-urlencode "url=$WEBHOOK_URL"', text)
        self.assertIn("Unrelated MAX webhook exists", text)


if __name__ == "__main__":
    unittest.main()
