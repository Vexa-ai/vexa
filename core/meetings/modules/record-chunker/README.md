# @vexa/record-chunker — shared browser MediaRecorder driver

_meetings/ · module · combined audio MediaStream → `recording.v1` base64 chunks._

Runs **inside the meeting page**. Wraps a `MediaRecorder` over a combined audio `MediaStream`, encodes
each timeslice to base64, and hands it to an injected `onChunk` callback (the `recording.v1` chunk
shape: `{ base64, chunkSeq, isFinal, mimeType }`). On `stop()` it emits one final chunk with
`isFinal: true`. **No master assembly here** — the master is built downstream (server-side
`recording_finalizer.py`, or the desktop via [`@vexa/recording`](../recording/) `buildRecordingMaster`)
from the `chunkSeq` sequence.

Both lane recording taps use this once: [`@vexa/gmeet-capture`](../gmeet-capture/) (gmeet) and
`@vexa/mixed-capture-core` (mixed/teams). The combine-the-audio step differs per lane and lives in each
lane; the `MediaRecorder` loop is identical and lives here.

- `MediaRecorderChunker` — the core driver class over a ready `stream`.
- `createRecordingTap` — the full generic tap (all platforms / both hosts): find every page audio
  element → combine → `MediaRecorderChunker` → `recording.v1` chunks.

No fallbacks: a failed/false `onChunk` splices the chunk anyway and logs (the server reconciler
re-fetches via `chunkSeq`); with no supported `mimeType` it logs and refuses to start.

## Surface
`MediaRecorderChunker` · `createRecordingTap` (+ types `RecordingChunk`, `RecordingTap`,
`RecordingTapOptions`, `MediaRecorderChunkerOptions`, `CreateRecordingTapOptions`).
Front door: [`src/index.ts`](src/index.ts).

## Verify
`pnpm --filter @vexa/record-chunker run build` — `tsc` clean (`tsconfig` adds the `DOM` lib). The
MediaRecorder loop is pinned in isolation by [`src/chunker.smoke.test.ts`](src/chunker.smoke.test.ts)
(`pnpm --filter @vexa/record-chunker test` — stubs the four browser globals it touches and drives the
REAL class: seq increments from 0, base64 round-trips the blob body, `mimeType` negotiated, `stop()`
emits exactly one `isFinal=true` final chunk). The live browser path is validated in a real meeting.
Covered by `gate:node`, `gate:isolation`, `gate:exports`, `gate:readme`.
