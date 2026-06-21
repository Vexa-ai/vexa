# @vexa/bot — the meetings ephemeral container WORKER

_meetings/ · service (P7 worker) · join → capture → transcribe → emit → die._

The bot is a stateless, disposable container (12-Factor): it boots from one env var, joins one
meeting, runs the capture→transcribe→emit data plane, and exits. One bot per meeting (horizontal
fan-out). Built as a **modular monolith behind ports** — the orchestrator core is offline-provable;
every transport is an adapter wired only at the composition root.

```
VEXA_BOT_CONFIG (invocation.v1)  ─►  config.ts (ajv-validate, fail-fast, P14)
                                       │
                          composition root (index.ts) wires the adapters ▼
   ┌─────────────────────────────────────────────────────────────────────────────┐
   │ orchestrator.ts — the lifecycle.v1 STATE MACHINE (pure; depends only on ports)│
   │   joining → awaiting_admission → active → (completed | failed)                │
   └─────────────────────────────────────────────────────────────────────────────┘
        ▲ JoinDriver   ▲ Pipeline        ▲ ActsSource    ▼ TranscriptSink ▼ LifecycleSink ▼ RecordingSink
   @vexa/join +    @vexa/{gmeet,mixed}-   redis pub/sub    redis stream     HTTP callback   @vexa/recording
   @vexa/remote-   pipeline + transcribe- (acts.v1)        (transcript.v1)  (lifecycle.v1)  → upload
   browser (auth)  whisper + recording
```

## Surface
The **composition root** [`src/index.ts`](src/index.ts) exposes `main(env) → Promise<exitCode>` and
runs as the container entrypoint. The reusable core:
- [`src/config.ts`](src/config.ts) — `loadInvocation(env)` / `parseInvocation(raw)` → typed `Invocation`,
  validated against `invocation.v1` with ajv (schema loaded by path; the goldens are the spec, P8).
- [`src/ports.ts`](src/ports.ts) — `JoinDriver · Pipeline · TranscriptSink · LifecycleSink · ActsSource ·
  RecordingSink` (pure interfaces; no transport types).
- [`src/orchestrator.ts`](src/orchestrator.ts) — `createOrchestrator(inv, deps) → { run, handle }`, the
  state machine emitting the correct `lifecycle.v1` event at each transition.
- [`src/contracts.ts`](src/contracts.ts) — TS mirrors of the published `lifecycle.v1 · acts.v1 ·
  transcript.v1` schemas + the executable `canTransition` machine.

## Deps
`@vexa/{join, remote-browser, recording, record-chunker, gmeet-pipeline, mixed-pipeline,
transcribe-whisper}` (the meetings bricks it composes, by their published front doors, P6) + `ajv` /
`ajv-formats` (invocation.v1 + lifecycle.v1 boot validation). It imports no other domain's internals
and no transport client libs — the real redis/HTTP/browser are adapters at the composition root.
Enforced by [`scripts/check-isolation.js`](scripts/check-isolation.js) (`gate:isolation`, P2).

## Status (this increment)
The gate-green **core** is delivered: config + ports + orchestrator + L2 tests. The live transports in
`index.ts` are **stubbed** placeholders (clearly marked `TODO(live)`) — the seam exists now (P16); the
next increment swaps each stub for its real adapter (join over a browser, redis bus, HTTP callback,
recording upload). The **voice agent** (acts.v1 speak/chat/screen/avatar) is **deferred** — out of scope.

## Verify
```bash
npx tsx src/config.test.ts          # L1/L2 — goldens parse, off-contract input fails fast
npx tsx src/orchestrator.test.ts    # L2   — full lifecycle.v1 sequence (ajv-conformant) + transcript routing
npx tsc --noEmit -p .               # typecheck
node scripts/check-isolation.js     # gate:isolation
```
`pnpm --filter @vexa/bot test` runs both L2 suites (cheapest first). No browser / redis / STT —
every port is an in-memory fake.
