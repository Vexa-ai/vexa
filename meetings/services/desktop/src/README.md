# desktop/src

[`desktop.ts`](desktop.ts) — the host: `startDesktop()` wires the ingest WS (decode `capture.v1`
→ `gmeet-pipeline` → real STT) to an in-memory store + the gateway (`/transcripts`, `/bots`,
`/recordings/{p}/{n}`, `/ws`).

[`recording-sink.ts`](recording-sink.ts) — the **recording.v1 receiver PORT** (P5; see ADR-0005). A
pure assembly core (no WS, no disk): accumulates recording.v1 chunks per session and on `is_final`
assembles the master via `@vexa/recording` `buildRecordingMaster`, handing the bytes to an injected
`onMaster`. `desktop.ts` fills `onMaster` with the disk-write + gateway-serve adapter.

Tests (`gate:node` runs all three): [`recording-sink.test.ts`](recording-sink.test.ts) is the **L2**
unit (synthetic chunks via an in-memory fake → assert the assembled master); [`recording-e2e.test.ts`](recording-e2e.test.ts)
is the **L3** integration (synthetic recording.v1 over the real ingest WS → decode → sink → a REAL file
on disk → served by the gateway; no live meeting); [`desktop-e2e.live.test.ts`](desktop-e2e.live.test.ts)
is the end-to-end transcript gate against real STT (skips without `VEXA_TX_KEY` + `EVAL_CACHE`).
