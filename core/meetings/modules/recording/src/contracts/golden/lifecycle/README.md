# recording.v1 — lifecycle golden vectors

Frame-sequence vectors for `createRecordingAssembler` (the finalize **lifecycle**, not just
`buildRecordingMaster`'s pure assembly). Each `<name>.json` is a sequence of `{seq, isFinal, bytes}`
frames + a `finalize` trigger (`is_final` | `close`) → the expected master (`master_len`,
`master_sha256`).

The `*-close-no-final` vectors pin the load-bearing invariant: a session finalized by `close()`
(the live Stop race, where the trailing `is_final` chunk is lost) yields the **same master** a clean
`is_final` would. Both receivers — `@vexa/recording` (TS) and meeting-api's finalizer (Python) —
must reproduce these byte-for-byte.

Regenerate / integrity-check from the parent oracle: `node ../generate.mjs [--check]`.
