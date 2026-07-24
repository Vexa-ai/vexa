# Causal speaker replay matrix

`manifest.json` pins the generated #956 C3 Teams/Jitsi fixture matrix and its Zoom
direct-handover non-regression row. Each entry stores:

- SHA-256 of the one authored WAV, truth JSONL, and stable timeline used by all platform views of
  that scenario;
- the exact C1 `captured-signal-custody-receipt` over uncompressed JSONL bytes.

The tapes are generated deterministically by
[`../../src/causal-speaker-matrix.ts`](../../src/causal-speaker-matrix.ts). The bot replay gate
admits them through the real custody adapter, deletes worker staging, independently reads them
back, and only then runs causal scoring and direct Teams/Jitsi pipeline replay.

This directory contains no harvested meeting data. It models the post-producer `recordHint` seam;
Teams/Zoom DOM extraction and live validation remain #797.
