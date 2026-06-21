# bot_spawn — `POST /bots`

The bot-spawn flow, ported from the parent `meetings.request_bot` CORE happy path. Builds the bot's
invocation, mints the MeetingToken, spawns the meeting-bot workload over the runtime kernel, and
eager-creates the `MeetingSession` keyed by the bot's `connectionId`.

## Front door
- `build_router(repo, runtime)` — the mountable `POST /bots` router (the unified
  `meeting_api.app.create_app` mounts it).
- `request_bot(...)` — the spawn flow (the router's core; callable directly in tests).
- `build_invocation(...)` / `build_workload_spec(...)` / `mint_meeting_token(...)` — the
  `invocation.v1` / `runtime.v1` builders + the stateless MeetingToken minter. Both builders
  validate against the sealed schema **at the seam** before anything ships.
- `MeetingRepo` / `RuntimeClient` ports + `QuotaExceeded` / `MaxBotsExceeded` / `SpawnFailed` /
  `DuplicateMeeting`.
- `adapters.build_production_router(...)` — wire with real SQLAlchemy + the httpx runtime client.
- `fakes` — `InMemoryMeetingRepo` / `FakeRuntimeClient` (offline drivers).

## The flow (P2 core + P3 control-plane)
construct the meeting URL → dedup (409 on a CONCURRENT active prior) → **max-bots pre-check (429)**
→ **continue_meeting (reuse a TERMINAL prior row)** or insert a fresh `Meeting` row (status
`requested`) → mint the MeetingToken + build the `invocation.v1` invocation → spawn the `runtime.v1`
`WorkloadSpec` (`profile="meeting-bot"`; the invocation rides as the one `BOT_CONFIG` env var) →
eager-create the `MeetingSession` (`session_uid` == `connectionId`) → write the kernel workload id
back as `bot_container_id` → return the `api.v1` `MeetingResponse` (now listing its `sessions`).

### P3c — `continue_meeting` (sequential multi-bot per meeting)
When the prior meeting for `(platform, native_id)` is TERMINAL (`completed`/`failed`), reuse the
SAME meeting row + add a NEW `MeetingSession` instead of the 409. Transcripts + recordings stay keyed
by the (unchanged) meeting row, so a continued run preserves them. A CONCURRENT second bot (prior
still active) is still rejected (409).

> **Contract decision (api.v1 is SEALED — DO NOT edit it).** The `POST /bots` request body
> (`MeetingCreate`) has **no `additionalProperties: false`** — it is an OPEN object — so an extra
> `continue_meeting` field on the wire is NOT rejected by the frozen schema, and the behaviour ships
> now via an internal request param. **FLAG (lane:contract):** the schema does not *declare*
> `continue_meeting`; exposing it as a documented, typed PUBLIC field on `api.v1` needs a `vN+1`
> (a human-reviewed `lane:contract` change). Same for the response: the listed `sessions[]` ride in
> the open `data.sessions` (MeetingResponse `data` is `additionalProperties:true`), not a new typed
> field — a typed `sessions` field is likewise a `vN+1`. `gate:contract-version` stays green (no
> sealed schema touched).

### P3e — max-bots (per-user concurrency)
A pre-check BEFORE the runtime call: count the user's ACTIVE bots (status in
`{requested, joining, awaiting_admission, active}`, **excluding** infra `browser_session` —
parent `meetings.py:1091`) and reject the N+1th with `429` (`MaxBotsExceeded`). The cap arrives as
the gateway's `X-User-Limits` header (resolved upstream from `/internal/validate`, identity.v1).
The runtime kernel's own `owner_quota` → `QuotaExceeded` (→ 429) is the defense-in-depth BACKSTOP.
Join-retry re-spawns and `continue_meeting` sessions count against the same cap.

Tests: `../../../tests/test_bot_spawn.py` · `test_continue_meeting.py` · `test_max_bots.py`.
Join-retry (P3d) lives in the `lifecycle` brick: `lifecycle/retry.py` + `test_join_retry.py`.
