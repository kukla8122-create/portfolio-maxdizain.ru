# KEDR AI recovery staging

This branch is intentionally isolated from the working MAX transport.

## Scope

Change only the AI call used by KEDR. Do not change:

- MAX bot token;
- MAX webhook URL or subscription;
- MAX owner/user routing;
- the existing task queue/storage;
- message delivery code.

## Prepared router

`kedr/ai_router.py` provides sequential failover with a provider cooldown after quota/rate-limit/upstream errors.

Default order:

`GigaChat -> Groq -> OpenRouter Free -> Gemini`

Only providers with configured server-side secrets are attempted. No secret values belong in Git.

OpenRouter defaults to `openrouter/free`. Other model names can be overridden by Yandex Cloud environment/secret configuration.

## Production integration procedure

1. Read the currently deployed Yandex Cloud function source and logs first.
2. Save/export the current function version before editing.
3. Identify the existing KEDR AI call and its current secret/env names.
4. Integrate only `ai_router.answer(...)` at that call site.
5. Keep the existing task queue semantics: failed tasks stay saved and are retried later.
6. Deploy as a new Yandex Cloud function version.
7. Test AI status and one harmless KEDR task.
8. Verify MAX delivery still uses the unchanged webhook/token/subscription.

Do not merge or deploy this staging branch blindly: the current October KEDR source must be read first because the historical GitHub MAX branch is older than the live KEDR implementation.
