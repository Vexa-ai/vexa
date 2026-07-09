# routine.v1 — a saved recurring/event unit (trigger → plan) — UNSEALED (stub)

A routine = `(name, trigger, plan)` — the authoring entity a user creates (e.g. with `/routine`). It
**compiles down** to existing mechanism: `kind:scheduled` → a `schedule.v1` cron job whose body is a
`unit.v1` Invocation; `kind:event` → an event-source subscription that emits the same Invocation. It
adds **no new execution path** — the dispatcher + scheduler already run it. Owner = the `identity.v1`
subject (per-person).

**Status: UNSEALED** (stub) — sealed in the routines MVP. `gate:schema` validates its golden.
