# capture-codec/src

The brick's source — front door is [`index.ts`](index.ts) (the public surface;
`package.json` `exports` points at its build). `recording-chunk.test.ts` is the
REC1-framing round-trip + audio-disambiguation test (`gate:node` runs it).
