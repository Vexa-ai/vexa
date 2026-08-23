# core/flows — the reaction engine

Durable product workflows as TWO Postgres tables and ONE leased worker loop. A fact enters as a
sealed envelope, becomes exactly one `reaction` row (dedup by constraint), is worked one step at a
time, and every touch of the outside world leaves an `effect_receipt` a retry consults before acting.

- **ADR:** the reaction table with receipts IS the engine; Dapr is a possible future executor of the
  same steps, never the foundation. Full design: docs/WORKFLOW-ARCHITECTURE-PRD.md + the published
  architecture page.
- **Isolation:** `src/flows/` (the engine) imports stdlib only at module scope — no meetings, no
  agent, no runtime, no third-party. Step implementations (`src/flows_steps/`) and flow definitions
  (`src/flows_defs/`) sit OUTSIDE the engine's import graph; the engine receives them as values.
- **No scheduler:** time is a column (`next_run_at`); the worker's `SKIP LOCKED` poll is the only
  clock consumer. Waits cost nothing; escalation is `blocked_deadline`, one column not a service.
- **Testing:** the whole failure matrix runs offline on stdlib sqlite with `FakeClock` and fake
  steps — zero domains attached. Postgres is the production dialect (see `schema.sql`).

Layering (dependency, not data): flows → domain HTTP APIs → runtime. Domains never know flows
exists — they only write outbox facts. Tactical retries (bot reconnects, webhook delivery) stay in
their components; strategic, crash-surviving coordination lives here.
