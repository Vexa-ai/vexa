# extension/src — MV3 source (esbuild entry points)

One file per build entry, plus the shared meeting-detection helper. esbuild
(`../build.mjs`) bundles each `.ts` to `dist/<name>.js`; the `.html` files are
copied alongside.

- `background.ts` — service worker. Owns the ingest WebSocket; encodes PCM to
  capture.v1 (`encodeAudioFrame`) and meeting events (`encodeEvent`); relays the
  offscreen's recording.v1 chunks (`RECORDING_CHUNK`) verbatim over the SAME WS;
  orchestrates Start/Stop/Pause, auto-start, tab re-inject, telemetry, and the dev stamp-watch reload.
- `content.ts` — isolated-world bridge. Relays audio/hint messages inpage → background
  and Start/Stop commands background → inpage; triggers auto-start on a Meet URL.
- `inpage.ts` — MAIN-world capture loop. Wires each Meet participant's `<audio>` and
  the local mic into the gmeet captor; emits `postMessage('audio', { index, pcm, speakerName })`.
- `offscreen.ts` — offscreen mic capture (voice-notepad) + mixed tab-audio capture
  (YouTube/Zoom). Also tees that captured tab stream into `@vexa/record-chunker` →
  recording.v1 chunks (`encodeRecordingChunk`) → `RECORDING_CHUNK` messages (the ACQUIRE
  adapter; the desktop's `RecordingSink` assembles the master — ADR-0005).
- `mic-permission.ts` — one-time getUserMedia grant page (offscreen docs can't prompt).
- `sidepanel.ts` — capture-control UI (Start/Pause/Stop, settings, status). No transcript brick.
- `meeting.ts` — Google-Meet URL → `{ platform, nativeMeetingId }` detection, shared by background + content.
- `sidepanel.html` / `offscreen.html` / `mic-permission.html` — page shells for the above.
