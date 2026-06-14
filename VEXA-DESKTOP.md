# Vexa Desktop — spec (all-Node)

> A Docker-free, cross-platform (mac/linux/windows) local deployment of the whole
> Vexa data plane + the real dashboard, on an embedded SQLite file — that is at the
> same time the canonical **hot-debug** environment. **The product you ship is the
> rig you develop in.** Decision locked: **all-Node** (one runtime, no Python /
> Postgres / Redis / Docker). Updated 2026-06-14.

## Why all-Node
A desktop deployment must install clean on a user's laptop on all three OSes — so
it can't drag Docker, Python, Postgres, or Redis along. The data plane (`@vexa/*`
bricks) and the dashboard (Next.js) are **already Node**; only the control plane
(`meeting-api`, FastAPI) is the outlier. Porting its *small* client-facing surface
to a typed Node service on SQLite gives a single packageable runtime and a single
hot-reload story. STT stays **remote by default** (the hosted endpoint); an
optional bundled `whisper.cpp` is the offline path.

## Topology

```
┌─ Vexa Desktop (Electron · mac/linux/windows · no Docker) ───────────────┐
│                                                                          │
│   window ─► dashboard (Next.js)        next dev (hot) / next start (ship)│
│                  │ REST: /bots /transcripts   │ live: WS /ws            │
│                  ▼                              ▼                        │
│   embedded backend (Node · tsx watch hot / bundled ship):               │
│     ingest  ws :9099  ◄───────────────────────────── browser extension  │
│        └─ pipeline (mixed ‖ multistream) ─► speaker-attribution          │
│             ├─ delivery: in-proc WS broadcast (NO Redis) ─► dashboard    │
│             ├─ recording tee: StreamCaptureWriter ─► stream.capture      │
│             └─ control plane ─► node:sqlite file (NO Postgres)           │
│                                                                          │
│   STT ↗ hosted transcription.vexa.ai  (optional: bundled whisper.cpp)    │
└──────────────────────────────────────────────────────────────────────── ┘
   capture = the browser extension (meetings live in the browser);
   Desktop hosts everything downstream. No headless bot / join / Playwright.
```

In one process you get what compose splits across **5 services** (ingest-server,
api-gateway, pipeline, redis, postgres): no Redis (in-proc delivery), no Postgres
(SQLite file).

## The control-plane surface to port (Node, the minimal real subset)
Grounded in what the extension + dashboard actually call (api-gateway public
surface — *not* the bot-lifecycle callbacks, *not* dashboard-local `/api/*`):

| endpoint | who | purpose |
|---|---|---|
| `POST /extension/sessions` (+ `/end`) | extension/ingest | resolve a session → `meeting_id` (the `resolveSession` the ingest does today) |
| `GET /bots`, `GET /bots/id/{meeting_id}` | dashboard, ingest status-watch | meeting/session list + live status |
| `GET /transcripts/{platform}/{native_id}` | dashboard, API | confirmed transcript history |
| `WS /ws` (+ `/ws/authorize-subscribe`) | dashboard, extension | live segments (in-proc broadcast) |

**Out of scope for Desktop** (cloud-only / stubbed): bot-lifecycle callbacks
(`/bots/internal/...` — no headless bot), `/recordings/*` master/media (Desktop
keeps the local recording tee instead), `/calendar`, `/billing`, admin. **Auth →
single local user** (no-op token) in Desktop; the dashboard's own `/api/auth`,
`/api/ai`, `/api/billing` Next routes stay local/stubbed.

## SQLite schema (mirror of meeting-api models, SQLite-flavored)
- **meetings** — `id, platform, native_id, status, start_time, end_time, data (JSON via JSON1), created_at`
- **sessions** — `meeting_id, session_uid, session_start_time`
- **segments** (transcriptions) — `meeting_id, start, end, text, speaker, language, session_uid, segment_id`

`JSONB → TEXT + JSON1`; dev bootstrap = `create_all` equivalent (no Alembic). The
pipeline writes **confirmed** segments here; `GET /transcripts` reads them; live
segments go straight out the WS (not via the DB).

## The shell + hotness
- **Shell:** Electron — main process spawns/embeds the Node backend, the window
  loads the dashboard. (A tray-daemon + system browser is the fallback if we want
  to avoid Electron weight.)
- **Dev mode:** one `npm run dev` (a small orchestrator / `concurrently`) launches
  **backend `tsx watch`** + **dashboard `next dev`** + **extension `esbuild` watch**.
  Edit any layer → live. **Ship mode:** the same three, built + bundled by Electron.
- **lite-db:** `node:sqlite` (built into Node 22+, you're on 24 → zero deps) — or
  `better-sqlite3` (prebuilt binaries) if we want a non-experimental API.

## The one open decision: is the Node control plane *canonical*?
Desktop introduces a **second** control-plane implementation alongside the Python
`meeting-api`. To avoid drift, the client-facing surface above becomes a **contract**
both implement (a tiny OpenAPI/`control.v1`), gated like the other contracts. Two
stances:
- **Desktop-only variant** (now): Node CP serves Desktop; Python `meeting-api` stays
  the cloud/server (Compose/Helm) control plane. Additive, zero risk to cloud.
- **Canonical Node CP** (later): cloud migrates onto the Node control plane; one
  implementation. Bigger call — out of 0.11 scope.
For 0.11: **Desktop-only variant**, contract-bound.

## Where it fits the 0.11 plan
- **It IS the "full setup" Lane 4 wanted** — `npm run dev` = transcripts live in the
  dashboard **and** the `stream.capture` fixture recorded, in one hot process. So
  building it *accelerates* fixture collection + extension testing for 0.11.
- **It collapses the dev-tool sprawl** — `capture-recorder`, `live-stack`,
  `live-ingest` all become "Vexa Desktop, dev mode."
- **As a product** (Electron-packaged, signed installers per OS) it's a new
  **deployment target** alongside Lite/Compose/cluster — likely **post-0.11**, but
  the dev rig lands now.

## Build order (incremental — each step independently useful)
1. **Backend MVP** — evolve `live-stack`: add `node:sqlite` control plane (sessions +
   meetings + segments) + the recording tee (`StreamCaptureWriter`) + the `/bots` /
   `/transcripts` REST the dashboard reads. Single process, no Electron yet.
2. **Dashboard against it** — point `next dev` at the MVP backend; prove the *real*
   dashboard renders live transcripts + history from the all-Node stack, hot.
3. **Electron shell** — wrap backend + dashboard window; one launch.
4. **Package** — `electron-builder` per OS; optional bundled `whisper.cpp`.
