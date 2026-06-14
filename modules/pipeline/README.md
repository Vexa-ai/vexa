# speaker-streams — the pipeline core brick

Consumes `capture.v1` audio (`feedAudio`) + speaker turns; emits attributed,
confirmed transcript segments. stt.v1 egress via `TranscriptionClient`
(OpenAI-compatible endpoint — any compatible implementation works).

- `src/speaker-streams.ts` — `SpeakerStreamManager`: per-speaker buffers,
  sliding-window submit, confirm/flush. **Contract: `addSpeaker()` MUST precede
  `feedAudio()` (it arms the submit timer).**
- `src/speaker-mapper.ts` — words × speaker boundaries → attributed segments.
- `src/vad.ts` — Silero VAD (onnxruntime-node).
- `src/hallucination-filter.ts` + `src/hallucinations/*.txt` — phrase filter.
- `src/transcription-client.ts` — stt.v1 client.
- `src/log.ts` — host-injectable logger (`setLogger`), the only host coupling.

## Harness
- **replay** (driver+oracle): `npm run replay -- <fixture-dir>` — feeds a recorded
  `capture.v1` fixture (per-speaker WAVs from the recorder brick) through the
  pipeline with no bot, no meeting, no GPU; prints the confirmed transcript.
  Proven against a live-recorded fixture: replay reproduces the live transcript.
- **unit oracle**: `npm test` (note: 2 pre-existing flaky assertions, inherited
  from the monolith — fail identically pre-extraction; tracked, not regressions).

Gates: `npm run check:isolation` · `npm run build` · `npm run replay`.

---

## Debugging the mixed pipeline (zoom / teams)

The **mixed** path is the diarized one: one mixed-remote audio channel (999) →
`createMixedPipeline` (`ChunkedTranscriber` = segmentation gate + online diarizer +
Whisper, LocalAgreement confirmation) → `separated-transcript.v1` with **opaque
cluster ids** (naming is the downstream speaker-attribution brick). Code:
`src/mixed-pipeline.ts`, `src/chunked-transcriber.ts`, `src/diarization/*`.

You debug its **quality** with no meeting, by benchmarking it against **Deepgram**
and **judging with your own eyes** on a side-by-side playback page.

### The loop
```bash
cd modules/pipeline
cp .env.example .env          # put TRANSCRIPTION_SERVICE_* + DEEPGRAM_API_KEY in .env
npm run bench:mixed           # build the run (default spec: bench/specs/podcast-520.json)
npm run bench:view            # → http://localhost:8077  — look + listen
```

**`bench:mixed`** (`scripts/bench-mixed.ts`): fetches a YouTube clip (`yt-dlp`+`ffmpeg`,
spec = `bench/specs/*.json`, a URL pointer — audio never in the repo), **Deepgram
transcribes+diarizes the full meeting** (cached `deepgram.ref.json`), **auto-selects
the 2-min window of interest** (the span with the most speakers / switches / turns —
where the pipeline is stressed), then **plays only that window into the pipeline at
faithful real-time 1×**. Writes to `$VEXA_FIXTURE_CACHE/bench/<name>/`:
`ours.separated-transcript.v1.jsonl`, `reference.jsonl` (Deepgram, **word-clipped** to
the window), `window.wav`, `scorecard.json` (supporting numbers only).

> **Real-time matters.** The `ChunkedTranscriber` closes turns and confirms pending
> text on **wall-clock timers**, so feeding must be paced 1×. Firehosing collapses
> confirmation and drops most of the transcript — an artifact, not the pipeline.
> `bench:mixed` paces to 1× by default; `BENCH_SPEED≠1` is marked non-faithful — don't
> judge those.

**`bench:view`** (`scripts/bench-view.ts`): the judging page — **Deepgram (left) vs Vexa
(right)**, same-speaker turns merged, colour-per-speaker, with the window audio. Press
play: both columns highlight the active turn and auto-scroll; click any turn to seek.
**You are the judge** — listen and watch where the columns diverge.

```bash
npm run bench:view                 # default bench dir
npm run bench:view -- <bench-dir>  # any other run
PORT=9090 npm run bench:view       # change port
```

### What to look for (the failure modes)
- **Over-split / mis-cluster** — one real speaker rendered as several Vexa clusters
  (e.g. a monologue lighting up `s6 → s2 → s7 → s8`). The diarizer opening new clusters
  on short, weak-embedding fragments. *Usually the #1 issue.*
- **Over-merge** — a long Vexa turn swallowing several reference turns (the gate not
  cutting on rapid speaker changes / sentence boundaries); can also drop content.
- **Dropped / truncated text** — reference has speech, Vexa is blank or cut off at a
  turn end (LocalAgreement discarding pending).
- **Clean baseline** — single-speaker spans should track Deepgram closely; if those
  break, the problem is upstream (capture / STT), not the diarizer.

To benchmark a different conversation, add a `bench/specs/<name>.json`
(`{ "name", "youtube_url", "start_s": 0, "duration_s": 0, "language" }`) and
`npm run bench:mixed -- bench/specs/<name>.json`.
