# buffer/src

Front door [`index.ts`](index.ts) exports:

- the pure LocalAgreement-N core in [`local-agreement.ts`](local-agreement.ts);
- the parity-locked reusable Google Meet window in
  [`gmeet-compatible-buffer.ts`](gmeet-compatible-buffer.ts).

[`gmeet-compatible-buffer.parity.test.ts`](gmeet-compatible-buffer.parity.test.ts) imports the
untouched Google Meet source as its behavioral oracle and separately pins the Teams-only caller
seams. `gate:node` runs both test files.
