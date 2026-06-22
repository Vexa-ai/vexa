# @vexa/dash-contracts — the consumed-contract seam

_dashboard_new/ · brick · the single seam between the dashboard and the backend._

The dashboard is a **consumer**. This brick is the **one place** that knows the shapes the UI reads,
and it is grounded verbatim on the **sealed** contracts in
[`core/gateway/contracts/`](../../../../core/gateway/contracts/) — it conforms to them, it never
redefines them.

- **WS frames** mirror [`ws.v1/ws.schema.json`](../../../../core/gateway/contracts/ws.v1/ws.schema.json)
  (the 0.10.6 live `/ws` multiplex truth): `MeetingStatusFrame`, `TranscriptFrame`,
  `TranscriptionSegmentFrame`, `ChatMessageFrame`, `SubscribedFrame`, `UnsubscribedFrame`, `PongFrame`,
  `ErrorFrame` → the `WsFrame` discriminated union (on `type`). Plus `TranscriptSegment`, `MeetingRef`,
  `Platform`, and the `MeetingStatus` union (`requested | joining | awaiting_admission | active |
  needs_help | needs_human_help | stopping | completed | failed` — the api.v1 enum seals
  `needs_human_help`; `needs_help` is carried as the surface alias, and `payload.status` is open by
  design).
- **REST shapes** mirror [`api.v1/api.schema.json`](../../../../core/gateway/contracts/api.v1/api.schema.json)
  (frozen OpenAPI 3.1, "Vexa API Gateway" 1.5.0): `MeetingResponse`, `MeetingListResponse`,
  `TranscriptionResponse`, `TranscriptionSegment` (REST), and `RecordingMaster` (the
  `/recordings/{id}/master` projection — not a sealed component, so typed here).

The frames are **additive**: the gateway forwards the raw redis payload verbatim, so the types are a
required floor + optional carried fields (extra keys allowed).

## Surface — two doors, by runtime

The brick has **two** entry points because its halves have different runtimes:

- **`@vexa/dash-contracts`** (the `.` export, [`src/index.ts`](src/index.ts)) — **TYPES ONLY**. Every
  export is a TS type/interface, so the import is **fully erased at compile**: a browser bundle that
  imports it carries **zero runtime**. This is the door the browser-bound bricks (api-client, ws,
  meeting-state, the views) use.
- **`@vexa/dash-contracts/validate`** ([`src/validate.ts`](src/validate.ts)) — the **NODE-ONLY** ajv
  validators: `validateWsFrame(shape, frame)` · `validateApiShape(shape, obj)` · `apiIdentity` ·
  `WS_SHAPES` · `API_SHAPES`. These **load the on-disk sealed schemas** (walking up to find
  `core/gateway/contracts/`, like `meeting-api/tests/test_lifecycle_durable.py`) via `node:fs` and
  compile per-shape ajv validators — the schema is the spec, never copied in. Consumed by the L1/L2
  tests, and **injectable** into `createHttpApiClient` (its `validate` option) — never in the browser.

The split is deliberate: an fs-backed validator on the `.` front door would drag `node:fs` into every
browser bundle that only wanted a type. Types go through `.`; the fs validators go through `/validate`.

## Verify

`npm run build` — `tsc` clean. `npm test` runs
[`src/contracts.test.ts`](src/contracts.test.ts) via `tsx` (exit code is the signal): it loads **every**
ws.v1 golden + a couple api.v1 goldens and asserts each conforms to its validator (filename prefix →
shape, like `ws.v1/validate.mjs`), pins the api.v1 identity (1.5.0), and proves the exported TS types
parse a `MeetingStatus` + a `Transcript` golden.

```bash
cd clients/dashboard_new/modules/dash-contracts
npm i --no-audit --no-fund
npx tsx src/contracts.test.ts
```
