# dash-ws — source

The brick's implementation. The single public surface is `index.ts` (the front door); everything else
here is internal to the brick and reached only through it.

> The unified dashboard WS client: one frame-handling table over the 0.10.6 ws.v1 multiplex. Replaces the vendored dashboard's two drifted consumers (use-vexa-websocket + use-live-transcripts). Subscribes on open, normalizes meeting.status into the dashboard vocabulary (needs_help → needs_human_help, per ADR-0023), fans transcript/transcription_segment/chat/error frames to injected callbacks, pings every 25s. Transport-injected (zero browser globals) so it's deterministic to test.

