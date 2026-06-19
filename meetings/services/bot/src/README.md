# @vexa/bot — src

The bot worker's source. Hexagonal: the orchestrator core depends only on ports + contract
types; transports are adapters wired at the composition root.

| File | Role |
|---|---|
| `index.ts` | **composition root** — validates config, wires adapters (STUBBED this increment), runs the orchestrator, exits. The container entrypoint (`main`). |
| `config.ts` | `invocation.v1` boot config — parse + ajv-validate `VEXA_BOT_CONFIG`, fail-fast (P14). Exports the typed `Invocation`. |
| `ports.ts` | the port interfaces the core depends on: `JoinDriver · Pipeline · TranscriptSink · LifecycleSink · ActsSource · RecordingSink`. Pure (no transport types). |
| `orchestrator.ts` | the `lifecycle.v1` state machine (`createOrchestrator`) — joining → awaiting_admission → active → (completed \| failed). Depends only on ports. |
| `contracts.ts` | TS mirrors of the published `lifecycle.v1 · acts.v1 · transcript.v1` schemas + the executable `canTransition` machine. |
| `config.test.ts` | L1/L2 — drives the ajv parser against the invocation.v1 goldens; off-contract input fails fast. |
| `orchestrator.test.ts` | L2 — drives the machine with in-memory fake ports; asserts the full lifecycle.v1 sequence (ajv-conformant) + transcript.v1 routing. |

Tests run via `tsx` (no build step): `npx tsx src/<file>.test.ts`.
