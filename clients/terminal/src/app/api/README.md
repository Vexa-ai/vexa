# api (route handlers)

Server-side proxy routes — the browser calls these same-origin; they forward to `agent-api` (`AGENT_API_URL`) so the backend host stays server-side. One front door for the client (P6): surfaces fetch `/api/*`, never agent-api directly.
