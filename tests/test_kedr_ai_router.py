import os
import unittest
from unittest.mock import patch

import kedr.ai_router as router


class KedrRouterTests(unittest.TestCase):
    def setUp(self):
        router._COOLDOWN_UNTIL.clear()

    def test_default_order(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(router.provider_order(), ["gigachat", "groq", "openrouter", "gemini"])

    def test_only_configured_providers_are_reported(self):
        with patch.dict(os.environ, {
            "GROQ_API_KEY": "x",
            "OPENROUTER_API_KEY": "y",
            "KEDR_AI_ORDER": "groq,openrouter,gemini",
        }, clear=True):
            self.assertEqual(router.configured_providers(), ["groq", "openrouter"])

    def test_fallback_after_retryable_failure(self):
        calls = []
        def first(system_prompt, messages):
            calls.append("groq")
            raise router.AIProviderError("groq", "rate limit", retryable=True, status=429)
        def second(system_prompt, messages):
            calls.append("openrouter")
            return router.AIResult("ok", "openrouter", "openrouter/free")

        with patch.dict(os.environ, {
            "GROQ_API_KEY": "x",
            "OPENROUTER_API_KEY": "y",
            "KEDR_AI_ORDER": "groq,openrouter",
        }, clear=True), patch.dict(router.PROVIDERS, {"groq": first, "openrouter": second}, clear=True):
            result = router.answer([{"role": "user", "content": "test"}])
            self.assertEqual(result.provider, "openrouter")
            self.assertEqual(calls, ["groq", "openrouter"])
            self.assertIn("groq", router._COOLDOWN_UNTIL)

    def test_cooldown_skips_failed_provider(self):
        calls = []
        def first(system_prompt, messages):
            calls.append("groq")
            raise router.AIProviderError("groq", "rate limit", retryable=True, status=429)
        def second(system_prompt, messages):
            calls.append("openrouter")
            return router.AIResult("ok", "openrouter", "openrouter/free")

        with patch.dict(os.environ, {
            "GROQ_API_KEY": "x",
            "OPENROUTER_API_KEY": "y",
            "KEDR_AI_ORDER": "groq,openrouter",
        }, clear=True), patch.dict(router.PROVIDERS, {"groq": first, "openrouter": second}, clear=True):
            router.answer([{"role": "user", "content": "one"}])
            router.answer([{"role": "user", "content": "two"}])
            self.assertEqual(calls, ["groq", "openrouter", "openrouter"])

    def test_no_provider_is_explicit_error(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(router.AIProviderError) as ctx:
                router.answer([{"role": "user", "content": "test"}])
            self.assertFalse(ctx.exception.retryable)


if __name__ == "__main__":
    unittest.main()
