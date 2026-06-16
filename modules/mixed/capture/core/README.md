# @vexa/mixed-capture-core

Platform-agnostic **mixed-audio capture** (browser) shared by every mixed-lane
platform (Zoom, Teams, arbitrary tab). One mixed PCM stream — no per-speaker
channels, no names (those come from the platform hint watchers in
`@vexa/zoom-capture` / `@vexa/teams-capture`).

Emits `mixed-capture.v1` audio frames.

## Surface
- `createMixedAudioCapture(opts) → MixedAudioCapture`
- `installRemoteAudioHook(...)` — WebRTC remote-audio tap

## Files
`src/mixed-audio.ts`, `src/webrtc-audio-hook.ts`. Zero external imports (pure
DOM/WebAudio); `npm run check:isolation` enforces it.
