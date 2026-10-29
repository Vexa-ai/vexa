# ws.v1 — the live WebSocket multiplex (`/ws`), frozen to vexa **main**

The single public WebSocket api-gateway serves at **`/ws`** for live updates — transcripts,
bot status, and chat — multiplexed over one authenticated connection. Pinned IDENTICAL to
vexa `main` (the shapes are exactly those main's **G5 WebSocket gate test** asserts:
`services/api-gateway/tests/test_gate_g5_websocket.py`). This is the companion to
[`api.v1`](../api.v1/) (REST) — the *streaming* half of the public surface the dashboard,
SDKs, and any client build against.

## Protocol
- **Connect:** `ws(s)://<host>/ws` with the key in the `x-api-key:<token>` header, **or** — for a
  client that cannot set a header, such as a browser — in the subprotocol list:
  `new WebSocket(url, ["vexa.v1", "vexa.key.<token>"])`; the server answers `vexa.v1`. The key then
  rides in `Sec-WebSocket-Protocol`, never in the URL. The `?api_key=<token>` query is
  **deprecated**: still accepted, taken after the header and the subprotocol, logged as
  `ws_query_key_deprecated` (never the key), and removed in 0.13.6, after which it is refused with
  `{type:"error", error:"api_key_query_removed", changed_in, do_instead, docs}` and close `4401`.
  Missing key → `{type:"error", error:"missing_api_key"}` then close `4401`; a key that
  resolves to nobody → `invalid_api_key`, close `4401`; the key cannot be resolved now →
  `auth_unavailable`, close `4503`. Any valid key opens the socket, as in 0.12; a key that may read no
  meeting status over REST (`GET /meetings`, `GET /bots/status`) is logged `ws_scope_not_enforced`.
  **Deprecated, removed in 0.13.6:** from then such a key gets `insufficient_scope` (with `changed_in`,
  `do_instead`, `docs`) and close `4403`.
- **Client → server:** `SubscribeRequest` `{action:"subscribe", meetings:[{platform, native_id}]}`
  (and `UnsubscribeRequest`). Authorization is delegated to the collector
  (`POST /ws/authorize-subscribe`): you only receive meetings your key may read (its own, a meeting
  bound to a workspace it belongs to, a meeting shared with it). Any valid key may subscribe, as in
  0.12; one without the scopes of `GET /transcripts/{platform}/{native_meeting_id}` (`tx`) is logged
  `ws_scope_not_enforced`. **Deprecated, removed in 0.13.6:** from then a subscription takes `tx`, on
  the gateway and in `POST /ws/authorize-subscribe`; otherwise `insufficient_scope`, and the socket
  stays open.
- **Live re-authorization:** access is re-checked while the socket is open, not only at subscribe.
  Every ~15 s the gateway asks `POST /ws/authorize-subscribe` again for the meetings this socket is
  subscribed to; a meeting that no longer passes (the owner removed the reader, turned a share link
  off, or a workspace membership ended) has its fan-in stopped and the client receives
  `{type:"error", error:"subscription_revoked", details:{platform, native_id}}` — the socket stays
  open. The key is re-resolved on the same tick: a membership change re-derives the workspace status
  channels, and a key that no longer resolves gets `invalid_api_key` and close `4401`. A failed
  authorize hop is retried on the next tick; two failures in a row stop the per-meeting
  subscriptions with `authorization_call_failed` (fail closed), and the client may subscribe again.
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

  - `Notification` `{type:"notification", notification:{id, kind, actor, object, text, created, read}}`,
    `NotificationRead` `{type:"notification.read", unread}` and `Rail` `{type:"rail", change, session}`
    — from `u:{user_id}:notifications`, which the socket fans in at connect for its own person
    (#1783). agent-api is the one writer; a notification's text names the actor and the object only.

  - `MeetingSignals` `{type:"meeting.signals", meeting:{id,platform,native_id}, annotations:[{id, segment_id, scores, words, model?, brief_version?, at?}]}`
    — transcript signals, from the redis **stream** `sig:meeting:{id}` (the signals service is its one
    writer). Only for a subscription that asked: `{action:"subscribe", meetings:[…], streams:["signals"]}`
    (`streams` is optional and additive; absent means `["transcript"]`, the channels above, so a 0.12
    client sees nothing new). The newest 500 entries come first, then each new batch live. Signals are
    read for the meeting ROW the authorize hop granted, so they reach exactly the people who may read
    that meeting's transcript; live re-authorization and `subscription_revoked` stop them the same way.
    `scores` is `{label: 0..1}` against the meeting's listening brief; `words` is `{word: 0..1}`.

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

**Re-sealed in place in 0.13.4.** The founder ruled to keep one socket protocol and change `ws.v1` in
place: «вычищай, пожалуйста, все… Нужно чтобы все было состояло из минимального количества
примитивов». 0.13.4 adds the `vexa.key.<key>` subprotocol, and the `api_key_query_removed` code and the
`changed_in` / `do_instead` / `docs` fields on `Error`. It keeps what 0.12 clients rely on (the URL key,
and subscribing without `tx`) as deprecated, so no client already deployed breaks; 0.13.6 removes both.
See [What changed in 0.13.4: auth](https://docs.vexa.ai/changes-0-13-4-auth).
