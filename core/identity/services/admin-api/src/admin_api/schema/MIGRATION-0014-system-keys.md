# MIGRATION-0014: a person sees only the keys they made; the product's own credentials end and go

**Status:** applied **automatically** by `ensure_schema` (`schema/sync.py::_classify_and_sweep_keys`)
on every admin-api boot, after MIGRATION-0013. Data only: `api_tokens.kind` (MIGRATION-0010) gains a
third value, so `schema.seal.json` is unchanged.

## What it fixes (#1868)

A customer's key list held ~384 `key-NNNN` rows they never made. Prod, 23 Oct 2026: 1,734
`webapp-login` keys (one per sign-in, the key the browser cookie carries), 2,197 unnamed 15-minute
keys flows minted for itself, and sign-in keys from the old dashboard. Founder ruling: a person's
key list shows only the keys they created; system keys are hidden, and deleted once they expire.

## The credential class

| `kind` | What it is | Listed to the person | Ends |
|---|---|---|---|
| `key` | a person's key: their key page, the connect door (`mcp-connect`), an operator | yes | as minted |
| `session` | the terminal's signed-in browser (`POST /internal/sessions`) | no | 30 days |
| `system` | a credential the product mints for itself (a sign-in cookie's key) | no | always, ≤ 30 days |

- `POST /admin/users/{id}/tokens` takes `credential_class` (`key` default, or `system`) in the body
  or the query. `session` is refused (422): sessions come only from their own door.
- A mint named `terminal-login`, `webapp-login` or `dashboard-login` is `system` whatever the caller
  sent, so a client that predates the class still mints a hidden, ending credential.
- A `system` key's end is the requested lifetime, capped at 30 days, and 30 days when none is named.
- `GET /admin/users/{id}/tokens` answers `kind = 'key'` rows only, each with `credential_class`;
  `?include_hidden=true` is the operator's full view.
- `/internal/validate` still answers `credential_class: key` for a system key (identity.v1 has
  `session`, `key`, `delegation`): a system key is never a session.

## The boot pass

```sql
UPDATE api_tokens SET kind = 'system',
       expires_at = COALESCE(expires_at, COALESCE(created_at, now()) + interval '30 days')
WHERE kind = 'key' AND (lower(name) IN ('terminal-login', 'webapp-login', 'dashboard-login')
       OR (name IS NULL AND expires_at - created_at <= interval '1 day'));
DELETE FROM api_tokens WHERE kind IN ('session', 'system') AND expires_at < now();
```

- A sign-in key with no end (webapp-login before 4 Sep 2026) gets its cookie's: `created_at` + 30
  days. Its cookie is long gone, so it is deleted in the same pass.
- An unnamed key that lived a day or less is flows' (15 minutes each); all have expired, and go.
- **Not touched:** unnamed keys with no end (~5,950 in prod, mostly the old dashboard's per-login
  keys). They cannot be told apart from a key a person made with no name, and whether to retire
  them is the founder's decision. They stay `key`, listed and working.
- Every mint (both doors) also deletes that person's expired `session` and `system` rows.
- Idempotent: the UPDATE reads only matching `key` rows; the DELETE only expired hidden ones.

## Operator-visible

Key lists shrink to the keys people made. A browser whose cookie still holds a `webapp-login` or
`terminal-login` key older than 30 days signs in again, as it would have had to anyway.

## Rollback

An older admin-api lists `system` rows again and reads them as keys; nothing stops validating.
Deleted rows were expired and could not authenticate.

| Rule | How | Enforced by |
|---|---|---|
| a person sees only their keys | the list filters `kind = 'key'` | `tests/test_key_classes.py` |
| no standing product credential | a system key always ends; expired ones are deleted on mint and boot | `tests/test_key_classes_pg.py` |
