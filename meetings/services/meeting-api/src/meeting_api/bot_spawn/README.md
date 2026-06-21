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
- `MeetingRepo` / `RuntimeClient` ports + `QuotaExceeded` / `SpawnFailed` / `DuplicateMeeting`.
- `adapters.build_production_router(...)` — wire with real SQLAlchemy + the httpx runtime client.
- `fakes` — `InMemoryMeetingRepo` / `FakeRuntimeClient` (offline drivers).

## The flow (core only)
construct the meeting URL → dedup (409) → insert the `Meeting` row (status `requested`) → mint the
MeetingToken + build the `invocation.v1` invocation → spawn the `runtime.v1` `WorkloadSpec`
(`profile="meeting-bot"`; the invocation rides as the one `BOT_CONFIG` env var) → eager-create the
`MeetingSession` (`session_uid` == `connectionId`) → write the kernel workload id back as
`bot_container_id` → return the `api.v1` `MeetingResponse`.

## P3 seams (NOT built here)
`continue_meeting` (reuse a stopping meeting), max-bots / concurrency pre-check (the kernel still
surfaces its own quota → 429), and join-retry / bot-timeout scheduling. The seams are marked in
`service.request_bot`.

Tests: `../../../tests/test_bot_spawn.py`.
