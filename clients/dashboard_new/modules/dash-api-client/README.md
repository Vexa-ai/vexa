# @vexa/dash-api-client — the api.v1 REST client behind a PORT

_dashboard_new/ · brick · the dashboard's typed read/write of the sealed api.v1 REST surface._

The dashboard talks to the backend through **one interface**, `ApiClient`, never through `fetch`
directly. Two implementations sit behind that port, swappable without touching a caller:

- **`createHttpApiClient({ baseUrl, fetchImpl?, apiKey?, validate? })`** — the real client over HTTP. It
  hits the sealed [api.v1](../../../../core/gateway/contracts/api.v1/) paths off `baseUrl`. Response
  validation is **injected, not hard-wired**: pass `validate` (the node-only
  [`@vexa/dash-contracts/validate`](../dash-contracts/) `validateApiShape`) and a drifted backend fails
  LOUD here; **omit it (the browser default)** and the client is a pure typed pass-through — so the
  fs-backed ajv validator is never dragged into the browser bundle (shape drift is caught at L1/L2, not
  per-request). `fetchImpl` is injected too (default global `fetch`) — tests pass a stub; no DOM/`window`
  dependency.
- **`createFakeApiClient(seed?)`** — in-memory api.v1 **golden-shaped** responses (no network): two
  meetings (active + completed), a transcript, a recording master. `postBot` mints a `requested`
  meeting and `deleteBot` flips it to `stopping`, so the seam behaves like the real lifecycle. Used to
  build/test the UI without a live backend.

Every shape returned comes from `@vexa/dash-contracts` (the consumed-contract seam) — this brick never
redefines a shape; it only fetches/serves it (and validates it when a validator is injected).

## Surface

Front door: [`src/index.ts`](src/index.ts).

The port (`ApiClient`) — all returns are `@vexa/dash-contracts` shapes:

| method | api.v1 path | returns |
| --- | --- | --- |
| `getMeetings(params?)` | `GET /meetings` | `MeetingListResponse` |
| `getMeeting(id)` | `GET /meetings/{meeting_id}` | `MeetingResponse` |
| `getTranscripts(platform, nativeId)` | `GET /transcripts/{platform}/{native_meeting_id}` | `TranscriptionResponse` |
| `getRecordingMaster(recordingId, type?)` | `GET /recordings/{recording_id}/master` | `RecordingMaster` |
| `postBot(req)` | `POST /bots` | `MeetingResponse` |
| `deleteBot(platform, nativeId)` | `DELETE /bots/{platform}/{native_meeting_id}` | `void` |

Also exported: `createHttpApiClient` (+ `CreateHttpApiClientOptions`, `ApiShapeValidator`),
`createFakeApiClient` (+ `FakeSeed`), and the port types `ApiClient`, `BotRequest`, `GetMeetingsParams`,
`RecordingMasterType`, `FetchImpl`, `FetchResponse`.

`RecordingMaster` (`/recordings/{id}/master`) is not a sealed api.v1 component, so the HTTP client
reads it as the dash-contracts typed projection (no `validateApiShape` validator exists for it).

Note: this brick imports **types only** from the `@vexa/dash-contracts` `.` front door (erased at
compile, so it adds no runtime). The optional `validate` function is supplied by the *caller* — the
node-only `@vexa/dash-contracts/validate` subpath in tests/tools; nothing in the browser path.

## Verify

`npm run build` — `tsc` clean. `npm test` runs [`src/api-client.test.ts`](src/api-client.test.ts) via
`tsx` (exit code is the signal): the fake client's `getMeetings`/`getMeeting`/`getTranscripts`/
`getRecordingMaster`/`postBot` outputs each **conform to api.v1** (via `@vexa/dash-contracts/validate`),
the fake lifecycle holds (`postBot` → `requested`, `deleteBot` → `stopping`), and the HTTP client driven
by a **stub fetch** parses a golden, hits the right path/method, sends the bot body, throws LOUD on a
non-2xx response, throws on a drifted body **when a validator is injected**, and (the browser default)
**passes a drifted body through when no validator is given**.

```bash
cd clients/dashboard_new/modules/dash-api-client
npm i --no-audit --no-fund
npx tsx src/api-client.test.ts
```
