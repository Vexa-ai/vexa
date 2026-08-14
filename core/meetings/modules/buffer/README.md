# @vexa/transcribe-buffer — shared confirmation core and GMeet-compatible window

_meetings/ · module · deterministic agreement plus the reusable continuous Whisper window._

As a turn's unconfirmed window is re-submitted to Whisper, only the **words stable
across N consecutive passes** (default 3) are safe to confirm; the still-forming
tail stays pending. This brick is that decision — **pure, deterministic, no audio,
no I/O**. The driver owns the buffer, the cut, the turn lifecycle, naming, and
publishing; it calls `localAgreement(...)` to decide how many leading **whole**
segments confirm and carries the returned history.

- Never confirms a **partial** segment, and never past the **read audio window**.
- N=3 because live-mixed audio (Teams/Zoom AGC + jitter) makes a 2-pass agreement
  commit not-yet-settled text; the driver pairs it with a TTL idle-finalize so the
  stricter threshold never strands pending words.

`GmeetCompatibleBuffer` is the separate stateful surface reconstructed from Google Meet's
`SpeakerStreamManager`: per-speaker unconfirmed PCM, a two-second minimum call interval,
confirmed-text prompt feedback, segment-prefix LocalAgreement, identical-full-text fallback,
pending/final lifecycle, silence gate, hard cap, idle/terminal flush, and stale-response handling.

Its defaults are behaviorally parity-locked against the still-untouched Google Meet source by
[`src/gmeet-compatible-buffer.parity.test.ts`](src/gmeet-compatible-buffer.parity.test.ts). Teams
imports the shared class now. Google Meet deliberately keeps its pipeline-local class for this
release; after Teams wins on diverse fixtures, a later release can switch Google Meet to this
package and delete the duplicate source.

Teams may adapt only caller-owned seams:

- timer scheduling is disabled in deterministic replay, while calls still pass through the same
  two-second manual interval seam;
- the post-prefix tail remains visible as pending;
- the timestamped batch-input gap guard is disabled because the CSRC adapter already owns and
  closes discontinuous virtual turns. This prevents `feedAudio` becoming an off-timer Whisper
  trigger. The default remains enabled for exact Google Meet parity.

## Surface

`GmeetCompatibleBuffer` · `gmeetCompatibleRms` · `localAgreement` · `words` ·
`longestCommonWordPrefix` · `commonWordPrefix` · types `GmeetCompatibleBufferConfig`,
`AgreementSegment`, `AgreementResult`. Front door: [`src/index.ts`](src/index.ts).

## Verify
```bash
pnpm --filter @vexa/transcribe-buffer build
pnpm --filter @vexa/transcribe-buffer test   # LocalAgreement + live-window parity locks
```
Covered by `gate:node` (build + test), `gate:isolation`, `gate:exports`, `gate:readme`.
