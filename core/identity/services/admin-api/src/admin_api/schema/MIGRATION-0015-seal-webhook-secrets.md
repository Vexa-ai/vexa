# MIGRATION-0015: webhook signing secrets are stored sealed

**Status:** applied **automatically** by `ensure_schema` (`schema/sync.py::_seal_webhook_secrets`) on
every admin-api boot, when `VEXA_AT_REST_KEY_FILE` is set. Data only (JSONB values), so
`schema.seal.json` is unchanged.

## What it fixes

A person's webhook signing secret sat in `users.data.webhook_secret` as written, and a copy rode on
every meeting (`meetings.data.webhook_secret`) for the post-meeting delivery. It cannot be hashed:
meeting-api presents it onward, signing every delivery with it. So it is sealed.

## The key

`VEXA_AT_REST_KEY_FILE`: a file of 64 hex characters (`openssl rand -hex 32`), mounted into
admin-api and meeting-api (the same file), apart from the database, never in it and never in a
database backup. The same custody as the credential broker's store key. Generated at install
(compose, Helm, Lite); never regenerate it on an existing install.

## The form

`vxe1:` + base64url(nonce ‖ AES-256-GCM ciphertext+tag), AAD `vexa-at-rest.v1\0webhook_secret`
(`at_rest.py`, vendored byte for byte in admin-api and meeting-api). A value without the prefix is a
legacy plaintext value and is used as it is.

| Where | Written by | Read by |
|---|---|---|
| `users.data.webhook_secret` | `PUT /user/webhook` — sealed | `/internal/validate`, the calendar spawn context — unsealed into the signed identity |
| `meetings.data.webhook_secret` | meeting-api `POST /bots` — sealed | the lifecycle callback's delivery — unsealed to sign |
| meeting-api's webhook retry queue (Redis) | `RetryQueue.enqueue` — sealed | `_deliver_one` — unsealed to sign |

## The boot pass

For `users` and `meetings`: every `data.webhook_secret` that is a non-empty string not starting
`vxe1:` is replaced with its sealed form (`jsonb_set`, one path, nothing else in the row moves).
Idempotent. The secret itself does not change, so every subscriber keeps verifying signatures
(`X-Webhook-Signature`) with the secret they hold.

Retry-queue entries queued before the upgrade stay plaintext until delivered or dead-lettered
(minutes to hours); they still sign correctly.

## Without the key

Nothing is sealed, every value is used as written, and admin-api logs an error at boot naming the
missing key. A sealed value read without the key (or with another key) opens to nothing: that
delivery goes unsigned and the subscriber rejects it, never accepting a wrong secret.

## Rollback

An older admin-api or meeting-api reads a sealed value as the secret itself and signs with the
wrong value: subscribers reject deliveries. Roll back both services together, and unseal first if
you must (`at_rest.unseal` over the same rows), or keep the release.

| Rule | How | Enforced by |
|---|---|---|
| no presentable secret stored as written | sealed with a key outside the database | `tests/test_webhook_secret_at_rest_pg.py`, meeting-api `tests/test_webhook_secret_at_rest.py` |
| existing webhooks keep verifying | the secret is unchanged; legacy and sealed rows sign identically | both tests above |
