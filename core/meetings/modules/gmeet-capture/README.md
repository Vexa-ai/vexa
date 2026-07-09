# @vexa/gmeet-capture — the gmeet capture layer (browser)

_meetings/ · module · Meet page → `capture.v1` (per-channel audio + glow name bound at the source)._

Runs **inside the meeting page**. Google Meet renders each participant's audio as a separate
`<audio>` element (a live `MediaStream`); this wires each into an `AudioContext`, resamples to
16 kHz, and emits per-channel PCM — **stamping each chunk with the glow name lit at that instant**.
That binds identity to the *audio* at the source: Meet's remote channels are an anonymous rotating
pool (channel ≠ speaker), but the glow names the live speaker, so the downstream
[`gmeet-pipeline`](../gmeet-pipeline/) transcribes per-name with no diarizer.

**Two hosts, one brick** — the [extension](../../../clients/) wires the `capture.v1` sink to the WS
codec (live); the [bot](../../services/) wires it to its in-process sink (container). The `capture.v1`
model + wire codec is [`@vexa/capture-codec`](../capture-codec/) (the SSOT).

## Surface
`createGmeetCaptureV1` (the producer) · `createGmeetCapture` · `createGmeetSpeakers` ·
`createPcmCaptureNode` · `GmeetChannelBinder` · `pickBoundName`. Front door: [`src/index.ts`](src/index.ts).

## Verify
`pnpm --filter @vexa/gmeet-capture test` — `gmeet-capture.test.ts` pins the **pure cores** (no DOM):
`pickBoundName` (a name only when exactly one tile is lit) + `GmeetChannelBinder` (energy↔glow
correlation). The DOM capture itself (`pcm`/`audio`/`glow` scraping) is validated **live** in a real
Meet (extension/bot) — consistent with how the lane has always been tested. `tsconfig` adds the `DOM`
lib. Covered by `gate:node`, `gate:isolation`, `gate:exports`, `gate:readme`.
