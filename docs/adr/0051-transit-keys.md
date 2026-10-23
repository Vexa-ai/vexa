# ADR 0051 — Transit keys: identity issues pass-through access, the target asks identity

**Status:** proposed (draft, no code) · 2026-10-23 · builds on ADR-0041 (the edge signs identity),
subject-ticket.v1 (car 57), admin-api MIGRATION-0013/0014 · founder ruling 2026-10-23 (#1868):
*no hardcoded per-service scope like `tx`; a service that needs pass-through access gets a transit
key issued by the authorization module, different transit keys per downstream service when needed;
existing transcription keys keep working under the same rules.*

## Context

A **pass-through** caller holds a credential so that it can call one downstream service on a
person's or customer's behalf. Today Vexa has two shapes for that, and neither is identity's:

| Shape | Where | What is wrong with it |
|---|---|---|
| A **scope named after a service**: `tx` | `token_scope.py` `VALID_SCOPES`; hosted `transcription-gateway` (`main.py` `get_current_user`) | The scope's meaning depends on who reads it. On hosted, `tx` is "may call the transcription API"; on the OSS gateway it is "may read meetings and transcripts" (`core/meetings/routes.v1.json`). `transcription-gateway` decides alone: it checks the key's **prefix** (`vxa_tx_`, not the stored scopes), reads `api_tokens` directly, and mints `tx` keys straight into the table, bypassing admin-api. Adding the next pass-through service means a new scope, a new reader of `api_tokens`, a new minting path. |
| A **service door** with its own key | gateway `service_door.py`, `VEXA_FLOWS_SERVICE_KEY` | Correct for flows (one key, a fixed list of calls, subject tickets for who), but it is one door for one service. Nothing generalises it. |

Since MIGRATION-0013 the table holds only hashes, so a service that reads `api_tokens` itself must
also re-implement hashing in lockstep (`transcription-gateway` does, as a stopgap). Every direct
reader is one more place the storage format is load-bearing.

## Decision (proposed)

1. **A transit key is an identity credential with a target.** New credential class `transit`
   (`api_tokens.kind`, MIGRATION-0010's column), plus two fields: `audience` (the target service's
   registered name, e.g. `transcription`) and `actions` (what it may do there, e.g.
   `["audio.transcribe"]`). Scopes stay for the gateway's own routes (`bot`, …); a transit key carries
   no gateway scope and reaches no gateway route.

2. **Identity issues it; nothing else does.** `POST /admin/users/{id}/transit-keys {audience, actions,
   name?, expires_in?}`, admin tier. The audience must be a registered target (a small registry in
   identity: name → allowed actions → the service's identity for the validate call). An unknown
   audience or action is 422. The value is returned once and stored as a hash (MIGRATION-0013). A
   transit key is a person's key when a person asked for it (listed, revocable on their key page,
   labelled with its target), or `system` when a service mints it for itself (hidden, ending).

3. **The target asks identity, it does not decide.** `POST /internal/validate-transit {token,
   audience}` with the target's own service credential (internal tier + the target's name). Identity
   answers `{user_id, audience, actions, expires_at}` only when the key's audience equals the
   caller's registered name; any other audience is 401, exactly as an unknown key. The target never
   reads `api_tokens`, never knows the hash format, and caches a positive answer for at most 60 s
   (revocation latency is bounded by that). Billing stays the target's: identity answers who, not
   whether they can pay.

4. **Existing `tx` keys are transit keys on `transcription`.** No row is rewritten and no customer
   changes anything. Identity's validate-transit reads a `kind = 'key'` row whose scopes include
   `tx` as `{audience: "transcription", actions: ["audio.transcribe"]}` when the caller is
   `transcription`. The OSS gateway keeps honouring `tx` for meetings and transcripts exactly as
   today (`routes.v1.json` unchanged): the old keys keep working under the same rules on both
   sides. New transcription keys are minted as transit keys; `tx` is no longer offered for new keys
   once the hosted key page issues transit keys.

5. **`transcription-gateway` stops being an identity.** Its direct reads and its direct `INSERT`s
   into `api_tokens` are replaced by validate-transit and by the issue door. Its
   `TRANSCRIPTION_SERVICE_API_TOKEN` internal bypass for bots becomes a `system` transit key per bot
   dispatch (audience `transcription`, ending with the bot), or stays as an operator secret if the
   bot path is out of scope; that is the one open fork (below).

6. **The flows service door is the first transit audience on the gateway side, later.** Flows' key
   becomes a `system` transit key with `audience: gateway-service-door`; subject tickets keep saying
   *for whom*. Not part of the first cut: the door works and car 57 just landed.

## Consequences

- One issuer, one storage format, one revocation path for every credential. Hashing, expiry,
  sweeping and "a person sees only their keys" apply to transit keys for free.
- A new pass-through service is a registry entry, not a new scope and not a new table reader.
- Cost: one more network hop on the target's cold path (bounded by the 60 s cache), and the target
  must hold a service credential to call identity. `transcription-gateway` already sits beside
  admin-api on the same cluster.
- `tx` keeps two meanings for keys minted before the switch. That is the price of "old keys keep
  working"; it ends when those keys are rotated or expire, never by rewriting them.

## Open questions

- **Bot path.** Do bots keep the shared `TRANSCRIPTION_SERVICE_API_TOKEN`, or get a per-dispatch
  system transit key? The second is cleaner (revocable, attributable) and costs a mint per dispatch.
- **Registry home.** A table (`transit_audiences`) or config (`config.v1.json`)? Config keeps it
  reviewable and sealed; a table lets an operator add a target without a release.
- **Rename `tx` on the OSS gateway?** Out of scope here: renaming a gateway scope breaks every
  integrator's keys. Leave `tx` as the OSS meetings/transcripts scope.

## Not decided here

Code. This ADR is a draft for the founder's review; nothing in this branch implements it beyond the
hash-at-rest and credential-class groundwork (MIGRATION-0013/0014) it builds on.
