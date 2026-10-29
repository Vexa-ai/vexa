# webhook-signing seam

Proof that the secret a user sets with `PUT /user/webhook` signs their `meeting.completed` delivery. The
request runs through the real gateway, admin-api (Postgres) and meeting-api, and a local receiver checks
`X-Webhook-Signature` against that secret. The same run shows that a meeting started before the secret
was set is delivered unsigned, and that a later PUT with only `webhook_url` keeps the stored secret.

```bash
cd core/seam/webhook-signing && uv run pytest
```

Postgres comes from `SEAM_PG_URL` (a `postgresql+psycopg://` URL) or, without it, an ephemeral
testcontainers Postgres; with neither the test skips.
