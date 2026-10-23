# signin.v1 — who may sign in, and who may become the administrator

The internal wire between the terminal's server (every sign-in door) and admin-api, which holds every
input and decides (`app/signin_allow.py`, `app/claim_code.py`). The terminal asks and obeys; it holds
no list of its own. Internal tier only (`X-Internal-Secret`).

| Route | Request | Response |
|---|---|---|
| `POST /internal/signin-admission` | `SigninAdmissionRequest` | `SigninAdmissionResponse` |
| `POST /internal/bootstrap-admin` | `AdminClaimRequest` | `AdminClaimResponse` |
| `POST /internal/admin-claim/check` | `ClaimCodeCheckRequest` | `ClaimCodeCheckResponse` |
| `GET /internal/instance` | — | `InstanceState` |
| `POST /internal/signin-links/redeem` | `SigninLinkRedeemRequest` | `SigninLinkRedeemResponse` (200, the first time); `409` already redeemed or expired; `503` the record could not be written |
| `PUT /internal/users/{id}/provider-subject` | `ProviderSubjectBindRequest` | `ProviderSubjectBindResponse` (200); `409` the account is bound to another identity of that provider; `404` unknown account |

**The reason vocabularies are the contract.** `AdmittedReason`, `RefusedReason` and `ClaimReason`
are generated into both languages by `gen.mjs` — admin-api's `app/signin_wire.py` and the terminal's
`src/app/api/auth/signinWire.ts` — so neither side spells a reason. A reason added here reaches both
on the next `node gen.mjs`; a generated file that no longer matches fails `validate.mjs --check`
(gate:schema). admin-api serves the shapes as pydantic models, so a reason the schema does not know
fails the response instead of being read as a refusal.

`SigninAdmissionResponse` is `admitted: true` with an admission reason, or `admitted: false` with
`not-allowed` — one refusal reason on purpose: the answer must not tell a caller which list an
address is missing from. A caller admits only on a body that conforms and says `admitted: true`.

Validate: `node validate.mjs --check`. Sealed in `contracts.seal.json` (gate:contract-version):
additive changes re-seal, breaking ones go to `signin.v2`.

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._
