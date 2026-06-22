# @vexa/dashboard-new — fresh modular dashboard

A **fresh, modular** dashboard rebuilt from scratch (sibling of [`clients/dashboard`](../dashboard/)),
following the repo **brick paradigm**: every concern is a self-contained brick under
[`modules/`](modules/) with exactly **one public front door** (`src/index.ts`), a `README.md`
(one concern + its surface), and a `tsx` test whose **exit code is the signal** (0 = pass).

## The consumed truth

The dashboard is a **consumer**. The seam to the backend is frozen, not invented: the **0.10.6 WS
contract** ([`core/gateway/contracts/ws.v1/ws.schema.json`](../../core/gateway/contracts/ws.v1/ws.schema.json))
plus the sealed REST surface ([`core/gateway/contracts/api.v1/`](../../core/gateway/contracts/api.v1/))
are the **single source of truth** for every shape the UI reads. Bricks conform to those schemas —
they never redefine them.

## Layout

- `modules/<brick>/` — one brick per concern. ESM (`"type": "module"`), minimal deps, `tsc`-built
  `dist/`, `tsx` tests (logic bricks) or a Playwright `e2e/` spec (view bricks).
- `modules/dash-contracts/` — the **foundation brick**: the single consumed-contract seam. A
  **types-only `.` front door** (browser-safe, fully erased) + a **node-only `/validate` subpath** (the
  fs-backed ajv validators that load the on-disk sealed schemas and pin every golden).
- `src/app/` — the **Next.js composition root** (the `create_app` analogue). It is the *only* place the
  bricks meet:
  - `src/app/api/config/` + `src/app/api/vexa/[...path]/` — the server seam: `/api/config` resolves the
    browser runtime config via `@vexa/dash-config`; `/api/vexa/*` proxies REST to the gateway and
    injects the api key **server-side** (the browser holds a token only for the WS, which can't set
    headers).
  - `src/app/providers.tsx` — wires the live ports: a `dash-api-client` over the `/api/vexa` proxy and a
    `dash-ws` client (over a browser `WebSocket` transport) for `dash-meeting-state`'s `wsClientFactory`.
  - screens — `meetings/` (list), `meetings/[id]/` (the meeting-detail composite: `dash-meeting-state` +
    the transcript/recording/status/ws-log/chat/vnc view bricks), `join/` (start a bot).
- `e2e/` — the **L4 real-browser** harness (Playwright): renders bricks against goldens in chromium.

## Build & run

`npm install` (workspace root) then `npm run build` (= `next build`, the integration gate) — requires
`VEXA_API_URL` (the deploy SSOT for the `/ws` + `/b/` rewrites and the REST proxy). `npm run dev` serves
on `:3002`. Per-brick: `npm test` inside a brick (logic = tsx; views = Playwright run from the brick).

## Rules

Each brick owns ONE concern, exposes it through `src/index.ts`, documents it in `README.md`, and
proves it with `npm test`. Do not reach across brick boundaries — depend on a brick's front door, never
its internals. The composition root may wire bricks together but holds no domain logic of its own.
