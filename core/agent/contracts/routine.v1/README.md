# routine.v1 — a saved recurring/event unit (trigger → plan) — UNSEALED (stub)

**Retired by ADR-0046.** A routine is now a person's own flow — a schedule trigger and the
`agent_run` step in `flows.v1` `FlowDefinition`, written in the caller's scope (`user:<uid>`).
Nothing compiles a `routine.v1` to a `schedule.v1` job any more; old `routines/*.md` files are
converted into flows once, at startup. This stub stays until the `/api/routines` adapter is removed
in 0.14.0.

**Status: UNSEALED** (stub), never sealed. `gate:schema` validates its golden.
