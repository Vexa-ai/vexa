# viewer — the live eyeball for a standalone bot run

A **dumb, transport-agnostic presentation sink** for the bot-local L4 harness ([../README.md](../README.md)):
it holds an in-memory view of ONE run — the `lifecycle.v1` timeline + `transcript.v1` segments + the
final verdict — and streams it to the browser over SSE. It knows nothing about redis / ssh / bbb;
every datum arrives as an HTTP `POST`, so the same viewer serves a bot on a remote VM (fed by
`../feed.mjs` over ssh) OR a bot on the local docker network (point the bot's `meetingApiCallbackUrl`
straight at `/lifecycle`). That separation keeps it offline-testable (curl in, watch the page) and reusable.

- **`server.mjs`** — the SSE server + ingress endpoints: `POST /lifecycle` (one lifecycle.v1 event),
  `POST /transcript` (one transcript.v1 segment), plus the verdict; `GET /` serves the page and
  `GET /events` is the SSE stream.
- **`index.html`** — the single-page UI that subscribes to `/events` and renders the timeline,
  transcript, and verdict live.

Run: see [../README.md](../README.md) / [../run.sh](../run.sh) — the harness starts the viewer and points
the bot's callbacks at it.
