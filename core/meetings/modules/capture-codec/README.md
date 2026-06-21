# @vexa/capture-codec — the shared capture wire codec

_meetings/ · module · the ONE serialization both capture lanes share._

The binary **audio-frame** + JSON **event-frame** codec used by both lane contracts
(`gmeet-capture.v1`, `mixed-capture.v1`), plus the **recording-chunk** frame
(`recording.v1`) that rides the same desktop ingest WS. Pure, zero-dependency, and
drift-gated so a **bot-captured** and an **extension-captured** fixture are
byte-identical.

- The sender stamps capture-time into every frame; the receiver **never** restamps.
- Two backward-compatible audio shapes: no-name (mixed) and named (gmeet binds the
  glow name at the source). The high bit of `track` flags a named frame; legacy
  frames decode unchanged.
- `REC1`-magic recording frames disambiguate from audio frames on one wire.

## Surface
`encodeAudioFrame` · `decodeAudioFrame` · `encodeEvent` · `decodeEvent` ·
`encodeRecordingChunk` · `decodeRecordingChunk` · types `MeetingEvent`,
`RecordingFormat`. Front door: [`src/index.ts`](src/index.ts).

## Verify
```bash
pnpm --filter @vexa/capture-codec build   # tsc → dist/
pnpm --filter @vexa/capture-codec test    # REC1 framing round-trip + audio disambiguation
node scripts/check-isolation.js           # P2: pure, zero-dep
```
Covered by the repo gates: `gate:node` (build + test), `gate:isolation`,
`gate:exports`, `gate:readme`.
