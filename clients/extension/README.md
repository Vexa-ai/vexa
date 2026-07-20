# extension — in-tab capture driver (MV3)

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). A client app, not a library — `"private": true`, so the exports gate skips it._

The live **page-audio capture driver** for the Vexa desktop host. It runs inside
the tab you are already in (no bot, no waiting room), captures that tab's audio,
and streams **capture.v1** over a WebSocket to the desktop's ingest at
`ws://localhost:9099/ingest`. The transcript itself is read on the dashboard.

## Platforms — both lanes

Four platforms, detected from the tab URL by `src/meeting.ts` (`detectMeeting`),
matching the `host_permissions` in `manifest.json`:

| tab | platform | lane | capture |
|---|---|---|---|
| `meet.google.com/abc-defg-hij` | `google_meet` | **gmeet** | per-participant `<audio>`, each channel glow-named at the source |
| `youtube.com/watch?v=…` | `youtube` | **mixed** | the tab's `<video>` — ONE mixed stream, diarized by the desktop's pyannote lane |
| `*.zoom.us/j/<id>` (+ `/w/`, `/wc/`) | `zoom` | **mixed** | offscreen `tabCapture` → channel 999, plus zoom-speakers DOM name hints |
| `teams.microsoft.com` · `teams.live.com` · `teams.cloud.microsoft` | `teams` | **mixed** | offscreen `tabCapture` → channel 999, plus msteams-speakers DOM name hints |

`background.ts` routes the last three through `const MIXED = new Set(['youtube',
'zoom', 'teams'])`; only Google Meet takes the per-channel gmeet path.

**A YouTube tab is therefore the cheapest repeatable MIXED-lane source there is** —
a URL, no meeting, no admission, no second participant — which makes it the
fixture generator for mixed-lane work.

## Capture → ingest chain

```
tab (MAIN world)           inpage.ts   per-participant <audio> (gmeet) / tab <video> (mixed)
        │                              → AudioWorklet → 16 kHz PCM
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

esbuild bundles the v0.12 capture bricks in via the `alias` map in `build.mjs`
(pointed at each brick's `src/index.ts`).

## Load it in Chrome

1. `node build.mjs` to produce `dist/`.
2. Chrome → `chrome://extensions` → enable **Developer mode** → **Load unpacked**
   → pick the `dist/` folder.
3. Start the Vexa desktop so something is listening on `ws://localhost:9099/ingest`.
4. Open the side panel (click the Vexa toolbar icon), set your **API key** in
   settings, open any supported tab (a Google Meet, a YouTube video, a Zoom web
   client, or a Teams meeting), and press **Start** (or leave auto-start on).
