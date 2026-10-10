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

## `/internal/validate` — the authz oracle

The gateway, flows and the terminal's server resolve every bearer with `POST /internal/validate` on
the internal tier (`X-Internal-Secret` = `INTERNAL_API_SECRET`).

- **`ValidateRequest`** — `{"token": …}`: an API key, or a worker's delegation token (`vxd_…`,
  [delegation.v1](../delegation.v1/README.md)). A delegation token is answered only beside
  `AcceptsDelegationHeader` (below).
- **`ValidateResponse`** — `user_id`, `scopes`, `max_concurrent`, `email`, `is_admin`, plus the
  owner's webhook (`webhook_url`, `webhook_secret`, `webhook_events`) and shared-workspace
  memberships (`workspaces`) when they exist. For a delegation token, `scopes` are exactly
  `["bot", "tx"]`, `is_admin` is always `false`, and two fields appear that an API key never has:
  `delegation` (`ValidateDelegation`: the dispatch's `regime`, `workspaces` ceiling and `target`)
  and `person_is_admin` (whether the person the worker acts for is the instance admin). Its
  `workspaces` are the person's memberships narrowed to that ceiling (delegation.v1
  `ceiling_reads`; `"*"` keeps them all), because the services behind the gateway read them as the
  workspaces the bearer reads through. The gateway signs this answer onward as gateway-identity.v1
  claims.
- **Failures** — `503` without an internal secret outside dev mode; `403` on a wrong one; `401`
  `Missing token` · `Invalid token` · `Token expired` · `Delegation tokens are not accepted on this
  deployment` · `Invalid delegation: <reason>` (delegation.v1 `RefusalReason`) · `Invalid
  delegation: no such user`.

### A delegation answer only to a caller that asks for one (`#/$defs/AcceptsDelegationHeader`)

A worker's 200 is a person's 200 plus `delegation` and `person_is_admin`. A resolver that does not
read those two fields would take the worker for its person and drop the ceiling. So a caller
declares that it reads them:

| | |
|---|---|
| **Header** | `X-Vexa-Internal-Accepts-Delegation: 1` (case-insensitive name; in the `x-vexa-internal-` family, which the gateway never forwards from a client) |
| **With it** | a `vxd_` token is verified and answered as above |
| **Without it** (or any value other than `1`) | a `vxd_` token is refused exactly like a bearer nobody answers to: `401 Invalid token` |
| **API keys** | answered the same either way |

Callers that send it: the gateway and flows-api. The terminal's server does not (it reads no
delegation), so a worker's token signs nobody into it.

**Minimum versions.** The declaration arrived in **v0.13.2**. A gateway older than v0.13.2 does not
send it, so a v0.13.2 identity refuses it every delegation token: workers' tool calls fail with 401
until the gateway is upgraded — closed, never open. Upgrade the gateway, the MCP, admin-api,
agent-api and flows-api together.

## Goldens (`golden/`)
`<Shape>.<case>.json` — the prefix is the `$def` it must conform to.
- `ScopedToken.valid.json` — in-scope, far-future expiry.
- `ScopedToken.expired.json` — past `expires_at` (shape-valid; rejected at validation time, not by schema).
- `ScopedToken.scoped.json` — single-scope, non-expiring (`expires_at: null`).
- `AccessDecision.owner-allow.json` / `AccessDecision.not-owner-deny.json` — the allow/deny verdicts.
- `ValidateRequest.token.json` — the oracle's request.
- `ValidateResponse.api-key.json` (a key with a webhook and a membership), `ValidateResponse.admin-key.json`
  (the admin's legacy key) and `ValidateResponse.delegation.json` (an autonomous worker acting for the
  admin: `is_admin` false, `person_is_admin` true).

## Validate
`node validate.mjs [--check]` — ajv2020 + ajv-formats, every golden against its `$def` (gate:schema).
Sealed in `contracts.seal.json` (gate:contract-version): an additive change re-seals with `pnpm seal:contracts` in a `lane:contract` PR; a breaking one is `identity.v2`.

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._
