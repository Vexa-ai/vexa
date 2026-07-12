# extension — in-tab Google Meet capture driver (MV3)

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). A client app, not a library — `"private": true`, so the exports gate skips it._

The live **page-audio capture driver** for the Vexa desktop host. It runs inside
the Google Meet tab you are already in (no bot, no waiting room), captures each
participant's audio per-channel plus your own mic, and streams **capture.v1** over
a WebSocket to the desktop's ingest at `ws://localhost:9099/ingest`. The
transcript itself is read on the dashboard.

Google-Meet-only by design: it consumes exactly two v0.12 bricks —
`@vexa/gmeet-capture` (per-participant capture + the glow active-speaker → the
name bound onto each channel at the source) and `@vexa/capture-codec` (the
capture.v1 binary-frame + JSON-event wire codec). There is no zoom/teams/recording
path here.

## Capture → ingest chain

```
Meet tab (MAIN world)      inpage.ts   per-participant <audio> → AudioWorklet → 16 kHz PCM
        │ window.postMessage('audio', { index, pcm, speakerName })
        ▼
content script (isolated)  content.ts  chrome.runtime.sendMessage → background
        ▼
service worker             background.ts  encodeAudioFrame(@vexa/capture-codec) → WebSocket
        ▼
        ws://localhost:9099/ingest   (the desktop)
```

The WebSocket lives in `src/background.ts`; `encodeAudioFrame` /
`encodeEvent` are called there. The default ingest URL is
`ws://localhost:9099/ingest` (overridable in the side-panel settings).

## Build

```bash
node build.mjs          # one-shot build → dist/
node build.mjs --watch  # rebuild on change (extension hot-reloads via build-stamp.txt)
```

esbuild bundles the two v0.12 bricks in via the `alias` map in `build.mjs`
(pointed at each brick's `src/index.ts`). No bundling of zoom/teams/recording.

## Load it in Chrome

1. `node build.mjs` to produce `dist/`.
2. Chrome → `chrome://extensions` → enable **Developer mode** → **Load unpacked**
   → pick the `dist/` folder.
3. Start the Vexa desktop so something is listening on `ws://localhost:9099/ingest`.
4. Open the side panel (click the Vexa toolbar icon), set your **API key** in
   settings, join a Google Meet, and press **Start** (or leave auto-start on).
