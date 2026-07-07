# docs — the constitution (ARCHITECTURE.md) + ADRs (adr/) + runbooks

_Governed by `docs/ARCHITECTURE.md` (P1–P21). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._

## Key explainers
- **[`WORKSPACES.md`](WORKSPACES.md)** — the workspace model (three tiers `_global`/normal/`_system`,
  personal + normal single-rank workspaces), the sharing model, and live in-meeting collaboration —
  what's delivered and what's deferred.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — the constitution (principles P1–P21) · [`CONTROL-PLANE.md`](CONTROL-PLANE.md) — control-plane boundary · [`adr/`](adr) — decisions.
