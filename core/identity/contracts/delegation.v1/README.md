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
| **Minter** | agent-api, per dispatch (`core/agent/shared/delegation.py`), lifetime 3600 s |
| **Verifier** | identity's `/internal/validate` (`admin_api/delegation.py`): the gateway and flows resolve a `vxd_` bearer through it like any other. Its answer is identity.v1 `ValidateResponse` with `delegation` and `person_is_admin` |
| **Key** | `VEXA_MCP_DELEGATION_SECRET`, symmetric, held by agent-api and admin-api only. Empty is refused on mint and verify; unset on admin-api means every `vxd_` bearer is refused |
| **Where it is accepted** | the gateway admits a `vxd_` bearer on `/mcp`, on the MCP's re-entry (gateway-identity.v1 § Re-entry) and on the worker harness's `POST /agent/friction`; every other route answers 403 |

**Regime** comes from `unit.v1.trigger`: `message` means a person is in the loop (`human`);
`scheduled`, `event` and `transcription` mean nobody is watching (`autonomous`). A human dispatch may
carry `workspaces: "*"`; an autonomous one always carries its explicit isolation set, and the minter
refuses the combination. The scope is a ceiling: it never grants what the account could not already
reach. `target` is a default workspace for verbs that name none, and is outside `scope` on purpose.

**Verification order**, with the reason a verifier gives for each refusal (`RefusalReason`):
`vxd_` prefix (`not_delegated` — not this scheme, try the others) → three parts (`malformed`) →
signature, constant time (`bad_signature`) → claims are a JSON object (`malformed`) →
`aud == "vexa-mcp"` (`bad_audience`) → non-empty `sub` (`malformed`) → `now < exp` (`expired`) →
`jti` not on a denylist (`revoked`). No claim is read before the signature verifies. identity answers
`401 Invalid delegation: <reason>`, and `401 Invalid delegation: no such user` when `sub` names no
account.

**Revocation.** The product verifier keeps no denylist: a token is valid until `exp`. The `revoked`
reason exists for a verifier that passes one (`verify_delegation(…, revoked=…)`); the dogfood rig's
own verifier does.

## Files

- `delegation.py` — the minter and the verifier. Standard library only, and **vendored byte for
  byte** into `core/agent/shared/` and `core/identity/services/admin-api/src/admin_api/`;
  `gate:fact-parity` (fact `delegation-token`) compares the three. Edit this one and copy it out.
- `delegation.schema.json` — the header, the claims, the refusal reasons and the vector shapes.
- `golden/` — claims, minting vectors and refusal vectors, made with a published test key.
  `validate.mjs` re-mints and re-verifies them in Node (gate:schema), and
  `core/agent/tests/test_delegation_vectors.py` does the same in Python with the vendored module
  (gate:python). Every service that loads `VEXA_MCP_DELEGATION_SECRET` refuses that key at boot.
