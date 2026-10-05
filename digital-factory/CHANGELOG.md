# v3.7

- Add GigaChat chat for the selected employee and an AI daily-plan button.
- Authenticate requests with Supabase and load only the verified user's saved tasks.
- Keep provider credentials on the server; no model output can change tasks or send messages.
- Add OAuth token reuse, request bounds, timeouts and best-effort per-instance rate limits.
- Activation steps are in `AI-SETUP.md`. A saved key and a successful real request are required before claiming GigaChat is working.
- Validation: `node digital-factory/tests/ai.cjs` and the v3.6 sync suite. External services are simulated in tests.

# v3.6

- Separate browser storage and pending queues for each authenticated account.
- Preserve existing v3 storage; migrate it only when its recorded owner matches the signed-in account.
- Compare the original task fields in atomic updates and deletions. Conflicting edits become a separate task; conflicting deletions are cancelled.
- Keep unsent changes for retry after a network failure.
- Remove private client examples from the public HTML and simulated AI events from the journal.
- Merge imported tasks, validate data and save a browser backup before importing. Imports cannot replace internal sync queues or remove existing tasks.
- Preserve search focus during refresh, and filter the selected employee's tasks.
- Distinguish local saving from cloud acknowledgement. Report service-worker registration failures.

Validation: `node digital-factory/tests/sync.cjs` from the repository root. These tests use an in-memory Supabase substitute; they do not verify production RLS, database schema or Android installation.
