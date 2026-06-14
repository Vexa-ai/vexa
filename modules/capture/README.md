# capture-kit — the meeting-capture brick

Runs inside the meeting page (injected by the bot, or loaded by the extension)
and emits **capture.v1** — per-speaker audio chunks + meeting events — the input
to the pipeline bricks. Zero node/back imports: pure browser-context modules.

- `src/gmeet-capture.ts` — per-speaker audio capture (Google Meet)
- `src/{gmeet,zoom,msteams}-speakers.ts` — per-platform speaker detection
- `src/webrtc-audio-hook.ts` — the WebRTC remote-audio tap
- `src/contract/capture-v1.ts` — the contract this brick EMITS (AudioChunk,
  MeetingEvent, CaptureV1Sink). The host wires emitted chunks/events to a sink:
  in-process for the bot, WebSocket for the extension.

Public API: `createGmeetCapture`, `createGmeetSpeakers`, `createTeamsSpeakers`,
`createZoomSpeakers`, `installRemoteAudioHook`.

Harness: the **extension is the live testbed** (it runs capture-kit in a real
user tab). Replay: recorded capture.v1 (the recorder tees this brick's output).

Gates (laptop, no infra): `npm run check:isolation` · `npm run build`.
