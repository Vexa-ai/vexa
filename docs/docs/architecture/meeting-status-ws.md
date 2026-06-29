# Live meeting statuses over WebSocket — user dropdown as source of truth

**Status: ⬜ planned**

Push per-meeting status to the Vexa EI terminal over the existing gateway `/ws`,
on a **user-scoped** channel, and replace the terminal's 4s poll. Add an INTENT
phase (`idle` · `scheduled`) **before** the bot FSM, where the **user dropdown is
the source of truth**; leave the bot lifecycle FSM untouched.

This doc is grounded in the real 0.12 code; every claim cites `file:line`.

---

## 0. Grounding — what exists today

### 0.1 Status model in meeting-api

- The persisted status is a **plain string column** on the `meetings` row:
  `Meeting.status = Column(String(50), nullable=False, default="requested", index=True)`
  — `core/meetings/services/meeting-api/src/meeting_api/sessions/models.py:52`.
  There is **no `idle` / `scheduled`** today; default is `requested`.
- A DB-level partial unique index enforces **at most one non-terminal meeting** per
  `(user, platform, native_id)`: `postgresql_where=text("status NOT IN ('completed','failed')")`
  — `models.py:82-87`. Terminal = `completed` / `failed`.
- The **bot FSM** lives in `lifecycle/machine.py`. `BotStatus` =
  `joining · awaiting_admission · active · needs_help · completed · failed`
  — `machine.py:26-35`. Legal edges: `None→joining`, `joining→{awaiting_admission,active,failed}`,
  `awaiting_admission→{active,needs_help,failed}`, `needs_help→{active,failed}`,
  `active→{completed,failed}` — `machine.py:78-90`. `completed`/`failed` terminal.
