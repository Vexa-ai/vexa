# @vexa/gmeet-capture

Google Meet capture (browser context). Unlike the mixed lane (one mixed stream,
names resolved downstream), gmeet exposes **per-participant audio channels** and
the **glow** (active-speaker) indicator, so the speaker name is **bound onto each
channel at the source** — `gmeet-capture.v1` carries named per-channel frames and
needs no downstream namer.

- `src/pcm-capture.ts` — the shared PCM capture node
- `src/gmeet-capture.ts` — per-channel audio capture
- `src/gmeet-speakers.ts` — glow active-speaker detection (per-track vote/lock)
- `src/gmeet-channel-binder.ts` — binds the glow name to a channel at onset, held through overlap
- `src/gmeet-capture-v1.ts` — assembles the above into the `gmeet-capture.v1` emitter
- `src/contract/capture-v1.ts` — the in-module sink port (`CaptureV1Sink`)

Public API: `createGmeetCaptureV1`, `createGmeetCapture`, `createGmeetSpeakers`,
`GmeetChannelBinder`, `createPcmCaptureNode`, `pickBoundName`.

Zero external imports (pure browser DOM/WebAudio) — `npm run check:isolation` enforces it.
