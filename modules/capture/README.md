# @vexa/capture — the gmeet capture brick (TRANSITIONAL — to be retired)

Runs inside the meeting page (injected by the bot, or loaded by the extension)
and captures **Google Meet** audio. Zero node/back imports: pure browser-context
modules.

> **This module is transitional and will be RETIRED.** The mixed lane was already
> carved out to `@vexa/mixed-capture-core` + `@vexa/zoom-capture` +
> `@vexa/teams-capture` (consumers import those directly). What remains here is
> gmeet-only, and it goes away when the gmeet lane is carved into
> `modules/gmeet/capture` (`@vexa/gmeet-capture`) — at which point `modules/capture`
> is deleted.

- `src/gmeet-capture.ts` / `src/gmeet-capture-v1.ts` — per-channel audio capture
- `src/gmeet-speakers.ts` — glow-based active-speaker detection
- `src/gmeet-channel-binder.ts` — binds the glow name to a channel at onset
- `src/pcm-capture.ts` — the shared PCM capture node
- `src/contract/capture-v1.ts` — the contract this brick emits (gmeet shape)

Public API: `createGmeetCapture`, `createGmeetSpeakers`, `GmeetChannelBinder`,
`createGmeetCaptureV1`, `createPcmCaptureNode`.

Gates: `npm run check:isolation` · `npm run build`.
