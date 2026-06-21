# identity_core — tokens · access · secrets

The identity CORE. Public surface is `__init__.py` (the lane's `index`):

- **`tokens.py`** — `ScopedToken` value object + `mint_token` / `validate_token`. Scopes are
  `{bot, tx, browser}` (admin-models `VALID_SCOPES`). Validation rejects expired (`token-expired`)
  and out-of-scope (`missing-scope`) tokens — derived from admin-api `/internal/validate`.
- **`access.py`** — `can_access(subject, resource, action)`: the `AccessPolicy` Protocol (port) +
  `OwnerOnlyPolicy`, a **default-deny** owner-only adapter (P20). Guards the three read paths
  (`meeting_transcript`, `recording`, `ws_subscribe`), keyed on resource `owner` (admin `user_id`).
- **`secrets.py`** — `SecretsPort` Protocol + `PassthroughSecretsBroker` (P15): returns a scoped,
  audited `BrokeredSecret` whose value is reachable only via `.reveal()` and is never logged. Real
  vault adapter deferred to P16 — only the seam is wired here.

All emitted shapes conform to `../../contracts/identity.v1`. Tested in `../../tests/`.

_Governed by `docs/ARCHITECTURE.md` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._
