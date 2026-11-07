# calm — governance controls + pattern (FINOS CALM 1.2)

The architecture model itself is the repo-root [`architecture.calm.json`](../architecture.calm.json) —
the single source of truth: module/service/contract inventory AND the runtime data plane (carriers,
single-writer ownership, flows, egress boundary). This directory holds what governs it.

## Layout
| Path | Role |
|---|---|
| `controls/single-writer.requirement.json` | P23: each data carrier declares exactly one producer |
| `controls/render-only.requirement.json` | consumers (the terminal) render, never re-derive |
| `controls/no-egress.requirement.json` | any edge carrying tenant data to another system declares its egress posture |
| `patterns/meeting-intelligence.pattern.json` | the reusable pattern a meeting-intelligence deployment must conform to |

## Gates (both run in CI)
```bash
pnpm gate:calm       # calm-cli 1.65.0: CALM 1.2 validity, every control resolves and validates, pattern conformance
pnpm gate:dataflow   # completeness vs disk, single-writer/render-only enforcement, code reality-diff, view staleness
```
Fail-closed: an architecture that omits the STT data-egress control, the render-only terminal, or any
required node is rejected — vexa as a CALM pattern others can `calm generate` from and validate against.

## Views
`docs/views/` holds deterministic projections generated from the chart (`pnpm arch:dsl --write`):
the compact `architecture.dsl`, plus Mermaid views — containers, carrier ownership (P23), one sequence
diagram per flow, deployment/spawn topology, and the tenant egress boundary. Never hand-edit them;
`gate:dataflow` fails when they are stale.

## Notes
- Every `requirement-url` is a repository path (`calm/controls/<control>.requirement.json`), so
  `calm validate -a architecture.calm.json` run from the repository root resolves it with no URL
  mapping and no network. `gate:calm` refuses a requirement that does not resolve, including any URL.
- After any chart edit: `pnpm arch:dsl --write && pnpm seal:arch` (the chart is the asserted-true
  baseline; drift from the seal is deliberate-only).
