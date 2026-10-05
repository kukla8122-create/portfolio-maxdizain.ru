# GigaChat activation

The current site uses the repository root as its Vercel root directory. The backend is `api/factory-ai.js`; the factory calls `/api/factory-ai` on the same host.

In the existing Vercel project, add **Production** environment variables:

- `GIGACHAT_CREDENTIALS`: the GigaChat **Authorization key**, the same type of credential used by the existing MAX bot. This is not the temporary access token. Never add it to GitHub, HTML or chat.
- `GIGACHAT_SCOPE`: the scope of the existing subscription, usually `GIGACHAT_API_PERS`, or `GIGACHAT_API_B2B` / `GIGACHAT_API_CORP` for the corresponding account. Default: `GIGACHAT_API_PERS`.
- `FACTORY_AI_MODEL`: a model available to the subscription. Default: `GigaChat-2-Pro`.
- `GIGACHAT_CA_PEM`: if required by the provider's certificate chain, the official trusted CA PEM chain. It is used only for the two GigaChat hosts, with hostname verification kept enabled.
- Optional `FACTORY_AI_ALLOWED_USERS`: comma-separated Supabase user UUIDs allowed to use the assistant. If omitted, authenticated factory users can use their own task data.

Redeploy after changing environment variables. GET `/api/factory-ai` should return `configured: true`. This checks the presence of the key, not its validity. A successful signed-in request is required to verify OAuth, the model subscription and the actual reply.

The assistant receives at most 40 saved active tasks of the verified user, filtered by employee (the dispatcher sees all roles), plus a short chat history. Pending browser changes, email, PRO100 files and production systems are not connected. No model response executes actions or updates tasks.

The per-user six-requests/minute limit is best effort per running serverless instance. It is not a distributed billing limit; set spending limits with the provider.

Tests from repository root:

```
node digital-factory/tests/sync.cjs
node digital-factory/tests/ai.cjs
```

Tests simulate external services. They do not prove live provider connectivity or production RLS isolation.
