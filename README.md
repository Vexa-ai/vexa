# vexa 0.12

The clean reimplementation. **Microservices, each internally a modular monolith — contract-bounded at
two scales** (published schemas between services, ports within). See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md):
the constitution (P1–P12), the gate suite, and the development process.

## Layout
| Dir | Role |
|---|---|
| `runtime/` | ① kernel — spawn/execute workloads + mount the workspace |
| `meetings/` | ② capture — join → capture → transcript |
| `agent/` | ③ execution — transcript → governed action |
| `identity/` | access · accounts · tokens · audit |
| `gateway/` | the edge — auth · routing · WS fan-out |
| `integrations/` | `out/` emit adapters · `in/` connectors |
| `clients/` | dashboard · extension · desktop · telegram · mcp |
| `schemas/` | published contracts (JSON Schema + goldens + TS/Py codegen) |
| `sdks/` | published client libraries |
| `tools/` · `deploy/` · `docs/` | dev tooling · deployment topologies · docs + ADRs |

## Gates — the compliance bar (green or it didn't happen, P9)
`pnpm gates` runs **readme · isolation · exports · graph · schema**; plus `pnpm typecheck build test`.
An artifact "exists" only when gate-green.

## Status
**Stage 0b** — scaffold + tooling + gates, green-on-empty. Build order is contract-first: `schemas/` is next (Stage 1).
