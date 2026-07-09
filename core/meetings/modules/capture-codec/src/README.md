# capture-codec/src

The brick's source — front door is [`index.ts`](index.ts) (the public surface;
`package.json` `exports` points at its build). The `capture.v1` wire contract it
emits is documented + golden-pinned under [`contracts/`](contracts/):
`capture-v1-golden.test.ts` asserts `encode`/`decode` byte-identity against the
committed vectors (both directions). `recording-chunk.test.ts` is the
REC1-framing round-trip + audio-disambiguation test (recording.v1's delta). Both
run under `gate:node` (the package `test` script).
