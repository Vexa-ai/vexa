# calm — architecture-as-code (FINOS CALM 1.0)

A code-accurate [FINOS CALM](https://calm.finos.org/) model of the vexa runtime, with governance
controls and a reusable pattern. Self-contained module; validated in CI by `@finos/calm-cli`
(`pnpm gate:calm`). Ships in the open-core carve.

## Layout
| Path | Role |
|---|---|
| `architecture.calm.json` | the model — services, runtime-spawned workers, redis transcript fabric, durable stores, external-STT egress boundary (runtime/data-flow lens) |
| `controls/single-writer.requirement.json` | P23: each data carrier declares exactly one producer |
| `controls/render-only.requirement.json` | consumers (the terminal) render, never re-derive |
| `controls/no-egress.requirement.json` | any edge carrying tenant data off-tenant must declare its egress posture |
| `patterns/meeting-intelligence.pattern.json` | the reusable pattern a meeting-intelligence deployment must conform to |

## Gate
```bash
pnpm gate:calm   # calm validate -p patterns/… -a architecture.calm.json
```
Fail-closed: an architecture that omits the STT data-egress control, the render-only terminal, or any
required node is rejected — vexa as a CALM pattern others can `calm generate` from and validate against.

## Notes
- `requirement-url`s point at the canonical published location (`vexa.ai/calm/controls/…`); the
  authoritative source schemas live here in `controls/`.
- Runtime/data-plane lens; complementary to the repo's top-level `architecture.calm.json`
  (the contract/module decomposition used by `gate:dataflow`).
- Display fields avoid `{}` braces so `calm docify` (MDX) renders cleanly.
