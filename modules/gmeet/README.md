# gmeet/ — the Google Meet lane (per-channel audio, name bound at capture)

Google Meet is the *forgiving* topology. The page hands us **per-participant
audio channels** plus the **glow** (the active-speaker ring), so we can bind the
speaker's name onto each channel **at the source**. The name then rides on the
audio all the way through — which makes this lane:

- **namer-free** — no segmentation, no time-window hints, no clustering. Identity
  is *known* at capture, not *reconstructed* downstream.
- **overlap-safe** — two people talking at once are two separate channels, so
  there is no cross-talk to untangle.

That is the whole contrast with [`../mixed`](../mixed/): the mixed lane gets one
blended stream and has to *reconstruct* who-said-what; gmeet gets it **for free**
from the page.

```
capture   @vexa/gmeet-capture    per-channel PCM + glow→channel name bind ─┐
                                                                           ├─ gmeet-capture.v1 ─► pipeline
pipeline  @vexa/gmeet-pipeline   channel router: one buffer+Whisper / channel ┘                  @vexa/gmeet-pipeline
                                 ─► transcript.v1 (named segments)
```

| dir | package | role | contract |
|---|---|---|---|
| [`capture`](capture/) | `@vexa/gmeet-capture` | per-participant audio + glow active-speaker → a name on every channel | page → `gmeet-capture.v1` |
| [`pipeline`](pipeline/) | `@vexa/gmeet-pipeline` | channel router — one sliding-window buffer + Whisper per channel; the name is carried straight through | `gmeet-capture.v1` → `transcript.v1` |

Shared engine: `@vexa/transcribe-whisper` (stt.v1) + `@vexa/capture-codec` (wire).
The per-channel confirm loop reuses the `@vexa/transcribe-buffer` word-prefix
primitive, but keeps its own loop for now (the full fold into the shared confirm
is deferred — see the [pipeline README](pipeline/)). Each subfolder's README has
the surface and files.
