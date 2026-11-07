# api/__tests__

Unit tests for the api proxy layer (e.g. proxyAuth key resolution).

`imageOptimizer.test.ts` — `/_next/image` is answered by `server.mjs` with a 404 and never reaches Next.

`wsUpstream.test.ts` — the WebSocket upgrade matcher `server.mjs` routes by (`/ws`, a session's live view, refusals).
