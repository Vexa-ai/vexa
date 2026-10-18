# delegation.v1 — the token a worker presents as the person it acts for

A chat or routine worker reaches Vexa through the MCP edge. It holds no API key of its own: for each
dispatch, agent-api mints a short-lived **delegation token** that names the person the worker acts
for and the ceiling of what this dispatch may touch. identity verifies it.

```
vxd_<base64url(header)>.<base64url(claims)>.<base64url(HMAC-SHA256(key, "<header>.<claims>"))>

header  {"alg":"HS256","typ":"vxdlg"}
claims  {"aud":"vexa-mcp", "exp":…, "iat":…, "jti":"…",
         "scope":{"regime":"human"|"autonomous", "workspaces":"*"|["slug",…], "shared":true?,
                  "grants":["bot.request",…]?, "flow":"user:<uid>/<name>"?},
         "sub":"<user id or workspace principal id>", "target":"<slug>"?, "unit":"<unit id>"?,
         "principal_kind":"workspace"?, "principal_workspace":"<slug>"?, "on_behalf_of":"<user id>"?}
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

**Unit** is the agent unit the token was minted for — the dispatch id of a chat thread
(`agent-<subject>-chat-<thread>[-g<gen>]`) or a background job (`agent-<subject>-<trigger>-<digest>`).
It is the **session** every call the worker makes belongs to. It grants nothing; it is signed so that
a worker cannot name another session. identity answers it (`ValidateResponse.delegation.unit`), the
gateway signs it into the identity it forwards (`gateway-identity.v1` `delegation.unit` →
`x-user-delegation-unit`), services log it as `logevent.v1` `session`, and the credential broker
records it as `audit.session` (Vexa-ai/vexa#1783). A refresh carries it over unchanged.

**Shared** (`scope.shared`, ADR-0048 decision 4) is present, and `true`, when the turn answers into
a conversation other people read. It travels on identity's answer and the gateway's signed identity
(`delegation.shared`, header `x-user-delegation-shared: 1`), and every service refuses such a worker
a person-wide read — the person's mail, calendar and other connections (credential broker,
agent-api), their meetings, transcripts and recordings (meeting-api) — with one body,
`reason: shared_conversation`. Absent on a private turn, whose token is byte-identical to before.

**Workspace principal** (D2, 0.13.4; security review 2026-10-17 C1). A room's turn runs as its
workspace's agent, not as any person.
- identity keeps one numeric principal of kind `workspace` per workspace (admin-api `principals`,
  MIGRATION-0009). Its id is a dedicated account row that owns nothing of a person's, and the row
  names the account it bills to.
- The token's `sub` is that id. Three claims describe it: `principal_kind: "workspace"`,
  `principal_workspace` and `on_behalf_of` (the speaker).
- The minter refuses a workspace principal without `shared`, with `workspaces: "*"`, with grants,
  or naming itself as `on_behalf_of`. It refuses the three claims on a person's token, so every
  person token stays byte-identical.
- identity checks the claims against its table (kind, workspace, `active`) and answers
  `delegation.principal_kind`, `principal_workspace`, `on_behalf_of` and `bills_to` beside `shared`
  (identity.v1 `ValidateDelegation`). The gateway signs them into `x-user-principal-kind`,
  `x-user-principal-workspace`, `x-user-delegation-on-behalf-of` and `x-user-bills-to`
  (gateway-identity.v1).
- agent-api asks identity for the principal (`POST /internal/principals/workspace`) and mints it
  only while `VEXA_ROOM_WS_PRINCIPAL` is on. That switch is off by default in 0.13.4
  (`control_plane/dispatch.py`).
- The change is additive, so delegation.v1 is re-sealed in a `lane:contract` review rather than
  opening a v2.

**Grants** (`scope.grants` with `scope.flow`, flows phase 0) are a routine's delegated authority.
A routine runs unwatched, so every verb that needs a person refuses it (`human_session_required`); a
person who makes a routine in their chat to do one such thing — "send a bot to this meeting at 12:13" —
grants it that verb by name when they create it (flow version `params.grants`, shown to them as "this
routine may: send bots"). agent-api mints the grant only on that routine's autonomous turn, capped by
`_global/POLICIES.md` `routine_unattended_actions`; identity answers it, the gateway signs it
(`delegation.grants`/`delegation.flow`, headers `x-user-delegation-grants`/`x-user-delegation-flow`),
and a service that refuses an unwatched caller lets exactly the granted verb through (`granted(scope,
action)`), logging each use. The vocabulary is closed: `bot.request`, `bot.stop`, `flow.retire_self`.

**What the ceiling admits** is defined once, in `delegation.py`: `ceiling_allows(workspaces,
workspace, subject=)` — `"*"`, an empty id or the subject's own id, or an id in the list — and
`ceiling_reads`, which also admits `_global`, the company layer every subject reads, and any id the
list carries as `ro:<id>` (`READ_ONLY_PREFIX`). An unwatched dispatch lists the workspaces it mounted
read-write by id and the ones it mounted read-only as `ro:<id>`, so its tools read everything on its
disk and write only where it may write; `ceiling_allows` never matches an `ro:` entry, and a verifier
that predates the prefix matches nothing with it (fails closed). The wire shape is unchanged: the
entries are strings. agent-api's
resolvers apply them to a named workspace (`control_plane/ceiling.py`), and identity applies
`ceiling_reads` to the person's memberships when it answers a delegation token, so the workspaces a
worker reads meetings through are the ones inside its ceiling.

**Verification order**, with the reason a verifier gives for each refusal (`RefusalReason`):
`vxd_` prefix (`not_delegated` — not this scheme, try the others) → three parts (`malformed`) →
signature, constant time (`bad_signature`) → claims are a JSON object (`malformed`) →
`aud == "vexa-mcp"` (`bad_audience`) → non-empty `sub` (`malformed`) → `now < exp` (`expired`) →
`jti` not on a denylist (`revoked`). No claim is read before the signature verifies. identity answers
`401 Invalid delegation: <reason>`; then `401 Invalid delegation: scope` when the verified `scope` is
not a `Scope`, and `401 Invalid delegation: no such user` when `sub` names no account.

**Revocation.** A token ends with the unit it was minted for. agent-api records each token's `jti`
against its unit at dispatch and, once the runtime no longer runs that unit (it completed, idled out,
was stopped or failed), writes `vexa:delegation:revoked:<jti>` to the service Redis with the token's
remaining lifetime as its expiry (`core/agent/control_plane/delegation_revocation.py`). agent-api
also holds `vexa:delegation:live:<jti>` from the moment it records a token until it revokes it or the
token expires; it is the only writer of every `vexa:delegation:*` key, and identity only reads. identity admits a verified token only while its live key exists and its revoked key
does not, and answers `401 Invalid delegation: revoked` otherwise — so a key the store evicted, or a
token agent-api never recorded, is refused rather than admitted — and `503` when it cannot read the
store, so an unreadable store never reads as "not revoked"; API keys never touch it
(`admin_api/app/delegation_revocation.py`). The key names are held equal on both sides by
`gate:fact-parity` (facts `delegation-revocation-key`, `delegation-live-key`). `verify_delegation(…, revoked=…)` remains the
in-module form; the dogfood rig's own verifier passes one.

**Refresh.** A unit still running when half of its token's life has passed is handed a new token:
the same `sub`, `scope` and `target`, a new `jti` and a full lifetime, minted by agent-api from its own
record of the current one (`core/agent/control_plane/delegation_refresh.py`). The worker picks it up
before its next turn. The replaced token is not revoked while the unit runs; it ends at its own `exp`,
or with the unit. A unit that has ended is never refreshed.

The whole lifecycle — minting, refresh, revocation and admission — is described for operators in
`docs/docs/architecture/identity-and-trust.mdx` (§ Worker delegation tokens).

## Files

- `delegation.py` — the minter and the verifier. Standard library only, and **vendored byte for
  byte** into `core/agent/shared/` and `core/identity/services/admin-api/src/admin_api/`;
  `gate:fact-parity` (fact `delegation-token`) compares the three. Edit this one and copy it out.
- `delegation.schema.json` — the header, the claims, the refusal reasons and the vector shapes.
- `golden/` — claims, minting vectors and refusal vectors, made with a published test key.
  `validate.mjs` re-mints and re-verifies them in Node (gate:schema), and
  `core/agent/tests/test_delegation_vectors.py` does the same in Python with the vendored module
  (gate:python). Every service that loads `VEXA_MCP_DELEGATION_SECRET` refuses that key at boot.
