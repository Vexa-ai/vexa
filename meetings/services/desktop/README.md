# @vexa/desktop — the meetings all-in-one host (gmeet subset)

_meetings/ · service · the data plane in ONE process — no Docker / Postgres / Redis._

Composes the validated gmeet spine into a runnable backend:

```
capture.v1 ─► ingest WS (:9099)
   ├─ decode frames     @vexa/capture-codec
   ├─ gmeet channels ─► @vexa/gmeet-pipeline   (channel-routed, glow-named)
   ├─ STT egress        @vexa/transcribe-whisper (the real backend)
   └─ store + deliver ─► in-memory + gateway
gateway (:8056): POST /extension/sessions · GET /bots · GET /transcripts/{p}/{n} · WS /ws
```

It's the **same bricks the cloud splits** across `meeting-api` + collector + `gateway/`, composed
as one deployable. Crucially its gateway serves `/transcripts` **locally, unauthenticated** — so the
[eval](../../eval/) `judge` can score it with zero cloud / zero scope `403`s. The store is in-memory
(sqlite is a later refinement).

## Surface
`startDesktop({ ingestPort, gatewayPort, txUrl, txToken })` → `{ ingestPort, gatewayPort, close() }`.
Front door: [`src/desktop.ts`](src/desktop.ts).

## Run
```bash
TRANSCRIPTION_SERVICE_URL=https://transcription.vexa.ai TRANSCRIPTION_SERVICE_TOKEN=… \
  pnpm --filter @vexa/desktop dev          # ingest ws://localhost:9099/ingest · gateway :8056
```

## Verify
`pnpm --filter @vexa/desktop test` — `desktop-e2e.live.test.ts`: starts the host, feeds known TTS
clips as `capture.v1` over the ingest WS, reads `/transcripts`, asserts glow-attributed, schema-valid
`transcript.v1`. Skips without `VEXA_TX_KEY` + `EVAL_CACHE` (turbo passes them through). Validates the
**whole composition** end-to-end against real STT.
