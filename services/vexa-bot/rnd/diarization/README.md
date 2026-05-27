# vexa-bot diarization RnD — MVP0

> **NOTE (in-flight rebuild):** the harness pipeline implementation is
> currently a stub. The previous parallel-pipeline implementation
> (`transcription-client.ts`, `pipeline.ts`, `stub-diarizer.ts`) violated
> the MVP0 constraint that the harness must reuse production bot code,
> and has been archived to
> `/home/dima/dev/vexa-archive-mvp0-parallel-pipeline/`. The harness is
> being rebuilt on top of the production bot's modules
> (`speaker-streams.ts`, `vad.ts`, `transcription-client.ts`,
> `segment-publisher.ts`). Descriptions below may not match the current
> code on disk until that rebuild lands.

This is the **MVP0 walking skeleton** for the pack
[`pack-msteams-local-diarization-rnd`](https://github.com/Vexa-ai/vexa/issues/378).
It is a hot-reload Node + TypeScript harness that lets a human share any
browser tab and see, in real time, a diarized transcript with stable
`speaker_0..N` labels.

It is **not** the production MS Teams bot. It runs *standalone* and is a
net-new subtree — it does not modify or import any file under
`services/vexa-bot/core/`. It does mirror the production bot's
**transcription wire contract** (multipart POST to
`/v1/audio/transcriptions`) so we can call the same Whisper service.

It exposes a swappable `Diarizer` interface. MVP0 ships with a **stub
diarizer** (`VadRoundRobinDiarizer`) that rotates speaker labels at every
RMS-energy VAD speech onset. There is no actual voice discrimination at
this stage — the stub exists to prove the pipeline plumbing end-to-end.

Real diarization quality lands at MVP1 when `PyannoteSidecarDiarizer` is
plugged into the same `Diarizer` interface.

## Run it

Requires Node 20+ and `npm`. The first run downloads dependencies
(including `@jjhbw/silero-vad` via the bot's existing `node_modules`).

```bash
cd services/vexa-bot/rnd/diarization

# Without transcription backend (dashboard shows placeholder text per segment):
./scripts/dev.sh

# With transcription backend (real Whisper output):
TRANSCRIPTION_URL=http://localhost:8083 ./scripts/dev.sh
```

The harness listens on **PORT 43500** by default (the pack's allocated
`compose_dashboard` slot — see `runtime.json`).

Then in your browser:

1. Open <http://localhost:43500/dashboard> in one tab — leave it visible.
2. Open <http://localhost:43500/> in another tab.
3. Click **Share tab and start**, pick the YouTube tab, and **tick the
   "Share tab audio" checkbox** in the browser dialog.
4. Watch `/dashboard` populate with `speaker_0` / `speaker_1` chips and
   transcript lines as the conversation plays.

To stop: click **Stop** on the capture page, or click the browser's "Stop
sharing" pill.

## Environment

| Var                | Default                                            | What it does                                                              |
| ------------------ | -------------------------------------------------- | ------------------------------------------------------------------------- |
| `PORT`             | `43500`                                            | HTTP + WS port for capture page, dashboard, and audio/transcript sockets. |
| `TRANSCRIPTION_URL`| `(unset)`                                          | If set, harness posts audio to this URL. Unset → placeholder transcripts. |
| `NUM_SPEAKERS`     | `2`                                                | How many round-robin speaker labels the stub rotates through.             |

## Architecture (MVP0)

```
┌──────────────┐                ┌──────────────────────────────────────────┐
│ Browser tab  │ getDisplayMedia│  Browser capture page (public/capture.*) │
│ (YouTube)    │ ───────────────▶  AudioWorklet downsamples to 16kHz mono  │
└──────────────┘                └──────────────────────────────────────────┘
                                                  │ WS /audio (binary PCM)
                                                  ▼
                                ┌──────────────────────────────────────────┐
                                │  Harness (src/server.ts)                 │
                                │                                          │
                                │  ┌─ Diarizer (src/diarizer.ts) ────┐     │
                                │  │  VadRoundRobinDiarizer (MVP0)   │     │
                                │  │   → Silero VAD (bot vad.ts)     │     │
                                │  │   → label rotates on each       │     │
                                │  │     speech onset                │     │
                                │  └─────────────────────────────────┘     │
                                │              │ label                     │
                                │              ▼                            │
                                │  ┌─ Pipeline (src/pipeline.ts) ────┐     │
                                │  │ per-speaker buffer + flush on   │     │
                                │  │  speaker-change / max-buf /     │     │
                                │  │  timer → TranscriptionClient    │     │
                                │  │  (bot transcription-client.ts)  │     │
                                │  └─────────────────────────────────┘     │
                                │              │ SegmentEvent              │
                                │              ▼                            │
                                │  ┌─ Dashboard WS broadcast ────────┐     │
                                │  └─────────────────────────────────┘     │
                                └──────────────────────────────────────────┘
                                                  │ WS /transcript (JSON)
                                                  ▼
                                ┌──────────────────────────────────────────┐
                                │  Dashboard page (public/dashboard.*)     │
                                │  speaker_N chips + rolling transcript    │
                                └──────────────────────────────────────────┘
```

## Honesty notes (what is and isn't shared with the production bot)

- **Net-new in the harness (this MVP0):**
  - `Diarizer` interface + `VadRoundRobinDiarizer` stub (RMS-energy VAD)
  - Tab-capture WebSocket source
  - Per-speaker buffer + flush pipeline (`pipeline.ts`) — a simplified
    analog of the bot's `speaker-streams.ts`, NOT a port of it
  - Slim transcription client (`transcription-client.ts`) — mirrors the
    bot's wire contract (multipart POST to `/v1/audio/transcriptions`)
    but does not import bot code
  - RnD dashboard

- **Shared with production bot:** **none** in MVP0. The harness uses the
  same transcription-service HTTP contract, but not any TS module.

- **Deferred to later MVPs:**
  - **MVP1:** `PyannoteSidecarDiarizer` (Python child process, real
    diarization). May also reintroduce the bot's Silero VAD if useful.
  - **MVP3 extractability audit:** evaluate whether to share the bot's
    `transcription-client.ts` / `vad.ts` directly. The harness's slim
    mirrors today match the wire contract, so this is a low-risk lift.
  - **Stage 2:** wire `Diarizer` into the bot's full Pack U
    `UnifiedRecordingPipeline` and `speaker-streams.ts` so production
    MS Teams meetings benefit. Requires decoupling the audio path from
    the bot's lifecycle and meeting-api dependencies.

The MVP0 demo is sufficient to validate (a) the seam contract design, (b)
the swap composition root, and (c) that a single-channel audio source can
be diarized and surfaced in real time. Quality measurement begins at MVP1.

## Scope reminder

This harness is the MVP0 deliverable. Out of MVP0 scope (defer to later MVPs):

- File-replay adapter (MVP1)
- DER / cpWER / latency metric runner (MVP1)
- Pyannote sidecar (MVP1)
- Oracle-as-script-generator + voice-cloned TTS regeneration (MVP2)
- Anchor set (AMI/VoxConverse), overlap mutator, alternative diarizers (MVP3)
- Wiring into production MS Teams bot path (stage 3)

See [`.agents/packs/pack-msteams-local-diarization-rnd/mvp0/seam-design.md`](../../../../.agents/packs/pack-msteams-local-diarization-rnd/mvp0/seam-design.md)
for the seam decision rationale.
