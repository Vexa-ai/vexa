# ADR 0045 — A worker's delegation token lives as long as its unit

**Status:** accepted · 2026-10-10 · for v0.13.2 ([#1784](https://github.com/Vexa-ai/vexa/pull/1784),
[#1783](https://github.com/Vexa-ai/vexa/issues/1783)) · departs from P7 for one value; applies P18, P21 and P23

## Context

An agent worker reaches the vexa MCP with a delegation token (`vxd_…`, delegation.v1) that agent-api
mints per dispatch and hands over in the worker's environment. Identity verified it statelessly
(signature, audience, expiry), so a token stayed good until its `exp` whatever became of its worker,
and with a one-hour lifetime a leaked worker environment was a bearer credential for up to an hour.
Shortening the lifetime alone broke the other side: a warm worker reads its environment once, so a
conversation or a background job that outlived the token lost its tools in the middle of the work,
and the model carried on without them.

Three properties were wanted together: the token ends when its unit ends; a live unit never runs
without its tools because time passed; and nothing fails quietly.

## Decision

1. **Revocation, with one writer.** agent-api records each token's `jti` against its unit
   (`vexa:delegation:unit:<id>`, `vexa:delegation:units`) before the spawn, and withholds a token it
   cannot record. When the runtime no longer reports the unit starting or running, agent-api writes
   `vexa:delegation:revoked:<jti>` with the token's remaining life
   (`core/agent/control_plane/delegation_revocation.py`). agent-api is the only writer of every
   `vexa:delegation:*` key. Identity reads `revoked:<jti>` for every verified `vxd_` bearer
   (`admin_api/app/delegation_revocation.py`). The key name is held equal on both sides by the
   `delegation-revocation-key` parity fact.

   agent-api also holds `vexa:delegation:live:<jti>` from recording a token until it revokes it
   (deleted) or the token expires, and identity admits a verified token only while that key exists
   and no revocation does. A denylist alone fails open — a revoked key the store evicted, or a token
   never recorded, would read as "not revoked" — so the positive record makes a lost key refuse the
   token. Held equal on both sides by the `delegation-live-key` parity fact.

2. **Identity fails closed when it cannot read the store.** A `vxd_` bearer is answered `503` while
   Redis cannot be read; it never reads as "not revoked". API keys never touch the store.

3. **A live unit's token is replaced at half its life.** The same sweep re-mints, for each unit the
   runtime still runs, a token for the same person, regime, ceiling and target with a new `jti`, from
   agent-api's own record (`vexa:delegation:current:<id>`, which no worker can reach), records it for
   revocation, and publishes it at `unit:<id>:delegation` (`delegation_refresh.py`). The replaced
   token is not revoked early while its unit runs: a turn that started with it keeps it until its own
   `exp` (half a lifetime, 900 s at the 1800 s default), and it is revoked with the unit's other
   tokens when the unit ends. A unit the runtime does not run is never refreshed.

4. **The worker's credential arrives through Redis after boot — the P7 departure.** P7 says all
   config arrives by env. This one value does not, after the first: the worker reads
   `unit:<id>:delegation` before every turn, write-back and job and rewrites its MCP attachment
   (`worker/engine.py` `DelegationRefresh`), because a credential that must change while the process
   lives cannot arrive by env. The departure is bounded: it is the only value, the worker's Redis user
   may read that key and not write it (a `%R~` selector, `workload_redis.py`), the boot token still
   arrives by env, and the worker takes it out of its own environment at boot so no harness
   subprocess inherits it.

5. **A turn that outlives its token fails loud.** A vexa MCP call that fails at or after the
   attached token's `exp` ends the turn with a typed fault (`source: "vexa-tools"`,
   `kind: "access_expired"`; `worker/tool_access.py`), which the chat renders with a Retry.

6. **An unreadable runtime answer is an error, never "nothing is live".** Both unit-end sweepers act
   on what is absent from the runtime's live set, so `RuntimeHttpClient.live_workloads` raises the
   runtime's `bad_response` fault for a body that is not a list of workloads, and a sweep that cannot
   read the live set acts on nothing.

## Consequences

- **Identity's authorization answer for a worker now depends on Redis.** A Redis outage refuses every
  `vxd_` bearer (`503`) until it returns; people's own API keys are unaffected. Revocations live in
  Redis with AOF on in compose and Helm; if Redis loses its data, unrevoked tokens live until their
  `exp`.
- **Two reconcilers poll the runtime's live set independently** — the Redis ACL sweeper
  (`workload_redis.start_sweeper`, 15-minute grace) and the delegation reaper
  (`delegation_revocation.start_reaper`, 30 s interval, 120 s grace for a token whose spawn may still
  be on its way). Both skip a sweep whose live set they cannot read. Neither reports its last good
  sweep on `/health`; merging them into one reconciler with a liveness signal is follow-up work.
- **Revocation lags a unit's end** by up to one reaper interval, plus the grace for a token younger
  than 120 s. A refreshed token is recorded past the grace and is revoked on the first sweep after its
  unit ends.
- **`REDIS_WORKLOAD_ACL=shared`** gives workers the service connection, so a worker could delete a
  revocation key or write its own delivery key. That mode already trusts every worker; the refresh
  never re-mints from the worker-readable copy.
- **A single turn longer than half a lifetime** that started just before a refresh loses its tools at
  the old token's `exp`, loudly. Raising `VEXA_MCP_DELEGATION_TTL_SEC` widens the headroom and the
  window a leaked token is good for, together.
