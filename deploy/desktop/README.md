# Vexa Desktop — local, hot, no Docker

The all-Node deployment that doubles as the **hot debug rig**. `modules → services →
deploy`: this is the **local + hot** cell, next to `compose` / `helm` / `lite`. Spec:
[`../../VEXA-DESKTOP.md`](../../VEXA-DESKTOP.md). Option **B** (pure Node — no Python,
Postgres, Redis, or Docker; one SQLite file).

## One command

```bash
cd deploy/desktop
npm run dev
```

Launches three hot processes (edit any → it reloads):

| process | what | port |
|---|---|---|
| **backend** (`services/vexa-desktop/src/desktop.ts`, `tsx watch`) | ingest + pipeline (mixed ‖ multistream + mic) + delivery WS + recording tee + `node:sqlite` control plane | ingest `:9099`, gateway `:8056` |
| **dashboard** (`services/dashboard`, `next dev`) | the real UI, pointed at the backend | `:3001` |
| **extension** (`services/vexa-extension`, esbuild watch) | rebuilds `dist/` on edit | — |

Then load the extension (`chrome://extensions` → Load unpacked → `services/vexa-extension/dist`),
set the sidepanel `ingestUrl = ws://localhost:9099/ingest`, `gatewayUrl = http://localhost:8056`,
join a meeting → Start. Transcripts render live; the `stream.capture` fixture is recorded
to `$VEXA_FIXTURE_CACHE/capture/v1/` at the same time (collect-while-you-watch).

## Prereqs (one-time)
- Node 22+ (uses `node:sqlite`). `npm install` in `services/vexa-desktop`, `services/dashboard`,
  `services/vexa-extension`.
- STT: put `TRANSCRIPTION_SERVICE_URL` + `TRANSCRIPTION_SERVICE_TOKEN` in
  `services/vexa-desktop/.env` (remote endpoint — no local GPU).
- `cp .env.example .env` here to tweak ports / DB path.

## Subsets
```bash
DESKTOP_NO_DASHBOARD=1 npm run dev   # backend + extension only (the fixture-collection loop)
DESKTOP_NO_EXTENSION=1 npm run dev   # backend + dashboard
```

## Lite-db
One file: `~/.vexa/desktop.db` (override `VEXA_DESKTOP_DB`). Holds meetings · sessions ·
confirmed segments. Live segments go straight out the WS (no Redis); only confirmed
history is persisted. `GET /transcripts/:platform/:native` reads it; `GET /bots` lists meetings.

## Status / follow-ups
- ✅ backend proven end-to-end (real audio → named segments → SQLite → `/transcripts` → WS + recorded fixture).
- ◻ **dashboard ↔ backend API alignment** — the dashboard expects the api-gateway/meeting-api shapes + a local-user auth stub; the backend's REST must match so the UI renders unchanged.
- ◻ **schema drift gate** — the Node store mirrors `meeting-api`'s schema; a gate keeps them from diverging (the cost of pure-Node, kept honest).
- ◻ **Electron shell + per-OS packaging** — turns the rig into the shippable desktop app.
