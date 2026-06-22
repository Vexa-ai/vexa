# @vexa/dash-ws — the unified dashboard WS client

_dashboard_new/ · module · one frame-handling table over the 0.10.6 `/ws` multiplex._

The single live-data consumer for the dashboard. It **replaces the vendored dashboard's TWO drifted
hooks** — `use-vexa-websocket` and `use-live-transcripts` — which had forked subscribe / ping /
dispatch logic that fell out of sync. Here there is ONE dispatch table keyed on `frame.type`.

Lifecycle (all over an injected [`WsTransport`](src/ports.ts), so zero browser globals leak into the
brick):

- **connect** → `wsUrl?api_key=<authToken>` (browsers can't set WS headers, so the key is a query param)
- **on open** → send `{action:"subscribe", meetings:[{platform, native_id}]}`, then ping every 25s
- **on each inbound frame** → look up `frame.type`, fan to the matching callback
- **on close** → stop the ping loop

### The dispatch table

| `frame.type`            | action                                                                       |
| ----------------------- | ---------------------------------------------------------------------------- |
| `meeting.status`        | `onStatus(normalizeStatus(payload.status))`                                  |
| `transcript`            | `onTranscript({ speaker, confirmed, pending })`                              |
| `transcription_segment` | `onTranscript({ segments: [frame] })` (single segment wrapped to one shape)  |
| `chat_message`          | `onChat(frame)`                                                              |
| `error`                 | `onError(frame.error)`                                                       |
| `subscribed` / `pong`   | no-op (server acks)                                                          |
| _anything else_         | no-op (the ws.v1 contract is additive — unknown tags are ignored)            |

`normalizeStatus` maps `needs_help → needs_human_help` — the dashboard owns its status vocabulary
(per ADR-0023); everything else passes through untouched (`payload.status` is an open string).

Frame + status types are imported by package name from [`@vexa/dash-contracts`](../dash-contracts/)
(the 0.10.6 ws.v1 truth) — this brick conforms to that seam, it never redefines wire shapes.

## Surface

`createWsClient(opts) → { start, stop }` · `normalizeStatus` · `PING_INTERVAL_MS` · interface
`WsTransport` (+ types `CreateWsClientOptions`, `WsClient`, `TranscriptUpdate`). Front door:
[`src/index.ts`](src/index.ts). Transport seam: [`src/ports.ts`](src/ports.ts). Test fake:
[`createFakeWsTransport`](src/fakes.ts).

## Verify

`pnpm --filter @vexa/dash-ws test` (→ `tsx src/ws.test.ts`) drives the REAL client over
[`createFakeWsTransport`](src/fakes.ts): open → a `subscribe` frame with the `api_key`; a golden
`meeting.status` active → `onStatus("active")`; `needs_help` → `onStatus("needs_human_help")`; a
`transcript` bundle → the confirmed segment reaches `onTranscript`; `transcription_segment` /
`chat_message` / `error` fan correctly; `subscribed` / `pong` / unknown are no-ops. Exit code is the
signal (0 = pass). `pnpm --filter @vexa/dash-ws run build` — `tsc` clean. The live browser path
(real `WebSocket` wrapper implementing `WsTransport`) is validated against a running gateway.
