# @vexa/desktop — the Vexa Desktop backend (host)

The whole data plane in one Node process — no Docker / Postgres / Redis. The host
over the carved lane modules:

```
extension ─capture.v1─► ingest WS (9099)
   ├─ gmeet channels  → @vexa/gmeet-pipeline (channel-router, name at capture)
   ├─ mixed channel   → @vexa/mixed-pipeline  (pyannote segmenter + hints namer)
   ├─ wire codec       @vexa/capture-codec      (decode frames/events)
   ├─ recording tee    @vexa/recorder           (collect while you watch)
   ├─ STT egress       @vexa/transcribe-whisper (stt.v1)
   └─ gateway (8056): /extension/sessions /bots /transcripts  +  WS /ws  → node:sqlite
```

This is a **host** (far stack), not a lane brick — it imports the lane modules by
their `@vexa/*` contracts. It moved here from `modules/pipeline/scripts` when the
pipeline monolith was dissolved.

## Run
```bash
cd services/vexa-desktop
cp .env.example .env     # TRANSCRIPTION_SERVICE_URL + _TOKEN (the hosted Whisper egress)
npm install
npm run dev              # ingest ws://localhost:9099 + gateway http://localhost:8056
```

Other entrypoints: `npm run live-stack` (sidepanel stack), `npm run replay` /
`npm run e2e` (offline capture.v1 fixture replay).
