# MIGRATION-0010: the credential class (`api_tokens.kind`)

**Status:** in the SSOT model (`schema/models.py` `APIToken.kind`). `ensure_schema` adds it in-band
(`sync._sync_columns`): `ALTER TABLE "api_tokens" ADD COLUMN "kind" VARCHAR(16) NOT NULL DEFAULT 'key'`.
Every existing row reads `key`; nothing anybody sees changes until the terminal holds
`VEXA_TERMINAL_SESSION_SECRET`.

## What it adds

| Object | Shape | How it lands |
|---|---|---|
| `api_tokens.kind` | `VARCHAR(16) NOT NULL DEFAULT 'key'` — `key` or `session` | additive column, server default backfills every row |

## How it works

- A signed-in browser and an API key used to be the same row (`bot,tx,browser`, named
  `terminal-login`), so `browser` proved nothing about who was at the keyboard (PRD 0.13.4 D13).
- `POST /internal/sessions` (`app/sessions.py`) is the one door that writes `kind = 'session'`.
  - It needs the internal tier with no dev-mode escape, AND `X-Terminal-Session-Secret` equal to
    `VEXA_TERMINAL_SESSION_SECRET` (constant-time). Unset → 503; wrong or missing → 403.
  - Body `{user_id, name?, expires_in?}`. It answers `{token, id, user_id, scopes, credential_class: "session"}`.
  - The token keeps the `vxa_browser_…` prefix shape; its scopes are `bot,tx,browser`.
  - A workspace principal's account (MIGRATION-0009) is refused: nobody signs in as a workspace.
- `/admin/users/{id}/tokens` writes `kind = 'key'` and has no parameter that reaches `kind`.
- `/internal/validate` answers `credential_class` for every bearer: `session` for a `session` row,
  `key` for every other key (legacy rows included), `delegation` for a `vxd_` token.

## Rollback

Dropping the column is never done (`ensure_schema` never drops). An older admin-api ignores it: every
token then validates without `credential_class`, which every consumer reads as "not a session" —
decisions that need one are refused, never granted.

## Why it complies

| Rule | How | Enforced by |
|---|---|---|
| P4 | identity.v1 `ValidateResponse` gains `credential_class`; gateway-identity.v1 gains the claim. Re-sealed. | `gate:schema`, `gate:contract-version` |
| P23 | identity is the column's one writer; the terminal holds the only second secret. | `tests/test_session_credential.py` |
| no unreviewed DB change | `schema.seal.json` re-sealed with this column. | `gate:db-schema` |
