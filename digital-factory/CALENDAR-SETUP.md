# Google Calendar

The app uses the existing public Katya AI Web OAuth identifier, optionally overridden by `GOOGLE_CALENDAR_CLIENT_ID` in Vercel. No OAuth client secret is required for the Google Identity Services browser token model.

Google project: `katya-ai-508110`. Enable Google Calendar API and allow the exact JavaScript origin `https://portfolio-maxdizainru.vercel.app` on the OAuth web client. Keep existing origins and redirect URIs. If OAuth audience is Testing, add the intended Google account as a test user. Do not publish the consent app without checking Google verification requirements.

`calendar.html` requires the factory Supabase session and queries only that owner's tasks. Google account selection is separate from factory login. The first grant reads events in owned calendars. Creation requests the additional `calendar.events.owned` permission. Google grants broader event modification privileges than this UI uses; the UI only lists and inserts primary-calendar events, never edits or deletes events. Tokens and calendar contents stay in page memory, are cleared on factory account changes, and are not sent to the AI backend or saved to localStorage.

Each event insert has a random stable ID for retries of the same draft; a 409 checks that existing event instead of creating another. The ID survives token expiration in the current page. Closing/reloading the page ends this retry protection: after an uncertain request, check Google Calendar before recreating a draft. No guests, invitations, task mutation or automatic bidirectional sync. Times use the device timezone and are transmitted as UTC instants. Upcoming view is bounded to 30 days and 100 events.

Acceptance: authorize reading, verify upcoming events; choose a task and check the populated title/project/time; request create access and create a disposable event only if explicitly authorized by the user; confirm it exists and a second click creates no copy; sign out of the factory and confirm events/token disappear.

Official reference: https://developers.google.com/identity/oauth2/web/guides/use-token-model
