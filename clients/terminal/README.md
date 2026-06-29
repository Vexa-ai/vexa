# terminal — the browser-CLI workbench (Next.js)

## Purpose

The user-facing client for the agent domain: a browser "terminal" that renders a
[dockview](https://dockview.dev) workbench over a registry of surfaces (chat, meeting,
workspace, routines, sessions, tasks). It owns no business logic — every surface talks to
agent-api through thin `/api/*` Next route proxies that keep the backend host (and any key)
server-side. Next.js because the workbench is a rich client UI and the proxies want a
same-origin server runtime (SSE relay, no CORS).

## Seams

| Direction | Neighbour | Via | What crosses |
|---|---|---|---|
| calls | agent-api | `POST /api/chat` (SSE proxy → `${AGENT_API}/api/chat`) | a chat now-dispatch; SSE relay of the agent's output stream |
| calls | agent-api | `GET /api/sessions?subject=` | a subject's chat-session list (resume) |
| calls | agent-api | `GET/POST /api/routines`, `DELETE /api/routines/{id}` | list / create / delete a `routine.v1` cron job |
| produces | agent-api | `POST /api/events` (→ `${AGENT_API}/events`) | an `event.v1` Event → a `unit.v1` Invocation → Dispatcher |
| calls | meeting-api (via gateway) | `GET /meetings` + `WS /ws` (`u:{user}:meetings`) | the user's meetings (live + past); live status deltas over the socket (no poll) |
| calls | agent-api | `GET /api/meeting/stream?meeting_id=&session_uid=` (SSE, `EventSource`) | live transcript + copilot output wire |
| calls | meeting-api (via gateway) | `POST /bots`, `DELETE /bots/{platform}/{native}` | launch / stop a self-hosted meeting bot |
| calls | agent-api | `GET /api/workspace/{tree,file,git}?subject=` (git polled 5s) | workspace tree, file content, the agent's real git state |
| consumes | browser | dockview workbench + surfaces registry (`src/surfaces/index.tsx`) | LEFT lists, CENTER tab-kinds, RIGHT context-kinds, `/`-skill commands |

All upstreams resolve to `AGENT_API_URL` (default `http://127.0.0.1:18100`).

## Contracts

**Owns:** none — the terminal defines no `*.v1`; it is a pure client of the agent domain.
**Consumes:** `core/agent/contracts/event.v1` (the `/api/events` ingress shape), `routine.v1`
(routines CRUD), `unit.v1` (chat + SSE relay), and the meeting/workspace surfaces of
`core/agent/services/agent-api`. Schemas are sealed in `contracts.seal.json` (repo root) — the
proxies forward bodies verbatim, they do not re-declare schemas.

## Isolated evaluation

No test suite yet (`tests/` absent). Standalone build + typecheck:

```bash
pnpm install && pnpm build      # next build = typecheck + lint (L1/L2)
pnpm dev                        # next dev -p 3000 — drive surfaces against a live agent-api (L4)
```

## Status

- ✅ delivered — dockview workbench + surfaces registry (chat / meeting / workspace / routines / sessions / tasks)
- ✅ delivered — `/api/chat` SSE proxy + resumable chat sessions (`/api/sessions`)
- ✅ delivered — routines board over `/api/routines` CRUD
- ✅ delivered — workspace files + docs viewer + git source-control panel (5s poll)
- ✅ delivered — live meeting surface: `/meetings` + `/ws` status (no poll) + `/api/meeting/stream` SSE + bot start/stop via `/bots`
- ✅ delivered — generic event ingress proxy (`/api/events` → `event.v1`)
- 🟡 partial — hardcoded `subject` per surface (`u_jane` / `u_live`), no real identity
- ⬜ planned — login (Google + dev type-any-email) → replace the hardcoded `subject` with the authenticated user
- ⬜ planned — real meetings list (live + past) with a recorded view
- ⬜ planned — routines type-toggle (agent | meeting)
- ⬜ planned — meeting ↔ doc cross-links
- ⬜ planned — a single gateway WS client replacing the polls
