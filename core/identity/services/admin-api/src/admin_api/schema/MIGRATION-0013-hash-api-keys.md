# MIGRATION-0013: API keys are held as their SHA-256

**Status:** applied **automatically** by `ensure_schema` (`schema/sync.py::_hash_api_tokens`) on
every admin-api boot, after the column sync and before the index sync. `schema.seal.json` is
re-sealed with the two new columns.

## What it fixes

`api_tokens.token` held every key's value. Anyone with the database or a backup held every
customer's key. Workspace invites never had this problem (`invites.token_hash`, MIGRATION-0009);
keys now follow the same pattern.

## What it adds

| Object | Shape | How it lands |
|---|---|---|
| `api_tokens.token_hash` | `VARCHAR(64)`, unique index: SHA-256 of the key, lowercase hex | additive column; filled by the pass below |
| `api_tokens.token_hint` | `VARCHAR(32)`: `vxa_<scope>_…` + the last four characters (`…abcd` for a key with no prefix) | additive column; filled by the pass below |
| `api_tokens.token` | loses `NOT NULL`; NULL on every converged row | `ALTER COLUMN token DROP NOT NULL` (catalog only) |

## The change

For every row whose `token` is set: `token_hash = sha256(token)`, `token_hint = hint(token)`,
`token = NULL`. The key itself does not change: a caller presents the same value, identity hashes
it, and the row matches.

- Idempotent: only rows with a `token` are read, so a second boot finds none.
- A writer that has not moved to hashes yet (vexa-platform `transcription-gateway`) may still insert
  a plaintext row. Lookups match `token_hash = sha256(presented) OR token = presented`
  (`key_store.token_matches`), so such a key works at once, and the next boot hashes it.
- From this release, every mint door (`/admin/users/{id}/tokens`, `/internal/sessions`) writes only
  the hash and the hint. The value is in the mint answer and nowhere else.
- Lists (`GET /admin/users/{id}/tokens`) answer `token_hint`; they never carried the value.

## Operator-visible

Nothing for key holders: every key keeps working. A key's value can no longer be read back from the
database, so an operator who needs a customer's key mints a new one.

## Rollback

**One-way.** A value cannot be recovered from its hash. An admin-api older than this release looks
keys up by `token`, which is NULL after the pass, so on rollback **no existing key validates** until
the database is restored from a backup taken before the upgrade. Take one before upgrading.

## Deploy order (hosted)

vexa-platform `transcription-gateway` reads `api_tokens` itself. It must ship its hash lookup
(`token_hash = sha256(key)`) **before or with** this admin-api: an older gateway looks keys up by
`token` and finds none after the pass.

## Why it complies

| Rule | How | Enforced by |
|---|---|---|
| no secret at rest | only a hash and a four-character hint are stored | `tests/test_key_hashing.py`, `tests/test_key_hashing_pg.py` |
| no key breaks | the pass hashes in place; lookups hash the presented key | `tests/test_key_hashing_pg.py::test_old_plaintext_keys_still_authenticate_after_hashing` |
| no unreviewed DB change | `schema.seal.json` re-sealed with `token_hash`, `token_hint`, nullable `token` | `gate:db-schema` |