- The **server-side** status superset adds `requested` and `stopping`, which are NOT
  bot states: the rehydration map `_PERSISTED_STATUS_TO_BOTSTATUS` treats
  `requested → None` (the FSM's pre-`joining` entry) and `stopping → ACTIVE`
  — `machine.py:102-111`. So the real persisted vocabulary today is:
  **`requested · joining · awaiting_admission · needs_help · active · stopping · completed · failed`**.
- **Who writes each transition** (the ONE `meetings.status` column):
  - `requested` — written at spawn: `m.status = "requested"`
    (`bot_spawn/adapters.py:92,251,320`).
  - `joining … completed/failed` — written by the **bot lifecycle callback** via
    `update_meeting_status(session_uid, status, data)` after the FSM advances
    (`bot_spawn/adapters.py:148-167`, driven from the receiver in `app.py`).
  - `stopping` — written by the **user-stop** path (DELETE /bots):
    `repo.update_meeting_status(... status="stopping", data={"stop_requested": True})`
    — `lifecycle/stop_router.py:106-107`. The bot's terminal (`completed`) then lands
    normally because `stopping→ACTIVE→completed` stays legal (`machine.py:108`).
  - There is **no idle/scheduled writer** anywhere today.

### 0.2 The existing `/ws` (gateway)

`core/gateway/services/gateway/src/gateway/app.py`:

- `@app.websocket("/ws")` → `run_multiplex(ws, authorizer, redis)` — `app.py:293-295`.
- **Connect auth is shallow today**: it only checks an `x-api-key` (or `?api_key=`)
  is *present*; missing → `error:missing_api_key` + close `4401` — `app.py:315-322`.
  It does **NOT** resolve the key to a `user_id` at connect.
- The real `user_id` is resolved **per-subscribe**, inside the `authorize_subscribe`
  hop: `authorizer.authorize_subscribe(api_key, payload_meetings)` returns
  `{authorized:[{platform,native_id,user_id,meeting_id}], errors:[…]}`
  — `app.py:413-433`. The socket fans in three redis channels per meeting:
  `tc:meeting:{id}:mutable`, `bm:meeting:{id}:status`, `va:meeting:{id}:chat`
  — `app.py:351-356`, forwarding **raw redis payloads verbatim** — `app.py:336`.
- The authorizer port already exposes the connect-time resolver we need:
  `resolve(api_key) -> {user_id, scopes, …}` (admin-api `/internal/validate`)
  — `ports.py:39`, `adapters.py:54-61`. `/auth/me` already calls it the same way
  — `app.py:90-106`.

**Who publishes `bm:meeting:{id}:status`** — the meeting-api lifecycle callback, on
every *genuine* (non-`no_op`) FSM advance — `core/meetings/.../app.py:251-272`. Frame:

```json
{ "type": "meeting.status",
  "meeting": { "id": 42, "platform": "google_meet", "native_id": "abc-defg-hij" },
  "payload": { "status": "active" },
  "user_id": 7,
  "ts": "2026-03-27T10:00:00Z" }
```

(`core/meetings/.../app.py:257-267`; golden `ws.v1/golden/MeetingStatus.active.json`;
schema `ws.v1/ws.schema.json`, the `meeting.status` data message.) `payload.status` is
the **raw BotStatus value**; clients translate — `app.py:248`.

### 0.3 agent-api

`core/agent/services/agent-api/src/agent_api/api.py`:

- `_LiveMeetings` is an **in-memory** registry keyed by `session_uid` (native code):
  `add` marks `status="live"`; `stop`/`drop` mark `status="stopped"` and **keep** the row
  so "send the bot back" stays available — `api.py:55-78`. (`drop` just calls `stop`.)
- `POST /api/meeting/bot` → forwards to the gateway's `POST /bots` (spawn) — `api.py:201-230`.
- `POST /api/meeting/stop` → forwards to gateway `DELETE /bots/{platform}/{native}`,
  then `live.stop(native_id)` — `api.py:232-254`.
- `GET /api/meetings` → proxies gateway `GET /meetings` (real DB rows, all statuses) and
  **merges** the live registry; `is_live` = registry `live` OR
  `db_status in (active, joining, requested)` — `api.py:256-285`.
- Subject is the single dev identity **`u_live`**, hardcoded (e.g. chat dispatch
  `subject:"u_live"` in `meeting.tsx:317`). This is the §0-auth stub.

### 0.4 terminal

`clients/terminal/src/surfaces/`:

- `liveMeetings.ts` — `GET /api/meetings` every **4000 ms** (`poll`/`ensurePolling`,
  `liveMeetings.ts:62-84`). `LIVE_STATUSES = {active, joining, requested}`
  (`liveMeetings.ts:28`); a row is mapped to `status: "live" | "past"` (`toMock`,
  `liveMeetings.ts:41-60`). The whole UI binds via `useSyncExternalStore`
  (`liveMeetings.ts:116-123`).
- `meeting.tsx` — the per-row buttons today are just **Stop** / **Send**:
  `stopBot` → `POST /api/meeting/stop`; `sendBot` → `POST /api/meeting/bot`
  (`meeting.tsx:248-249,256-258`). No dropdown, no schedule/cancel, no idle.

---

## A. Unified status model

One **`status` enum spanning two phases** on the single canonical `meetings.status`
column. No second status column, no dual source of truth.

| phase | status | owner / writer | new? | maps to today |
|---|---|---|---|---|
| INTENT | `idle` | **user** (dropdown) / scheduler | **NEW** | — (no row, or a parked row) |
| INTENT | `scheduled` | **user** (dropdown) / scheduler | **NEW** | — |
| LIVE (bot FSM) | `requested` | meeting-api spawn | existing | `requested` |
| LIVE (bot FSM) | `joining` | bot FSM callback | existing | `joining` |
| LIVE (bot FSM) | `awaiting_admission` | bot FSM callback | existing | `awaiting_admission` |
| LIVE (bot FSM) | `needs_help` | bot FSM callback | existing | `needs_help` |
| LIVE (bot FSM) | `active` | bot FSM callback | existing | `active` |
| LIVE (in-flight stop) | `stopping` | user-stop path | existing | `stopping` |
| TERMINAL | `completed` | bot FSM callback | existing | `completed` |
| TERMINAL | `failed` | bot FSM callback | existing | `failed` |
| TERMINAL (user-cancelled) | `stopped` | **user** (dropdown) | existing-ish* | see note |

\* `stopped` exists today only in agent-api's in-memory `_LiveMeetings`
(`api.py:71`), **not** as a meeting-api DB value. Decision below: keep `stopped` as a
*terminal-equivalent* display state derived from `completed` + `data.stop_requested`,
**not** a new DB enum value — so the bot FSM and the partial-unique-index
(`completed/failed`) stay untouched. The dropdown's "Stop" still writes `stopping`
(existing path), and the bot terminal lands `completed`.

### The two NEW states sit BEFORE `requested` and never flow through the FSM

`idle` and `scheduled` are **pure intent**. The FSM's entry edge is `None → joining`
and its pre-entry is `requested → None` (`machine.py:79,102`). `idle`/`scheduled` live
*before* `requested`; they are **never** passed to `LifecycleSink.apply_change`, never
appear in `_PERSISTED_STATUS_TO_BOTSTATUS`, and `bot_status_from_persisted` returns
`None` for them (its `.get` default — `machine.py:122`), which is the correct "pre-joining"
seed. **The bot FSM is byte-for-byte untouched.**

### Source-of-truth rule (per transition, on the one column)

- **USER (dropdown) writes**, via new meeting-api intent endpoints, **only the intent
  edges**: `idle ↔ scheduled`, and `scheduled/idle → requested` (the "send now" hand-off
  that triggers the existing spawn). The user may also trigger `* → stopping` (existing
  DELETE /bots).
- **BOT FSM writes** everything from `joining` onward (`machine.py` + the lifecycle
  callback). The user **cannot** set `joining/awaiting/active/completed/failed` directly.
- **Invariant:** a status is writable by the user **iff** it is `idle`, `scheduled`, or a
  spawn/stop *trigger*. Once `requested` is written, the row is FSM-owned until terminal.
  This is enforced by *which endpoint* may write *which value* (below), not by a flag.

---

## B. Dropdown = action → transition map (not free status editing)

The dropdown never sets a status string directly. Each item is an **action** that calls
**one** endpoint, which performs the **one** legal write.

| current status | dropdown action | API call | resulting write | who writes |
|---|---|---|---|---|
| `idle` | **Schedule(time)** | `PUT meeting-api /meetings/{id}/intent {state:"scheduled", at}` | `scheduled` (+ `data.scheduled_at`) | user (intent endpoint) |
| `scheduled` | **Send now** | `PUT /meetings/{id}/intent {state:"requested"}` → spawn, OR `POST /api/meeting/bot` | `requested` then FSM | user trigger → meeting-api spawn |
| `scheduled` | **Cancel** | `PUT /meetings/{id}/intent {state:"idle"}` | `idle` | user (intent endpoint) |
| `requested` / `awaiting_admission` / `needs_help` / `active` | **Stop** | `POST /api/meeting/stop` → gateway `DELETE /bots/{platform}/{native}` | `stopping`, then bot terminal `completed` | user-stop path (`stop_router.py:106`), then FSM |
| `completed` / `failed` / `stopped` | **Re-send** | `POST /api/meeting/bot` (gateway `POST /bots`) | new row / reopen → `requested` then FSM | meeting-api spawn |
| `idle` | **Send now** | `POST /api/meeting/bot` | `requested` then FSM | meeting-api spawn |

Notes:

- **Schedule / Cancel** are the only genuinely NEW writes and need a **new meeting-api
  endpoint** (`PUT /meetings/{id}/intent`, or `POST /meetings/intent` to create a parked
  `idle` row for a meeting that has no DB row yet). They set `idle`/`scheduled` and stamp
  `data.scheduled_at`. They MUST reject any target value other than `idle`/`scheduled`
  (and the `requested` send-now trigger), so the dropdown can never bypass the FSM.
- **Send now** from `scheduled` is the **scheduler/user hand-off**: it either (a) calls
  the existing spawn directly, or (b) is what the meeting-api `scheduling/scheduler.py`
  fires when `scheduled_at` arrives (`tick`/`_process`, `scheduler.py:145-185`). Same
  spawn path either way — no duplicate logic.
- **Stop** and **Re-send** reuse the **existing** agent-api endpoints unchanged
  (`api.py:201-254`); only the trigger UI changes.
- `awaiting_admission` is surfaced read-only in the dropdown as "Awaiting admission" with
  Stop available; the user cannot push it forward (FSM-owned).

---

## C. WS transport

### C.1 New channel + frame

A **user-scoped** redis channel **`u:{user_id}:meetings`**, carrying the SAME
`meeting.status` frame plus the two new states, with `native` echoed for client routing:

```json
{ "type": "meeting.status",
  "meeting_id": 42,
  "native": "abc-defg-hij",
  "status": "scheduled",
  "when": "2026-06-25T18:00:00Z" }
```

To stay compatible with the existing per-meeting frame (`§0.2`), the user-channel frame
is **additive**: it keeps the existing `type:"meeting.status"`, and the publisher emits
**both** the legacy nested shape (`meeting:{id,platform,native_id}`, `payload:{status}`,
`user_id`, `ts`) AND the flat fields (`meeting_id`, `native`, `status`, `when`) so old and
new readers both parse it. **ws.v1 change:** extend the `meeting.status` `$def` in
`ws.schema.json` to allow `status ∈ {idle, scheduled, requested, joining,
awaiting_admission, needs_help, active, stopping, completed, failed, stopped}` and the
flat fields; add a golden `MeetingStatus.scheduled.json`. Extra fields are already allowed
(data messages are additive — schema description, `ws.schema.json:5`).

### C.2 Gateway change — auto-subscribe the authed socket to its user scope

The seam is **`run_multiplex`** in `core/gateway/.../app.py`. Today connect only checks
the key is present (`app.py:315-322`). Change:

1. After `api_key` is read (`app.py:316`), call `user = await authorizer.resolve(api_key)`
   (the resolver already exists — `ports.py:39`, used at `app.py:96-99`). If `None` →
   `error:invalid_api_key` + close `4401` (fail-closed, like the proxy at `app.py:120-125`).
2. Derive `user_id = user["user_id"]` and **auto-subscribe** a fan-in on
   `u:{user_id}:meetings` **at connect** (before the receive loop, near `app.py:324`),
   reusing the existing `fan_in([...])` helper (`app.py:327-344`) — it already forwards raw
   payloads verbatim. No client `subscribe` frame needed for the user scope; optionally
   accept `{"action":"subscribe","scope":"user"}` as an explicit opt-in, but connect-time
   is simplest given the resolved identity.
3. Per-meeting subscriptions (`tc:`/`bm:`/`va:`) are **unchanged** — the open-meeting tab
   still subscribes by `{platform,native_id}` as today.

This makes the gateway a thin verbatim forwarder for the user channel exactly as it is for
the meeting channels — no new fan logic, just a connect-time subscribe keyed on the
resolved `user_id`.

### C.3 Publishers

Publish `u:{user_id}:meetings` on **both** transition families:

- **Bot-FSM transitions (meeting-api).** Right where `bm:meeting:{id}:status` is already
  published — `core/meetings/.../app.py:251-272`. Add a second `redis.publish` to
  `u:{user_id}:meetings` with the same/extended frame (`user_id` and `native_id` are both
  in scope there — `app.py:262,265`). Same `no_op` gate (`app.py:251`), same best-effort
  try/except. **This is the one change that makes every existing bot transition land on the
  user channel.**
- **User/scheduler intent changes (meeting-api).** The new `PUT /meetings/{id}/intent`
  endpoint, and the scheduler's send-now fire (`scheduling/scheduler.py`), publish the same
  frame for `idle`/`scheduled`/`requested` so the dropdown's own actions echo back over WS
  (single render path — the UI never optimistically guesses).

### C.4 Interplay with `bm:meeting:{id}:status`

**Keep both.** `bm:meeting:{id}:status` stays for the **open-meeting tab** (already wired,
already golden-tested) — no churn there. The **list surface** moves to the user channel
`u:{user_id}:meetings`, which is strictly a superset (it also carries `idle`/`scheduled`,
which are never per-meeting-subscribed because the list may show a meeting with no open
tab). Do **not** subsume `bm:` into the user channel now; revisit once the list is the only
consumer. Net: one extra publish line in meeting-api, one connect-time subscribe in the
gateway.

### C.5 Replacing the terminal 4s poll

`liveMeetings.ts` keeps `GET /api/meetings` as the **initial snapshot** (one fetch on
mount / reconnect — `poll()` once), but **drops `setInterval(poll, 4000)`**
(`liveMeetings.ts:83`). A new WS client opens `/ws?api_key=…`, receives `meeting.status`
frames, and patches the in-memory `meetings[]` by `native`/`meeting_id`, then
`subs.forEach(f => f())` (the existing `useSyncExternalStore` notify — `liveMeetings.ts:73`).
Status→bucket mapping extends `LIVE_STATUSES` and adds `idle`/`scheduled` buckets for the
dropdown.

---

## D. Implementation plan (ordered; parallel tracks flagged)

**Dependency:** §0-auth. The user channel is keyed on a real `user_id`. Today the only
identity is the hardcoded **`u_live`** (`meeting.tsx:317`) and the gateway resolves
`user_id` from the API key (`adapters.py:54-61`). **Stub:** until auth lands, the gateway's
connect-time `resolve` returns the single test user's `user_id`; the terminal opens `/ws`
with the dev API key. The channel name is `u:{user_id}:meetings` regardless, so nothing
changes when real subjects arrive.

Tracks (the four can run as parallel background branches; only the gateway↔publisher frame
shape must be agreed first — fix it from `§C.1`):

1. **Track G — gateway user-scope** (`core/gateway/.../app.py`): connect-time `resolve`
   + auto-subscribe `u:{user_id}:meetings` (`app.py:316,324,327-344`); extend
   `ws.v1/ws.schema.json` + add golden. *Smallest, unblocks the client.*
2. **Track M — meeting-api status + publish** (`core/meetings/.../app.py`,
   `sessions/models.py`, new intent router, `scheduling/scheduler.py`): add the second
   publish to `u:{user_id}:meetings` (`app.py:251-272`); add `PUT /meetings/{id}/intent`
   (writes `idle`/`scheduled`, stamps `data.scheduled_at`, rejects FSM values); confirm
   `idle`/`scheduled` never reach `LifecycleSink` and never violate the partial unique
   index (they ARE non-terminal → at most one parked intent per native, which is desired).
3. **Track A — agent-api intent + merge** (`core/agent/.../api.py`): forward the new
   intent calls; teach `GET /api/meetings` merge to pass through `idle`/`scheduled`
   (`api.py:256-285`) and `_LiveMeetings` to model the intent rows. (Mostly pass-through.)
4. **Track T — terminal WS client + dropdown** (`clients/terminal/src/surfaces/`): new
   `meetingStatusSocket.ts` (open `/ws`, patch store); drop the 4s interval in
   `liveMeetings.ts:83` (keep one snapshot fetch); replace the Stop/Send buttons in
   `meeting.tsx:256-258` with the action→transition dropdown from §B.

Suggested order: **G + M in parallel first** (transport + producer), then **T**, then
**A** polish. M's intent endpoint and T's dropdown can land behind a flag.

---

## E. Open questions / risks

1. **`awaiting_admission` visibility.** It's a real FSM state (`machine.py:30`) but the
   list currently buckets only `{active,joining,requested}` as live (`liveMeetings.ts:28`,
   `api.py:283`). Surface it as a distinct "Awaiting admission" chip (Stop-only) or fold
   into "live"? Recommend distinct — it's the highest-signal state for the user.
2. **`scheduled → requested` hand-off timing.** Who fires it — the user's "Send now", or
   the meeting-api scheduler at `scheduled_at` (`scheduler.py:145-185`)? Both must converge
   on the *same* spawn path and the *same* publish, with a guard so a manual Send-now plus a
   scheduler fire don't double-spawn (the partial unique index `models.py:82-87` already
   blocks a second non-terminal row → second spawn 409s → benign).
