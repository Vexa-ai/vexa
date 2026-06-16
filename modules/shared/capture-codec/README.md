# @vexa/capture-codec

The one **capture serialization** shared by both lane contracts
(`gmeet-capture.v1`, `mixed-capture.v1`): the binary audio-frame codec + the JSON
event codec. Pure, zero-dep, drift-gated.

The sender stamps capture-time into every frame; the receiver **never** restamps,
so bot-captured and extension-captured fixtures are byte-identical.

## Surface
- `encodeAudioFrame(speakerIndex, ts, pcm, speakerName?)` / `decodeAudioFrame(buf, …)`
  - no-name frame = mixed lane; high-bit named frame = gmeet glow name on the wire
- `encodeEvent(ev)` / `decodeEvent(json)` — `MeetingEvent` (active-speaker hints,
  chat, lifecycle)

## Files
`src/index.ts`. `npm run check:isolation` enforces zero non-builtin imports.
