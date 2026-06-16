# @vexa/transcribe-buffer

The shared **confirmation core**. As a turn's unconfirmed window is re-submitted
to Whisper, only the words that are **stable across two consecutive submissions**
are safe to confirm — the still-forming tail stays pending. That LocalAgreement-2
primitive was copy-pasted between the two engines (`speaker-streams` and
`chunked-transcriber`); it now lives here once.

```
driver: cut window → whisper → gate → localAgreement() → confirmed / pending → sink
                                       └──── this module ────┘
```

Each engine owns its own buffer model, cut, turn lifecycle, naming, and sink; it
calls `localAgreement()` to decide how many leading segments of one submission
may confirm. Pure + deterministic — no audio, no I/O.

## Public surface

| symbol | kind | role |
|---|---|---|
| `localAgreement(segments, prevWords, spanEndMs, closing)` | fn | → `{ confirmCount, lastWords }` for one submission |
| `words(text)` | fn | whitespace word split (the tokenization the agreement uses) |
| `AgreementSegment` / `AgreementResult` | types | the inputs/outputs |

Behavior is pinned by the **confirm-loop golden** (currently in `@vexa/pipeline`;
moves here when the engines themselves move into the lanes).

## Naming conventions & isolation

Follows the project-wide stage conventions (see `modules/shared/README.md`). A leaf
brick — no `@vexa/*` deps; `npm run check:isolation` proves every import is
intra-package or a Node builtin.
