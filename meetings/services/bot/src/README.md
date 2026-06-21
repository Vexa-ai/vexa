# @vexa/bot — src

The bot worker's source. Hexagonal: the orchestrator core depends only on ports + contract
types; transports are adapters wired at the composition root.

**Status: 2b adapters wired (L1/L2/L3 green); the browser-resident legs are L4-pending → O6 (VM).**

| File | Role |
|---|---|
| `index.ts` | **composition root** — validates config, launches the browser, wires the REAL adapters, runs the orchestrator, exits. Speak acts are tee'd to a voice handler (orchestrator core untouched). The container entrypoint (`main`). |
| `config.ts` | `invocation.v1` boot config — parse + ajv-validate `VEXA_BOT_CONFIG`, fail-fast (P14). Exports the typed `Invocation`. |
| `ports.ts` | the port interfaces the core depends on: `JoinDriver · Pipeline · TranscriptSink · LifecycleSink · ActsSource · RecordingSink`. Pure (no transport types). |
| `orchestrator.ts` | the `lifecycle.v1` state machine (`createOrchestrator`) — joining → awaiting_admission → active → (completed \| failed). Depends only on ports. |
| `contracts.ts` | TS mirrors of the published `lifecycle.v1 · acts.v1 · transcript.v1` schemas + the executable `canTransition` machine. |
| `join-driver.ts` | **JoinDriver** — wraps `@vexa/join` `joinMeeting`/leave/removal (guest + authenticated); maps `JoinState`→`BotStatus`. |
| `pipeline.ts` | **Pipeline** — `google_meet`→`@vexa/gmeet-pipeline` (per-channel, glow-named) · `zoom`/`teams`→`@vexa/mixed-pipeline`; STT via `@vexa/transcribe-whisper`; lane sink → bot `TranscriptSink.publish`. Exposes `feedAudio`. |
| `recording.ts` | **RecordingSink** — `@vexa/recording` assembler (`buildRecordingMaster` on `is_final`/`close`) → upload (`RecordingService`). |
| `capture-bridge.ts` | **L4-pending (O6)** — browser launch (+ S3 auth profile), page-side capture inject + PCM pump → `pipeline.feedAudio`, and the speak controller. Browser-resident; not unit-provable — validated on the VM. |
| `*.test.ts` | L1/L2/L3 — config (ajv goldens) · orchestrator (lifecycle.v1 sequence, fake ports) · lifecycle-http/transcript-redis/acts-redis (transports) · **pipeline (L3: capture→lane→stt→publish, overlap no cross-mislabel)** · **recording (L3: webm/wav/seq)**. |

Tests run via `tsx` (no build step): `npx tsx src/<file>.test.ts`; all chained in `npm test`.
