# @vexa/mixed-pipeline

The **mixed lane** pipeline — for any single mixed audio stream (Zoom, Teams,
arbitrary tab). One stream in, named transcript segments out.

```
mixed-capture.v1 (audio + hints)
   └─ ChunkedTranscriber
        ├─ PyannoteSegmenter   cut-only (the only ONNX; NO diarization/clustering)
        ├─ @vexa/transcribe-buffer   LocalAgreement-3 confirm (+ TTL idle-finalize)
        ├─ @vexa/transcribe-whisper  stt.v1 transcribe (injected)
        └─ ClusterNameBinder   the namer — hints by time window
   ─► transcript.v1 (named segments + drafts)
```

The pyannote **segmentation** owns the cut (turns open/close on speaker-change /
silence / overlap boundaries). Names come purely from time-windowed platform
hints (`recordHint`) matched against each turn's audio window — the per-turn
segmentation id is the key. **There is no speaker clustering/diarization.**

## Surface
- `ChunkedTranscriber.create({ transcribe, publish, publishPending, clearPending, rename, makeSegmenter?, language? })`
- `feedAudio(pcm, tsMs)` · `recordHint(name, kind, tMs, isEnd?)` · `dispose()`
- Re-exports `PyannoteSegmenter` (the cut) and `ClusterNameBinder` (the namer).

## Files
`src/chunked-transcriber.ts` (driver: ring + boundary wiring + confirm),
`src/pyannote-segmenter.ts` (the cut), `src/cluster-name-binder.ts` (the namer).

## Gates
`npm run check:isolation` · `npm test` — 6 goldens: the confirm-loop golden plus
the naming / claim / priority / concurrency / flicker smokes (each pins one
attribution behavior: window-match naming, late-box claim, unattributed-priority,
concurrent multi-speaker hints, and flicker-resistant sticky attribution).
