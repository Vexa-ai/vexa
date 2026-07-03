# api (route handlers)

Server-side proxy routes — the browser calls these same-origin; they forward to the **gateway**
(`GATEWAY_URL`), the ONE authenticated edge, so the backend host + the user's key stay server-side.
Each call carries the per-user `X-API-Key` (resolved in `proxyAuth.ts`); the gateway resolves it →
user and injects `X-User-Id` downstream, so agent-api derives `subject` from identity — the client
never sends one (P20 scope). Path routing: meetings · transcripts · bots → the gateway root;
everything else (chat · sessions · routines · workspace · models) → the gateway's `/api/*`.

One front door for the client (P6): surfaces fetch `/api/*`, never agent-api directly.
