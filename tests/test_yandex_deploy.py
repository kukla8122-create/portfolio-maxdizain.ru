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

    def test_bootstrap_is_integrity_checked_immutable_launcher(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertIn(
            'BASE_COMMIT="9a5ea38136d9fecddd59cd70c7e7ce1b11c3a84e"', text
        )
        self.assertIn(
            'BASE_BLOB="da2f56eaee11955ee3b9b7a99dfb9b41ca17015c"', text
        )
        self.assertIn('ACTUAL_BLOB="$(git hash-object "$BASE")"', text)
        self.assertIn('[ "$ACTUAL_BLOB" = "$BASE_BLOB" ]', text)
        self.assertIn("--proto '=https' --tlsv1.2", text)
        self.assertIn('bash -n "$PATCHED"', text)
        self.assertIn('exec bash "$PATCHED"', text)

    def test_bootstrap_reattaches_piped_stdin_to_controlling_terminal(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertIn('[ -r /dev/tty ] || die "Interactive terminal /dev/tty is unavailable"', text)
        self.assertIn('exec bash "$PATCHED" </dev/tty', text)

    def test_launcher_hardens_current_cloud_functions_cli_memory(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertIn('text.count("--memory 256m") != 2', text)
        self.assertIn('text.replace("--memory 256m", "--memory 256MB")', text)
        self.assertIn("Cloud Functions CLI compatibility: OK", text)

    def test_launcher_passes_full_ydb_topic_path(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertIn(
            "YDS_TOPIC=$YDB_PATH/$STREAM_NAME,MAX_WEBHOOK_SECRET=$MAX_WEBHOOK_SECRET",
            text,
        )
        self.assertIn("Full YDB topic path: OK", text)
        self.assertIn("Relative YDB topic path unexpectedly remains", text)

    def test_launcher_parameterizes_final_ydb_verification(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertIn("DECLARE $event_id AS Utf8;", text)
        self.assertIn("WHERE event_id=$event_id", text)
        self.assertIn('--input-file "$TMP/e2e.json"', text)
        self.assertIn("Parameterized YDB verification: OK", text)
        self.assertIn("Interpolated E2E SQL unexpectedly remains", text)

    def test_launcher_cannot_directly_activate_max_webhook(self):
        text = self.read("deploy/yandex-bootstrap.sh")
        self.assertNotIn('-X POST "$MAX_API/subscriptions"', text)
        self.assertNotIn('-X DELETE "$MAX_API/subscriptions"', text)
        self.assertIn("MAX webhook activation: OFF throughout bootstrap", text)

    def test_cloud_functions_runtime_waits_for_topic_ack_and_keeps_token_private(self):
        text = self.read("maxbot_yandex_functions.py")
        self.assertIn("write_with_ack", text)
        self.assertIn('storage.get_deployment_secret("max_bot_token")', text)
        self.assertIn('storage.get_deployment_secret("max_webhook_secret")', text)
        get_token = text.index('storage.get_deployment_secret("max_bot_token")')
        set_env = text.index('os.environ["MAX_BOT_TOKEN"] = max_token')
        load_core = text.index("core = _load_core_wrapper()")
        self.assertLess(get_token, set_env)
        self.assertLess(set_env, load_core)
        self.assertIn('"max_token_present": bool(os.environ.get("MAX_BOT_TOKEN"))', text)

    def test_activation_uses_stored_identity_and_explicit_cutover(self):
        text = self.read("deploy/activate-max-webhook.sh")
        self.assertIn('INGRESS_FN="maximum-maxbot-ingress-fn"', text)
        self.assertIn("http_invoke_url", text)
        self.assertIn("deployment_secrets", text)
        self.assertIn("max_bot_token", text)
        self.assertIn("max_webhook_secret", text)
        self.assertIn("This token does not match the MAX bot token stored by bootstrap", text)
        self.assertIn("Type ACTIVATE", text)
        self.assertIn("channel_maxmebel_52", text)
        self.assertIn("read_all_messages", text)
        self.assertIn("write", text)
        self.assertIn("read -r -s MAX_TOKEN </dev/tty", text)
        self.assertIn("read -r answer </dev/tty", text)
        self.assertNotIn("lockbox", text.lower())
        self.assertNotIn("serverless container", text.lower())

    def test_rollback_is_scoped_to_exact_function_url_and_deployed_bot(self):
        text = self.read("deploy/rollback-max-webhook.sh")
        self.assertIn('INGRESS_FN="maximum-maxbot-ingress-fn"', text)
        self.assertIn("deployment_secrets", text)
        self.assertIn("DECLARE $key AS Utf8;", text)
        self.assertIn('--input-file "$TMP/read-token.json"', text)
        self.assertIn("This token does not match the deployed MAX bot", text)
        self.assertIn("http_invoke_url", text)
        self.assertIn("Type ROLLBACK", text)
        self.assertIn('read -r -s MAX_TOKEN </dev/tty', text)
        self.assertIn('read -r answer </dev/tty', text)
        self.assertIn('--data-urlencode "url=$WEBHOOK_URL"', text)
        self.assertIn("Unrelated MAX webhook exists", text)


if __name__ == "__main__":
    unittest.main()
