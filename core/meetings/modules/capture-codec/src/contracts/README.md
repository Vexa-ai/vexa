# capture-codec/src/contracts

The `capture.v1` wire contract this brick emits — the extension/bot → desktop
capture serialization (binary audio frame + JSON event frame).

- [`capture-v1.md`](capture-v1.md) — the prose contract: the binary audio-frame
  layout (named/unnamed, the high-bit flag, Int32 track + Float64 ts + optional
  zero-padded name + Float32 PCM), the event-frame JSON shape, and the
  back-compat rule.
- [`golden/`](golden/) — the golden vectors. The codec ([`../index.ts`](../index.ts))
  is pinned to these byte-for-byte (both directions) by
  [`../capture-v1-golden.test.ts`](../capture-v1-golden.test.ts).

(The `REC1` recording-chunk frame rides the same ingest WS but belongs to
`recording.v1` and is golden-pinned in `modules/recording` — not here.)
