# identity.v1 — scoped token · access decision

The sealed identity contract. Two shapes, both derived from the real admin-api token model
(`libs/admin-models` + admin-api `/internal/validate`):

- **`ScopedToken`** — `subject` (owning user id), `scopes[]` (`bot|tx|browser`, mirrors admin-models
  `VALID_SCOPES`), `expires_at` (RFC3339 or `null` = non-expiring), optional `email` / `issued_at`.
  This is what `tokens.py` mints and validates.
- **`AccessDecision`** — the verdict `canAccess(subject, resource, action)` returns. **Default-deny**:
  `allow=false` unless a policy explicitly grants. `reason` is a stable code
  (`owner | not-owner | default-deny | missing-scope | token-expired`).
- **`ResourceKind`** — the three guarded read paths: `meeting_transcript`, `recording`, `ws_subscribe`.

## Goldens (`golden/`)
`<Shape>.<case>.json` — the prefix is the `$def` it must conform to.
- `ScopedToken.valid.json` — in-scope, far-future expiry.
- `ScopedToken.expired.json` — past `expires_at` (shape-valid; rejected at validation time, not by schema).
- `ScopedToken.scoped.json` — single-scope, non-expiring (`expires_at: null`).
- `AccessDecision.owner-allow.json` / `AccessDecision.not-owner-deny.json` — the allow/deny verdicts.

## Validate
`node validate.mjs [--check]` — ajv2020 + ajv-formats, every golden against its `$def` (gate:schema).
Sealing: `pnpm seal:contracts` from the v0.12 root once the schema is frozen (gate:contract-version).

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._
