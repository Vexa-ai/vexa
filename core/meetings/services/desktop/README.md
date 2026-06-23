# @vexa/desktop — the meetings all-in-one host (gmeet subset)

_meetings/ · service · the data plane in ONE process — no Docker / Postgres / Redis._

Composes the validated gmeet spine into a runnable backend:

```
capture.v1 ─► ingest WS (:9099)
   ├─ decode frames     @vexa/capture-codec
   ├─ gmeet channels ─► @vexa/gmeet-pipeline   (channel-routed, glow-named)
   ├─ STT egress        @vexa/transcribe-whisper (the real backend)
   ├─ recording.v1   ─► RecordingSink port → @vexa/recording buildRecordingMaster → master file
   │   (SAME ingest WS; decodeRecordingChunk discriminates the chunk from an audio frame)
   └─ store + deliver ─► in-memory + gateway
gateway (:8056): POST /extension/sessions · GET /bots · GET /transcripts/{p}/{n}
              · GET /recordings/{p}/{n} (serve the assembled master) · WS /ws
```

The desktop is the **LOCAL recording.v1 receiver** (ADR-0005): in the all-in-one path it subsumes
meeting-api's assembly role over the SAME contract. Recording chunks (the extension's offscreen tee →
`@vexa/record-chunker`) ride the existing ingest WS as `recording.v1` frames; assembly lives behind the
`RecordingSink` port (`src/recording-sink.ts`), with WS + disk as adapters in `desktop.ts`. Masters land
in `VEXA_RECORDINGS_DIR` (default `.recordings/`).

It's the **same bricks the cloud splits** across `meeting-api` + collector + `gateway/`, composed
as one deployable. Crucially its gateway serves `/transcripts` **locally, unauthenticated** — so the
[eval](../../eval/) `judge` can score it with zero cloud / zero scope `403`s. The store is in-memory
(sqlite is a later refinement).

## Surface
`startDesktop({ ingestPort, gatewayPort, txUrl, txToken, recordingsDir })` →
`{ ingestPort, gatewayPort, recordingsDir, close() }`. Front door: [`src/desktop.ts`](src/desktop.ts).

## Run
```bash
TRANSCRIPTION_SERVICE_URL=https://transcription.vexa.ai TRANSCRIPTION_SERVICE_TOKEN=… \
  pnpm --filter @vexa/desktop dev          # ingest ws://localhost:9099/ingest · gateway :8056
```

## Verify
`pnpm --filter @vexa/desktop test` runs cheapest first:
- **L2** `transcript-store.test.ts` — the store UPSERTS confirmed segments by `segment_id`: a late-box
  claim / cluster re-resolve re-publishes the same id with the real name and REPLACES the provisional
  (empty-speaker) copy in place — no ghost duplicate in `GET /transcripts`.
- **L2** `recording-sink.test.ts` — the `RecordingSink` port fed synthetic recording.v1 chunks via an
  in-memory fake (no WS, no disk); asserts the `buildRecordingMaster` output.
- **L3** `recording-e2e.test.ts` — synthetic recording.v1 over the real ingest WS → decode → sink → a
  REAL master file on disk → served by the gateway `GET /recordings`. No live meeting, no STT.
- **L4** `desktop-e2e.live.test.ts` — starts the host, feeds known TTS clips as `capture.v1` over the
  ingest WS, reads `/transcripts`, asserts glow-attributed, schema-valid `transcript.v1`. Skips without
  `VEXA_TX_KEY` + `EVAL_CACHE` (turbo passes them through). Validates the **whole composition** against
  real STT.
