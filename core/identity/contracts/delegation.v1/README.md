# delegation.v1 — the token a worker presents as the person it acts for

A chat or routine worker reaches Vexa through the MCP edge. It holds no API key of its own: for each
dispatch, agent-api mints a short-lived **delegation token** that names the person the worker acts
for and the ceiling of what this dispatch may touch. identity verifies it.

```
vxd_<base64url(header)>.<base64url(claims)>.<base64url(HMAC-SHA256(key, "<header>.<claims>"))>

header  {"alg":"HS256","typ":"vxdlg"}
claims  {"aud":"vexa-mcp", "exp":…, "iat":…, "jti":"…",
         "scope":{"regime":"human"|"autonomous", "workspaces":"*"|["slug",…]},
         "sub":"<user id>", "target":"<slug>"?}
```

Both JSON documents are canonical (keys sorted at every depth, no whitespace, ASCII-escaped);
base64url is unpadded.

| | |
|---|---|
| **Minter** | agent-api, per dispatch (`core/agent/shared/delegation.py`), lifetime `VEXA_MCP_DELEGATION_TTL_SEC` — by default the chat worker's warm window plus one turn, 1800 s |
| **Verifier** | identity's `/internal/validate` (`admin_api/delegation.py`): the gateway and flows resolve a `vxd_` bearer through it like any other. Its answer is identity.v1 `ValidateResponse` with `delegation` and `person_is_admin` |
| **Key** | `VEXA_MCP_DELEGATION_SECRET`, symmetric, held by agent-api and admin-api only. Empty is refused on mint and verify; unset on admin-api means every `vxd_` bearer is refused |
| **Where it is accepted** | the gateway admits a `vxd_` bearer on `/mcp`, on the MCP's re-entry (gateway-identity.v1 § Re-entry) and on the worker harness's `POST /agent/friction`; every other route answers 403 |

**Regime** comes from `unit.v1.trigger`: `message` means a person is in the loop (`human`);
`scheduled`, `event` and `transcription` mean nobody is watching (`autonomous`). A human dispatch may
carry `workspaces: "*"`; an autonomous one always carries its explicit isolation set, and the minter
refuses the combination. The scope is a ceiling: it never grants what the account could not already
reach. `target` is a default workspace for verbs that name none, and is outside `scope` on purpose.

**What the ceiling admits** is defined once, in `delegation.py`: `ceiling_allows(workspaces,
workspace, subject=)` — `"*"`, an empty id or the subject's own id, or an id in the list — and
`ceiling_reads`, which also admits `_global`, the company layer every subject reads. agent-api's
resolvers apply them to a named workspace (`control_plane/ceiling.py`), and identity applies
`ceiling_reads` to the person's memberships when it answers a delegation token, so the workspaces a
worker reads meetings through are the ones inside its ceiling.

**Verification order**, with the reason a verifier gives for each refusal (`RefusalReason`):
`vxd_` prefix (`not_delegated` — not this scheme, try the others) → three parts (`malformed`) →
signature, constant time (`bad_signature`) → claims are a JSON object (`malformed`) →
`aud == "vexa-mcp"` (`bad_audience`) → non-empty `sub` (`malformed`) → `now < exp` (`expired`) →
`jti` not on a denylist (`revoked`). No claim is read before the signature verifies. identity answers
`401 Invalid delegation: <reason>`, and `401 Invalid delegation: no such user` when `sub` names no
account.

**Revocation.** A token ends with the unit it was minted for. agent-api records each token's `jti`
against its unit at dispatch and, once the runtime no longer runs that unit (it completed, idled out,
was stopped or failed), writes `vexa:delegation:revoked:<jti>` to the service Redis with the token's
remaining lifetime as its expiry (`core/agent/control_plane/delegation_revocation.py`). identity
answers `401 Invalid delegation: revoked` for a verified token whose key exists, and `503` when it
cannot read the store, so an unreadable store never reads as "not revoked"; API keys never touch it
(`admin_api/app/delegation_revocation.py`). The key's name is held equal on both sides by
`gate:fact-parity` (fact `delegation-revocation-key`). `verify_delegation(…, revoked=…)` remains the
in-module form; the dogfood rig's own verifier passes one.

**Refresh.** A unit still running when half of its token's life has passed is handed a new token:
the same `sub`, `scope` and `target`, a new `jti` and a full lifetime, minted by agent-api from its own
record of the current one (`core/agent/control_plane/delegation_refresh.py`). The worker picks it up
before its next turn. The replaced token is not revoked while the unit runs; it ends at its own `exp`,
or with the unit. A unit that has ended is never refreshed.

## Files

- `delegation.py` — the minter and the verifier. Standard library only, and **vendored byte for
  byte** into `core/agent/shared/` and `core/identity/services/admin-api/src/admin_api/`;
  `gate:fact-parity` (fact `delegation-token`) compares the three. Edit this one and copy it out.
- `delegation.schema.json` — the header, the claims, the refusal reasons and the vector shapes.
- `golden/` — claims, minting vectors and refusal vectors, made with a published test key.
  `validate.mjs` re-mints and re-verifies them in Node (gate:schema), and
  `core/agent/tests/test_delegation_vectors.py` does the same in Python with the vendored module
  (gate:python). Every service that loads `VEXA_MCP_DELEGATION_SECRET` refuses that key at boot.
