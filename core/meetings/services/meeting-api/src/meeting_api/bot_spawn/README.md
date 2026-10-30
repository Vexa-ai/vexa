# bot_spawn — `POST /bots`

The bot-spawn flow, ported from the parent `meetings.request_bot` CORE happy path. Builds the bot's
invocation, mints the MeetingToken, spawns the meeting-bot workload over the runtime kernel, and
eager-creates the `MeetingSession` keyed by the bot's `connectionId`.

## Front door
- `build_router(repo, runtime)` — the mountable `POST /bots` router (the unified
  `meeting_api.app.create_app` mounts it).
- `request_bot(...)` — the spawn flow (the router's core; callable directly in tests).
- `transcription_language` — transcription-language.v1's reading: checks each tier, resolves meeting >
  person (bot-context) > deployment (env) > auto, and builds the acts.v1 `reconfigure` for a live change.
- `build_invocation(...)` / `build_workload_spec(...)` / `mint_meeting_token(...)` — the
  `invocation.v1` / `runtime.v1` builders + the session-bound MeetingToken minter (re-exported from
  `meeting_api.meeting_token`). Both builders validate against the sealed schema **at the seam**
  before anything ships.
- `MeetingRepo` / `RuntimeClient` ports + `QuotaExceeded` / `MaxBotsExceeded` / `SpawnFailed` /
  `DuplicateMeeting`.
- `meeting_host` — the meeting host rule for a caller-supplied `meeting_url`: the host is the
  platform's (or, for Jitsi and Zoom front doors, the organisation's own) and every address it
  resolves to is public or listed in `VEXA_MEETING_HOST_ALLOW` (`MeetingHostRefused` → 422; the
  auto-join stamps it). The operator's entries ride to the bot as invocation.v1 `meetingHostAllow`.
- `adapters.build_production_router(...)` — wire with real SQLAlchemy + the httpx runtime client.
- `fakes` — `InMemoryMeetingRepo` / `FakeRuntimeClient` (offline drivers).

## The flow (P2 core + P3 control-plane)
check a caller-supplied meeting URL's host (422 off the platform or resolving inside) → construct
the meeting URL → dedup (409 on a CONCURRENT active prior) → **max-bots pre-check (429)**
→ mint a per-run service identity → **optional service-authority admission before any row/runtime
effect (403 deny, 503 unavailable)**
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

### Bot accounts (ADR-0049)

`POST /bots` takes an optional `bot_account`: a bot account id, `"default"` (also when absent) or
`"none"` (the open api.v1 body, like `continue_meeting`). The router resolves it before any DB write
through [`bot_accounts`](../bot_accounts/README.md) (`BotAccounts.resolve_for_spawn`): `default` is
the caller's most recently verified `ok` account for the platform (google_meet→google,
teams→microsoft, zoom→zoom), else a company account the `VEXA_COMPANY_BOT_USE` policy opens to them,
else none — today's bot, unchanged, and a store that cannot answer is also none. An explicit id must
exist, be the caller's or a usable company one (404 / 403), match the platform (422) and be signed in
(409, naming the remedy `browser_session_open`).

With an account the spawn is authenticated without the deployment knob: `auth_userdata_path` =
`bot-account:<id>` (so `find_active_by_userdata` → `AuthSessionBusy` serializes one live bot per
account, and `latest_auth_session` binds the restore and the write-back to it),
`meeting.data.bot_account_id` = the id, and the invocation carries `sessionRestoreUrl` and
`sessionWritebackUrl` (both meeting-api's `/internal/browser-session/<session_uid>`) and no userdata
path or S3 key. Without one, `BOT_AUTHENTICATED` works exactly as before. The auto-join sweep spawns
without an account.

### Optional external service authority

`VEXA_SERVICE_AUTHORITY_CONFIG` enables the sealed, policy-free
`service-authority.v1` seam. The request contains authoritative user/service identity, service
mode, frozen transcription provider, concurrency, and lifecycle timing—never an email, payment
provider ID, price, balance, transcription URL, or credential. The exact JSON bytes are signed
with `VEXA_SERVICE_AUTHORITY_SECRET`.

The admitted decision is frozen in `meeting.data.service_authority`. A meeting-api-owned sweep
asks again at every admitted-time + N-minute boundary. An enforced stop is persisted before the
runtime teardown and converges after restart; repeated sweeps never apply the same decision twice.
Observe-only sessions are never later reinterpreted as enforced sessions.

`failure_policy=closed` applies to **admission only**: with the authority unavailable `POST /bots`
answers 503 `service_authority_unavailable`. An **admitted** session fails open: an unavailable
answer at a minute boundary records no decision — `last_boundary_at` stays put — and books the
minute in `service_authority.debt_boundaries` (`record_service_authority_debt`, with the
`request_id` and `active_concurrency` of that minute), logs
`service_authority_fail_open meeting_id=… boundary=… reason=unavailable` once per minute, and
leaves the bot running. After the first unavailable answer a sweep makes no further authority
call that tick. When the authority answers again the sweep replays every debt minute in order
with the same `request_id` and stored concurrency; each real decision settles its entry exactly
once, and the row carries `service_authority.debt_open` and `debt_settled_count`
(`debt_boundaries` is `[]` once settled). An enforced deny on a replayed minute still stops the
bot. Debt left on a meeting stopped by that deny, or one that ended during the outage, is replayed
on later sweeps through `list_service_authority_debt_sessions`; a deny there settles the minute
without a stop. See [`../service_authority/README.md`](../service_authority/README.md).

No config means explicit stock OSS allow-all. Once configured, malformed, stale, or cross-bound
decisions are refused, and an unavailable authority fails closed at admission only (above). `mode=observe` records the authority response but cannot satisfy
a hosted hard-spend-cap claim.

## Auto-join block lists
`auto_join_block.py` — domains and addresses the auto-join sweep never joins: the operator's
`VEXA_AUTO_JOIN_BLOCK` (parsed at boot) and the owner's `auto_join_block` from bot-context. A due row
whose organiser, an invitee or the link's host matches is stamped (`data.auto_join_error`, counter
`blocked`) and never spawned. `POST /bots` is not affected. Docs:
[Calendar sync → Enterprise settings](../../../../../../../docs/docs/how-to/calendar-sync.mdx).
