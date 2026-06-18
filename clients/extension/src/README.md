# extension/src — MV3 source (esbuild entry points)

One file per build entry, plus the shared meeting-detection helper. esbuild
(`../build.mjs`) bundles each `.ts` to `dist/<name>.js`; the `.html` files are
copied alongside.

- `background.ts` — service worker. Owns the ingest WebSocket; encodes PCM to
  capture.v1 (`encodeAudioFrame`) and meeting events (`encodeEvent`); orchestrates
  Start/Stop/Pause, auto-start, tab re-inject, telemetry, and the dev stamp-watch reload.
- `content.ts` — isolated-world bridge. Relays audio/hint messages inpage → background
  and Start/Stop commands background → inpage; triggers auto-start on a Meet URL.
- `inpage.ts` — MAIN-world capture loop. Wires each Meet participant's `<audio>` and
  the local mic into the gmeet captor; emits `postMessage('audio', { index, pcm, speakerName })`.
- `offscreen.ts` — offscreen mic capture for voice-notepad mode (no meeting tab).
- `mic-permission.ts` — one-time getUserMedia grant page (offscreen docs can't prompt).
- `sidepanel.ts` — capture-control UI (Start/Pause/Stop, settings, status). No transcript brick.
- `meeting.ts` — Google-Meet URL → `{ platform, nativeMeetingId }` detection, shared by background + content.
- `sidepanel.html` / `offscreen.html` / `mic-permission.html` — page shells for the above.