3. **Idempotency.** The bot publish is already `no_op`-gated (`app.py:251`,
   `machine.py:354-362`). The intent endpoint must be idempotent too: `PUT intent` to the
   current state is a 200 no-op and **does not** re-publish (mirror the FSM's discipline),
   else a reconnect storm of identical frames hits every socket.
4. **Reconnect.** WS gives no replay. On (re)connect the terminal does **one** snapshot
   `GET /api/meetings` (the existing `poll()` body, `liveMeetings.ts:62-78`) to seed, THEN
   trusts live frames. Frames that arrive during the snapshot fetch are reconciled by
   `meeting_id`/`native` (last-write-wins on `when`/`ts`).
5. **`stopped` modelling.** Decided: derive `stopped` for display from `completed` +
   `data.stop_requested` (`stop_router.py:107`) rather than adding a DB enum value — keeps
   the FSM and the terminal-set `{completed,failed}` (partial unique index, `models.py:86`)
   untouched. Confirm the terminal's "Re-send" treats both `stopped` and `completed` rows
   identically (it already does — `meeting.tsx:256-258` Send branch).
6. **Per-user fan-out cost.** One `u:{user_id}:meetings` subscription per open socket; a
   user with N tabs gets N subscriptions to the same channel (fine for redis pub/sub, but
   note for connection accounting). The existing per-user request rate limiter
   (`app.py:134`) does not cover WS — out of scope here, flag for WS-rate work.
