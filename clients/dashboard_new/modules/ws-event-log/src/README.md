# ws-event-log — source

The brick's implementation. The single public surface is `index.ts` (the front door); everything else
here is internal to the brick and reached only through it.

> Presentational React debug view of the live WS frame log. Props in, DOM out: { events: WsLogEvent[] } where WsLogEvent = { ts, type, summary } — one terminal-styled row per frame, newest first. No store, no fetch, no WebSocket; the data is injected by whoever owns the socket (e.g. @vexa/dash-ws / @vexa/dash-meeting-state). Typed against @vexa/dash-contracts' WS vocabulary. L4-gated by a real chromium (Playwright) that mounts it over golden events and asserts the rendered rows.

