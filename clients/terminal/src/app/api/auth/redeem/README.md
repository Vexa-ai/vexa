# auth/redeem

`GET ?t=<token>&next=<relative-path>` — the back half of the magic-link door.

1. Verifies the HMAC signature and expiry and **burns the jti** (single use), before any
   admin-api round-trip, so a replay cannot race a slow mint.
2. Runs the same `findOrCreateUserToken` + cookie flow as every other door, so downstream
   consumers see an ordinary session (including the `id` field the `api/minutes/*` seams read).
   That function first asks whether the address may sign in AT ALL (Vexa-ai/vexa#1783) — checked
   again here because the link may outlive the allow-list entry that sent it — and refuses before
   any account exists: `403` with the shared sentence, or `503` when admin-api cannot answer.
3. `302`s to `next`, already reduced to a site-relative path — an emailed link can never bounce
   its recipient off this origin.

A refused link answers with a small self-contained HTML page (410 used/expired, 400 forged,
503 unconfigured), not a JSON error: the visitor arrived by clicking a mail.
