# @vexa/transcribe-buffer — the shared confirmation core

_meetings/ · module · the LocalAgreement-N confirm primitive both lanes' pipelines drive._

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

## Surface
`localAgreement` · `words` · `longestCommonWordPrefix` · `commonWordPrefix` ·
types `AgreementSegment`, `AgreementResult`. Front door: [`src/index.ts`](src/index.ts).

## Verify
```bash
pnpm --filter @vexa/transcribe-buffer build
pnpm --filter @vexa/transcribe-buffer test   # the confirm-loop golden
```
Covered by `gate:node` (build + test), `gate:isolation`, `gate:exports`, `gate:readme`.
