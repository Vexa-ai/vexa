# contracts/ — all wire boundaries

Every wire contract between services lives here as `contracts/<name>/v<N>/`:
schema + README + golden example messages. **The goldens are the spec** — if an
example can't express it, the contract doesn't carry it.

Rules (MANIFEST §2): data-shaped only; additive change = same version, breaking
change = `v<N+1>` with the old version kept until no consumer pins it; standard
contracts (stt) are never forked. Changes here ride `lane:contract` — the one
human-gated lane; review surface is schema diffs + goldens, never implementations.

| Contract | Between | Status |
|---|---|---|
| `stt/v1` | stt-client → transcription-service | **live since v0.10** — real golden included |
| `capture/v1` | bot/extension capture → pipeline bricks | formalize at MVP2 (embryo: `ingest-server` frames, `raw-capture` dumps) |
| `separated-transcript/v1` | mixed-pipeline & multistream-pipeline → speaker-attribution | formalize at MVP2 (embryo: `speaker-mapper.ts` opaque-key segments) |
| `transcript/v1` | speaker-attribution → collector | formalize at MVP2 (embryo: de facto Redis segment schema) |
| `acts/v1` | meeting-api / runtime-api → vexa-bot | define at MVP3 |
| `lifecycle/v1` | meeting-lifecycle ↔ bot-orchestration | formalize at MVP3 (semantics: Pack J / Pack D.2 outboxes) |
| `api/v1` | gateway / mcp / meeting-api → world | version at MVP4 (exists: OpenAPI + WS + MCP) |
| `webhook/v1` | webhooks → customer endpoints | version at MVP4 (live since v0.9, HMAC-SHA256) |
