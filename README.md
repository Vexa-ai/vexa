# vexa 0.12

Turn meetings and docs into knowledge that sandboxed agents act on.

The clean reimplementation. **Microservices, each internally a modular monolith — contract-bounded at
two scales** (published schemas between services, ports within). See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md):
the constitution (P1–P21), the gate suite, and the development process.

## Layout
| Dir | Role |
|---|---|
| `core/` | **the platform backend** — the five domains below, grouped (an organizing folder, not a domain) |
| `core/runtime/` | ① kernel — spawn/execute workloads + mount the workspace |
| `core/meetings/` | ② capture — join → capture → transcript |
| `core/agent/` | ③ execution — transcript → governed action |
| `core/identity/` | access · accounts · tokens · audit |
| `core/gateway/` | the edge — auth · routing · WS fan-out |
| `integrations/` | `out/` emit adapters · `in/` connectors |
| `clients/` | dashboard · extension · desktop · telegram · mcp |
| `core/<domain>/contracts/` | published contracts **nest with their owner domain** (JSON Schema + goldens) — no top-level `schemas/` (see ARCHITECTURE §3) |
| `sdks/` | published client libraries |
| `tools/` · `deploy/` · `docs/` | dev tooling · deployment topologies · docs + ADRs |

## Gates — the compliance bar (green or it didn't happen, P9)
`pnpm gates` runs **readme · isolation · exports · graph · schema**; plus `pnpm typecheck build test`.
An artifact "exists" only when gate-green.

## Status
Past **Stage 3.3.** The runtime kernel (process · docker · k8s, `runtime.v1`) and the **meetings**
capture plane are real: gmeet + mixed lanes (zoom · teams · youtube), the `recording.v1` desktop
receiver (ADR-0005), the L4 eval harness, and the `join` · `remote-browser` (authenticated) bricks.
Six contracts sealed; **8 gates green**. Next, per the 0.12 release plan: harden the process, then the
standalone bot · meeting-api · dashboard · agents · deploy. `agent · identity · gateway · integrations
· sdks` are still scaffolds.
