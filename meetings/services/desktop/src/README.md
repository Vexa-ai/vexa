# desktop/src

[`desktop.ts`](desktop.ts) — the host: `startDesktop()` wires the ingest WS (decode `capture.v1`
→ `gmeet-pipeline` → real STT) to an in-memory store + the gateway (`/transcripts`, `/bots`, `/ws`).
`desktop-e2e.live.test.ts` is the end-to-end gate against real STT (`gate:node` runs it; skips
without `VEXA_TX_KEY` + `EVAL_CACHE`).
