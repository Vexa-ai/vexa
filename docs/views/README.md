# docs/views — generated architecture projections

Deterministic views carved from the SSOT chart (`architecture.calm.json`). **Never hand-edit** —
regenerate with `pnpm arch:dsl --write`; `gate:dataflow` fails when any view is stale.

| File | View |
|---|---|
| `architecture.dsl` | compact text projection — the always-in-context LLM index |
| `containers.mmd` | C4-ish container view: systems, services, clients, protocols |
| `ownership.mmd` | P23 data-carrier ownership: write=solid, read=dashed, multi-writer carriers dashed-outline |
| `flow-*.mmd` | one sequence diagram per declared flow (live transcript, agent dispatch) |
| `deployment.mmd` | runtime spawn topology (runtime.v1 workloads) |
| `egress.mmd` | tenant trust boundary; edges carrying a data-egress control |

GitHub renders the `.mmd` files natively (Mermaid).
