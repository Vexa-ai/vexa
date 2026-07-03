# api/chat

[`route.ts`](route.ts) — SSE proxy for the agent chat turn. `POST /api/chat` streams the
agent's reply from agent-api as `text/event-stream`. Chat needs the streaming + abort lifecycle
(like `/api/meeting/stream`), not the generic buffering `[...path]` JSON proxy — so it owns its own
downstream controller: closes on upstream end, errors on drop, and aborts the upstream fetch when
the client disconnects. The upstream key is resolved per-request via `proxyAuth.resolveApiKey`.
