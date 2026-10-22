# MIGRATION-0011: every sign-in credential ends with its cookie

**Status:** applied **automatically** by `ensure_schema` (`schema/sync.py::_bound_sign_in_credentials`),
on every admin-api boot, after the default org and before the index sync. Data only: no column,
index or table changes, so `schema.seal.json` is unchanged.

## What it fixes (B1)

A browser's sign-in credential is carried by a 30-day cookie, but two kinds of row outlived it:

- **Sessions** (`kind = 'session'`) minted before a session had a lifetime: `expires_at` NULL.
- **`terminal-login` keys** (`kind = 'key'`, name `terminal-login`, the `browser` scope): the
  credential the sign-in minted before sessions existed, and still mints on a deployment without
  `VEXA_TERMINAL_SESSION_SECRET`. NULL expiry, so a standing key.

Before this release such a key could also be traded once for a fresh 30-day session
(`POST /internal/sessions/upgrade`). That door is gone: a browser holding a key signs in again.

## The change

```sql
UPDATE api_tokens
SET expires_at = COALESCE(created_at, now() AT TIME ZONE 'utc') + interval '30 days'
WHERE expires_at IS NULL
  AND (kind = 'session' OR (kind = 'key' AND name = 'terminal-login' AND 'browser' = ANY(scopes)));
```

- A row older than 30 days gets an end in the past: it stops validating, which is what its cookie
  did long ago.
- A row with no `created_at` gets 30 days from the convergence.
- A row that already has an end, and every self-serve key (any other name), is never touched.
- Idempotent: it only ever reads rows with no end.

The terminal also mints the fallback `terminal-login` key with `expires_in` = 30 days, so no new row
needs this pass.

## Operator-visible

A person whose browser still holds a pre-session key, or a session older than 30 days, is asked to
sign in again. Nothing else changes.

## Rollback

An older admin-api ignores `expires_at` values it did not write only in the sense that it keeps
honouring them: bounded rows stay bounded. Nothing is dropped.

| Rule | How | Enforced by |
|---|---|---|
| no standing browser credential | the convergence bounds every one | `tests/test_sign_in_expiry_backfill_pg.py` |
| no key traded for a session | the upgrade door is removed | `tests/test_session_credential.py::test_a_legacy_login_key_is_never_traded_for_a_session` |
