# desktop — the meetings all-in-one capture host (Node/TS)

`@vexa/desktop` (`startDesktop()`) is the meetings data plane composed into **one local process** —
no Docker / Postgres / Redis. It accepts the browser extension's `capture.v1` audio over an ingest
WebSocket, routes it dual-lane (`gmeet-pipeline` per-channel for Google Meet, `mixed-pipeline` for
zoom/teams/youtube), drives **real STT**, and serves transcripts + assembled recordings over an HTTP
gateway. TypeScript because it runs the same browser-adjacent bricks the cloud splits across
meeting-api + collector + gateway, here as a single deployable "modular monolith."

## Seams

| Direction | Neighbour | Via | What crosses |
|---|---|---|---|
| consumes | browser extension | ingest `WS :9099` (`capture.v1` frames, codec-discriminated) | audio frames (ch999 mix / ch1000 mic / per-channel gmeet) + active-speaker event hints |
| consumes | browser extension | `POST /extension/sessions`, `POST /extension/sessions/end`, `POST /telemetry` | session mint / finalize-now (Stop contract) / diagnostics ring buffer |
| consumes | extension | ingest WS (`recording.v1` chunks, same socket, `REC1`-magic discriminated) | recording chunks → `RecordingSink` |
| calls | `@vexa/transcribe-whisper` | `TranscriptionClient` over `TRANSCRIPTION_SERVICE_URL` | PCM → `stt.v1` segments |
| produces | transcript readers / live UI | gateway `GET /transcripts/{p}/{n}`, `WS /ws` | `transcript.v1`-shaped confirmed/pending segments + `health` frames |
| produces | recording readers | gateway `GET /recordings/{p}/{n}` (+ `/player`), `GET /bots`, `GET /health` | assembled `recording.v1` master, meeting list, liveness |

## Contracts

**Owns:** none — the gateway shapes are local HTTP, not a sealed `*.v1` it defines.
**Consumes:** [`core/meetings/contracts/transcript.v1`](../../contracts/transcript.v1) (the transcript
envelope it emits over `/ws` + `/transcripts`); `capture.v1` / `recording.v1` / `stt.v1` are owned by
the `@vexa/capture-codec`, `@vexa/recording`, and `@vexa/transcribe-whisper` packages it composes, not
by the meetings `contracts/` registry. Access is mediated through one `canAccess` seam (`access.ts`,
P20 / ADR-0012; default `ownerOnly` = allow-all on single-user localhost).

## Isolated evaluation

Tests live alongside the source in `src/*.test.ts`. Run with `pnpm test` (the package's `test` script
runs each via `tsx`):

- **L2 unit** — `recording-sink.test.ts`, `transcript-store.test.ts`, `health.test.ts`, `access.test.ts`
- **L3 integration** — `recording-e2e.test.ts` (synthetic `recording.v1` over the real ingest WS → real file on disk → served by the gateway; no live meeting)
- **L4 live** — `desktop-e2e.live.test.ts` (real STT; skips without `VEXA_TX_KEY` + `EVAL_CACHE`)

`pnpm check:isolation` enforces the brick-boundary rule.

## Status

- ✅ delivered — dual-lane ingest (gmeet per-channel + mixed pyannote-cut) with real STT
- ✅ delivered — gateway: sessions mint/end, `/transcripts`, `/bots`, `/health`, telemetry ring buffer, live `/ws`
- ✅ delivered — `recording.v1` receiver → assembled master + dependency-free `/player`
- ✅ delivered — `canAccess` mediation seam on every read path (default owner-only)
- ✅ delivered — P18 fault surfacing (engine fault + no-signal watchdog → `/ws health` · `/telemetry` · log)
- 🟡 partial — store is in-memory single-process (sqlite persistence is a later refinement)
- 🟡 partial — access grants are the seam only (`ownerOnly`); real owner/visibility grants land additively (ADR-0003)
