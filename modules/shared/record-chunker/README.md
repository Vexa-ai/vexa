# @vexa/record-chunker

The shared **browser MediaRecorder driver** for meeting recording. Wraps a
`MediaRecorder` over a combined audio `MediaStream`, encodes each timeslice to
base64, and hands it to an injected `onChunk` callback (the `recording.v1` chunk
shape) — plus one final `isFinal` chunk on stop.

It exists once because the MediaRecorder loop (mimeType selection, the defensive
chunk buffer, base64 encode, the final-chunk handshake) is **identical** across
lanes; only the *combine-the-audio* step differs. So:

- **`@vexa/gmeet-capture`** builds a combined stream from gmeet's media elements,
- **`@vexa/mixed-capture-core`** already has one mixed stream,

and both feed it to this driver. (Like `@vexa/capture-codec`, this is a shared
leaf both lanes depend on.)

```
lane tap (combine audio) → MediaRecorderChunker → onChunk(base64, seq, isFinal) → host → recording.v1
```

**No master assembly here** — the master is built server-side by `meeting-api`
(`recording_finalizer.py`) from the `chunk_seq` sequence. **No fallbacks**: a
failed `onChunk` splices the chunk anyway (the server reconciler re-fetches); no
supported mimeType → log and refuse to start.

## Surface
- `class MediaRecorderChunker implements RecordingTap` — `start()` / `stop()`
  (stop resolves AFTER the final chunk callback completes).
- types: `RecordingChunk`, `RecordingTap`, `RecordingTapOptions`,
  `MediaRecorderChunkerOptions`.

## Files
`src/index.ts`. Pure browser (DOM `MediaRecorder`/`Blob`/`btoa`), zero `@vexa/*`
deps — `npm run check:isolation` enforces it.
