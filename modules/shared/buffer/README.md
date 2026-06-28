# @vexa/transcribe-buffer

The shared **confirmation primitive** for the streaming transcription engines.
As a turn's still-open window is re-submitted to Whisper the tail keeps changing;
only the leading words that stay **identical across N consecutive submissions**
are safe to confirm — the rest stays pending. That is *LocalAgreement-N*, and it
used to be copy-pasted between the two engines; it lives here once.

`N` defaults to **3**: live mixed audio (Teams/Zoom AGC + jitter) makes a 2-pass
agreement confirm not-yet-settled text, so the mixed driver requires three
identical passes and pairs it with a TTL idle-finalize (commit whatever is pending
when updates stop) so the stricter threshold never leaves words stuck.

```
driver: cut window → whisper → gate → localAgreement() → confirmed / pending → sink
                                       └──── this module ────┘
```

The primitive is pure + deterministic (no audio, no I/O). Each engine owns its own
buffer model, cut, turn lifecycle, naming, and sink; it calls `localAgreement()`
to decide how many leading segments of one submission may confirm, then carries
the returned `history` into the next pass.

## Public surface
| symbol | kind | role |
|---|---|---|
| `localAgreement(segments, history, spanEndMs, closing, agree=3)` | fn | → `{ confirmCount, history }` for one submission |
| `commonWordPrefix(arrays)` | fn | longest leading run identical across ALL passes — the heart of LocalAgreement-N |
| `longestCommonWordPrefix(a, b)` | fn | the two-array variant (reused by the gmeet per-channel confirm loop) |
| `words(text)` | fn | the whitespace word split the agreement tokenizes on |
| `AgreementSegment` / `AgreementResult` | types | the inputs / outputs |

## Behavior pinned by goldens
The confirm behavior is pinned by the **confirm-loop golden** in
[`@vexa/mixed-pipeline`](../../mixed/pipeline/) (`src/confirm-loop.golden.test.ts`),
which drives the real 3-pass + TTL loop. (gmeet reuses only
`longestCommonWordPrefix`; its own per-channel confirm loop lives in
[`@vexa/gmeet-pipeline`](../../gmeet/pipeline/).)

## Isolation
A leaf brick — no `@vexa/*` deps. `npm run check:isolation` proves every import is
intra-package or a Node builtin.
