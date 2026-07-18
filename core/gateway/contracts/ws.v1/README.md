# ws.v1 — the live WebSocket multiplex (`/ws`), frozen to vexa **main**

The single public WebSocket api-gateway serves at **`/ws`** for live updates — transcripts,
bot status, and chat — multiplexed over one authenticated connection. Pinned IDENTICAL to
vexa `main` (the shapes are exactly those main's **G5 WebSocket gate test** asserts:
`services/api-gateway/tests/test_gate_g5_websocket.py`). This is the companion to
[`api.v1`](../api.v1/) (REST) — the *streaming* half of the public surface the dashboard,
SDKs, and any client build against.

## Protocol
- **Connect:** `ws(s)://<host>/ws` with auth `x-api-key:<token>` header **or** `?api_key=<token>`
  query. Missing key → `{type:"error", error:"missing_api_key"}` then close `4401`.
- **Client → server:** `SubscribeRequest` `{action:"subscribe", meetings:[{platform, native_id}]}`
  (and `UnsubscribeRequest`). Authorization is delegated to the collector
  (`POST /ws/authorize-subscribe`) — you only receive meetings your key may read.
- **Server → client (control):** `Subscribed` `{type:"subscribed", meetings:[…]}`, `Unsubscribed`,
  and `Error` `{type:"error", error:<code>, details?}`. `error` is a fixed code (a formal `enum` in
  the schema). Control/auth codes: `missing_api_key`, `invalid_json`, `invalid_subscribe_payload`,
  `invalid_unsubscribe_payload`, `unknown_action`. Downstream-auth codes (emitted by the deployed
  gateway's `POST /ws/authorize-subscribe` path — production-only, not exercised by the offline
  conformance harness): `authorization_service_error` (carries `status`+`detail`),
  `authorization_call_failed` (carries `details`).
- **Server → client (live data, forwarded raw from redis) — the canonical 0.10.6 shapes:**
  - `Transcript` `{type:"transcript", speaker?, confirmed[], pending[], meeting?, ts?}` — the per-speaker
    bundle the collector publishes + the dashboard renders live — from `tc:meeting:{id}:mutable`.
  - `TranscriptionSegment` `{type:"transcription_segment", text, speaker?, …}` — a single segment, also
    valid on `tc:meeting:{id}:mutable` (the G5 sample shape).
  - `MeetingStatus` `{type:"meeting.status", meeting:{id,platform,native_id}, payload:{status}, user_id?, ts?}`
    — from `bm:meeting:{id}:status` (status under `payload.status`).
  - `ChatMessage` `{type:"chat_message", sender?, text}` — from `va:meeting:{id}:chat`.

  Data messages are **type-tagged and additive** (the gateway forwards the producer's raw
  payload unchanged), so `type` + the listed required field are the floor; extra fields are allowed.

## The seam (what conforms to this)
| Consumer | How |
|---|---|
| `clients/terminal` | proxies `/ws` server-side; renders `transcript` live + `meeting.status`/`chat_message` |
| `meetings/services/meeting-api` (+ collector) | publishes the data shapes to the redis channels above |
| any client | subscribe + consume the type-tagged stream |

`meeting.status` is the **authoritative state channel** for a client's bot/meeting-state
controls: while this stream is not connected, a client MUST degrade state-bearing controls
(Stop/Send bot) to indeterminate/disabled — a cached REST snapshot is display-only, never a
basis for an actionable control.

## Re-verify / re-seal
Shapes are pinned by main's gate test. To re-verify: `node gateway/contracts/ws.v1/validate.mjs`
(goldens ≡ `$defs`). A deliberate main change → update the schema/goldens + `pnpm seal:contracts`
on a `lane:contract` PR; a breaking change opens `ws.v2`, leaving `ws.v1` until no consumer pins it.
