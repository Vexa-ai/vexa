# api (route handlers)

Server-side proxy routes — the browser calls these same-origin; they forward to the **gateway**
(`GATEWAY_URL`), the ONE authenticated edge, so the backend host + the user's key stay server-side.
Each call carries the per-user `X-API-Key` (resolved in `proxyAuth.ts`); the gateway resolves it →
user and injects `X-User-Id` downstream, so agent-api derives `subject` from identity — the client
never sends one (P20 scope). Path routing: meetings · transcripts · bots · bot-accounts → the gateway root;
everything else (chat · sessions · routines · workspace · models) → the gateway's `/api/*`.

One front door for the client (P6): surfaces fetch `/api/*`, never agent-api directly.

`imageOptimizer.mjs` — read by `server.mjs`: the image optimizer is off, so `/_next/image` is answered with a 404 before Next sets up a cache it cannot write.

`frameDoor.mjs` — read by `server.mjs`, before any route: the Meeting Canvas host (`TERMINAL_CANVAS_ORIGIN`) serves the frame document and the build's files by exact path and nothing else; the terminal refuses to be framed by another origin and serves no script, stylesheet or font to one but its build files (from `Sec-Fetch-*`). A refusal carries `x-vexa-refused` and never echoes the URL.

`wsUpstream.mjs` — read by `server.mjs`: which WebSocket upgrades go to the gateway — `/ws` and a browser session's live view `/bot-accounts/sessions/<id>/vnc`, both with the per-user key; other `/bot-accounts/` shapes are refused.
