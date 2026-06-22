# @vexa/dash-meeting-state — meeting FSM + live transcript assembly

_dashboard_new/ · module · the single source of truth for ONE meeting's live view._

A tiny **framework-agnostic observable store** (deliberately **NOT Zustand**) that composes the
dashboard's infra bricks through **injected ports**. It assembles one meeting's status, merged
transcript, and chat from REST (seed) + WS (live), and exposes immutable snapshots a UI layer can
subscribe to. No React, no Zustand, no browser globals — the store is pure logic over ports, so it's
deterministic to test.

```ts
const store = createMeetingState({ apiClient, wsClientFactory, meeting });
const off = store.subscribe((s) => render(s));   // s: { status, segments, chat, connection }
await store.bootstrap();   // REST: getTranscripts → seed confirmed segments + status
store.connectLive();       // WS: live status / transcript / chat → merge into the store
// …later…
store.stop();              // tear the socket down, connection → "closed"
off();
```

## State

```ts
interface MeetingState {
  status: MeetingStatus | string;          // the meeting/bot FSM state (dash-contracts vocabulary)
  segments: TranscriptSegment[];           // the merged two-map transcript, sorted by absolute_start_time
  chat: ChatMessageFrame[];                // bot chat, append-only
  connection: "idle" | "connecting" | "live" | "closed";
}
```

## Composition (injected ports, no package coupling)

- **`apiClient`** — the [`@vexa/dash-api-client`](../dash-api-client/) `ApiClient` port. `bootstrap()`
  pulls `getTranscripts(platform, native_id)`, normalizes the REST segments (start/end **seconds** →
  the live shape), and seeds the **confirmed** map. Status is taken from the response.
- **`wsClientFactory`** — builds a [`@vexa/dash-ws`](../dash-ws/) `WsClient` wired to this store's
  reducers: `onStatus → set status` (closes the socket on a **terminal** status — `completed` /
  `failed`), `onTranscript → merge a two-map tick`, `onChat → append`.

The bricks are satisfied **structurally** (minimal port interfaces here), not imported by package
name — so this brick stays decoupled and testable over the fakes. Types come from
[`@vexa/dash-contracts`](../dash-contracts/) (the consumed seam); wire shapes are never redefined.

## The TWO-MAP live-transcript model — `@vexaai/transcript-rendering`

The rendering pipeline is **NOT reimplemented here** — it is the published **`@vexaai/transcript-rendering`**
package (v0.4.x), the single source of truth the vendored dashboard also consumes. `createMeetingState`
drives its `createTranscriptManager`: `bootstrap()` seeds the confirmed map from REST, `handleMessage()`
applies each live `transcript` tick, and both return the finalized array (identity-dedup → sort →
overlap-dedup → sort). The two maps it manages:

| map                | keyed by                                      | update rule                                   |
| ------------------ | --------------------------------------------- | --------------------------------------------- |
| `confirmed`        | `segment_id` (fallback `absolute_start_time`) | **append-only** — upsert by key, never remove |
| `pendingBySpeaker` | `speaker`                                      | **fully replaced** per tick for that speaker  |

Both confirmed AND pending segments render (pending shown as in-progress drafts); a pending draft is
dropped only once it's **stale** vs. a confirmed text for the same speaker (equal / prefix / superstring)
— that's what stops the "show, disappear, come back" flash when a draft gets confirmed. The array sorts
on `absolute_start_time`, so every ingested segment must carry one.

[`transcript-merge.ts`](src/transcript-merge.ts) keeps only what the package needs but the producers
don't emit — the two SHAPE ADAPTERS (the package requires `text` + `absolute_start_time`): the **REST**
seed (`restSegmentToLive`) passes the backend's `absolute_start_time` through; the **WS** live segments
carry the time as an EPOCH `start` (seconds since 1970) with `absolute_start_time` null, so
`normalizeLiveSeg` **derives** it from the epoch `start` (and maps `start`/`end` → `start_time`/`end_time`)
before the segment enters the manager. Without that, every live segment would be filtered and only a REST
reload would show transcripts. A `transcription_segment` frame (single live segment) is delivered by
dash-ws as `{ segments:[seg] }` and folded in as a confirmed segment.

## Surface

`createMeetingState(opts) → { getState, subscribe, bootstrap, connectLive, stop }`. Also re-exports
the merge primitives (`createTranscriptState`, `bootstrapConfirmed`, `applyTranscriptTick`,
`recomputeTranscripts`, `restSegmentToLive`) and the port/state types. Front door:
[`src/index.ts`](src/index.ts). Merge model: [`src/transcript-merge.ts`](src/transcript-merge.ts).

## Verify

`pnpm --filter @vexa/dash-meeting-state test` (→ `tsx src/meeting-state.test.ts`) drives the REAL
store over the REAL infra fakes — `createFakeApiClient` (a golden transcript carrying
`absolute_start_time`) and the REAL `createWsClient` over `createFakeWsTransport`:

- `bootstrap()` → two segments seeded from REST, sorted, status seeded.
- `connectLive()` → connection `live`, transport connected with `api_key`, `subscribe` frame sent.
- `meeting.status active` → status `active`.
- a `transcript` bundle (confirmed + pending) → confirmed appended, pending draft shown.
- a **second** pending for the same speaker → **replaces** the draft (no duplicate).
- `meeting.status completed` → status `completed`, connection `closed`, socket torn down;
  `connectLive()` after close is a no-op.

Exit code is the signal (0 = pass). `pnpm --filter @vexa/dash-meeting-state run build` — `tsc` clean.
