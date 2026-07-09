# capture-codec/src/contracts/golden

`capture.v1` golden vectors (`*.json`). Each pins one input + the exact wire
bytes the codec produces for it, in BOTH directions:

- `audio-*.json` — `{ name, kind:'audio', speakerIndex, ts, samples[],
  speakerName?, len, sha256, bytes_b64 }` for `encodeAudioFrame` /
  `decodeAudioFrame`. `samples` are the exact Float32 values (the decode
  oracle); the wire stores Float32, so decode is compared Float32-bit-exactly.
- `event-*.json` — `{ name, kind:'event', event, len, sha256, bytes_b64 }` for
  `encodeEvent` / `decodeEvent` (`bytes` = UTF-8 of `JSON.stringify(event)`).

[`generate.mjs`](generate.mjs) regenerates them FROM THE CODEC (run with tsx:
`npx tsx src/contracts/golden/generate.mjs`; `--check` is the integrity guard).
Pinned by [`../../capture-v1-golden.test.ts`](../../capture-v1-golden.test.ts),
which asserts `encode(input)` ≡ these bytes AND `decode(bytes)` ≡ the input.
