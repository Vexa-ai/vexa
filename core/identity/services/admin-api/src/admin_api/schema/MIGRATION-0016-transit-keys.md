# MIGRATION-0016: transit keys (ADR-0051)

**Status:** applied **automatically** by `ensure_schema`: the column sync adds the columns and the
table; `schema/sync.py::_register_legacy_transit_target` registers one target. `schema.seal.json`
re-sealed.

## What it adds

| Object | Shape |
|---|---|
| `api_tokens.audience` | `VARCHAR(64)`, indexed: the target a transit key opens; NULL on every other key |
| `api_tokens.actions` | `TEXT[]`: what a transit key may do there |
| `transit_targets` | `name` (PK), `actions`, `legacy_prefixes`, `description`, `secret` (sealed, `at_rest.py`), `created_at`, `secret_rotated_at` |

## The one registration

```sql
INSERT INTO transit_targets (name, actions, legacy_prefixes, description, created_at)
VALUES ('transcription', '{audio.transcribe}', '{tx,user,admin,""}', 'the hosted transcription API …', now())
ON CONFLICT (name) DO NOTHING;
```

The prefixes are the ones transcription-gateway has always honoured, so every existing
transcription key (`vxa_tx_…`, `vxa_user_…`, `vxa_admin_…`, and keys with no prefix) reads as a
transit key on `transcription` with no row rewritten. A `vxa_bot_…` key does not, exactly as
before. The target has no credential until the operator issues one; until then it can validate
nothing, and transcription-gateway keeps its own hash lookup.

## Operator steps (hosted)

1. Upgrade admin-api (this migration).
2. `POST /admin/transit-targets/transcription/secret` with the admin key → store the answered
   secret as a Kubernetes Secret.
3. Set vexa-platform `transcriptionGateway.transit` (`enabled`, `identityUrl`, `secretName`).

## Behaviour

- A transit key is a person's key (`kind = 'key'`): hashed, listed (`audience`, `actions`), revoked
  like any other. `/internal/validate` and the self-serve tier refuse it: it reaches no gateway route.
- Only identity mints one: `POST /admin/users/{id}/transit-keys` (operator) or `POST /transit/keys`
  (the target, on itself).
- `POST /transit/validate` answers an HMAC-SHA256 signed claim set under the target's credential;
  any other target, action, expired key or unknown key is refused.

## Rollback

An older admin-api ignores the columns and the table. It reads a `vxt_…` transit key as an
ordinary key with no scopes (`legacy`), and the gateway's routes refuse that. Legacy transcription
keys are untouched either way.

| Rule | Enforced by |
|---|---|
| identity alone mints and answers | `tests/test_transit_keys_pg.py` |
| existing tx keys keep working | `tests/test_transit_keys_pg.py::test_existing_transcription_keys_keep_working_as_transit_keys` |
| the answer is the target's to verify | `tests/test_transit_answer.py`, vexa-platform `test_transit_client.py` (identity-signed vector) |
