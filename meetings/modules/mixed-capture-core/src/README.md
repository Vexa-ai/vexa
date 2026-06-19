# mixed-capture-core/src

Front door [`index.ts`](index.ts). The browser pieces:
[`mixed-audio.ts`](mixed-audio.ts) (`createMixedAudioCapture` — a mixed `MediaStream` → 16 kHz PCM
via a `ScriptProcessorNode`, plus re-play through a separate native-rate context on a cloned track),
[`webrtc-audio-hook.ts`](webrtc-audio-hook.ts) (`installRemoteAudioHook` — patches
`RTCPeerConnection` so each remote participant's audio track is mirrored into a hidden
`<audio data-vexa-injected>` element).

Zero external imports — pure DOM/WebAudio. The DOM taps are live-validated in a real meeting.

[`mixed-capture-core.test.ts`](mixed-capture-core.test.ts) (`npm test`) is the L2 unit: a shimmed
`AudioContext`/`MediaStream` drives the real `ScriptProcessor` callback to pin the silence-gate,
copy-on-forward, and teardown, plus the `installRemoteAudioHook` no-RTC contract — no browser.
