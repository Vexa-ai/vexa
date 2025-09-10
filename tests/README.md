## Vexa Test Kit — Meeting Bot E2E (minimal)

This kit provides text-only, minimal tests for the meeting bot lifecycle using REST and WebSocket.

- Base URLs: derived from `.env`
  - API base: `http://localhost:${API_GATEWAY_HOST_PORT}`
  - WS base: `ws://localhost:${API_GATEWAY_HOST_PORT}/ws`
  - Default `API_GATEWAY_HOST_PORT=18056` (see `env-example.*`). No need to pass base URLs via CLI.

- Test files: multiple scenario-focused scripts (CLI params may vary per file)
  - `e2e_ws_meeting_test.py` — main flow + core negatives (present)
  - Additional files below (PRD) — to cover joining failures, end-of-life reasons, concurrency, validation, and webhooks.


## Flows Under Test (from diagram → text)

### Main Flow
- post bot requested → joining → attended → active → end
- Observable via `meeting.status` events and transcript delivery. Current key statuses:
  - `requested` (bot accepted/queued)
  - `active` (bot joined meeting)
  - `stopping` → `completed | failed` (on shutdown)

### Validation (must happen before forwarding)
- Every REST call and WS connection MUST be validated for input/auth before passing further.
  - REST: `X-API-Key` required; invalid/missing → 401/403. Invalid `platform/native_meeting_id` → 422.
  - WS: `?api_key=` (or header) required; invalid/missing → handshake reject or error frame/close. Invalid messages (malformed/unknown/missing fields) → error frame.
- Pydantic validation:
  - Incoming REST bodies use shared schemas (e.g., `MeetingCreate`, `Platform`).
  - Meeting statuses MUST be validated against an allowed set: `requested`, `active`, `stopping`, `completed`, `failed`.
  - WS event payloads that we rely on in tests (e.g., `meeting.status`, `transcript.mutable`) MUST conform to minimal Pydantic models in the tests.

### Single Source of Truth (meeting.status)
- `meeting.status` in the DB is the single source of truth.
- Control priority:
  1. User control via API: POST/DELETE define intent. If user stops, status MUST become/stay stopped terminally, even if a container lingers; the system should remove the container and ensure the bot leaves the meeting.
  2. Bot self callbacks: startup/exit callbacks confirm transitions (`active`, `completed|failed`) and enrich with details.
- Any other signals (container watcher, etc.) MUST NOT override DB truth; they can trigger cleanup only.

### Joining (and failures)
- If joining fails, status is updated with a reason. Reasons:
  - rejected (0)
  - not attended (0)
  - timeout (0) - bot waits for admission but times out and leaves
  - errors (1)
- Semantics: failures surface as `meeting.status=failed` with details recorded; failed if `exit_code != 0`, otherwise normal completion. Detailed info is logged into meeting `data`.
- **CRITICAL**: When bot leaves meeting (rejected, timeout, or error), database status MUST be updated immediately to reflect the actual state.

### On meeting
- Transcript available via collector API for authorized user.
- WS delivers `meeting.status`, `transcript.mutable` (and possibly `transcript.finalized`).

### End of life — reasons and graceful handling
- Reasons: stop bot, left alone, evicted; and while joining: rejected, not attended, timeout, errored.
- MUST be gracefully managed in this priority order:
  1. status update in the DB (most important) - **CRITICAL VALIDATION**
  2. container exited (second important)
  3. bot left the meeting
  4. post-meeting routines done
- On every bot status change, a webhook is called if configured.
- **Database Status Validation**: Every test MUST validate that the database status accurately reflects the bot's actual state, especially when bots leave meetings unexpectedly.


## Minimal Test Expectations

- Negative (auth/input)
  - REST invalid API key → rejected (401/403)
  - REST invalid `platform/native_meeting_id` → rejected (422)
  - WS invalid API key → handshake error OR error frame OR close
  - WS invalid payloads (malformed/unknown/missing fields) → error frame
  - WS unauthorized meeting subscription → error OR not subscribed and no events

- Positive (lifecycle)
  - POST /bots → `requested`
  - WS subscribe → receive `meeting.status=active` within join timeout
  - WS → receive ≥3 `transcript.mutable` segments with fields: `start`, `end_time`, `text`
  - DELETE /bots → `stopping` → terminal `completed | failed` within stop timeout
  - End-of-life reasons (stop/alone/evicted or joining failures) satisfy graceful handling order above and trigger webhook if configured.
- **Database Status Validation**
  - Every test MUST verify final database status matches expected bot behavior
  - Bot timeout scenarios MUST validate status transitions: `requested` → `failed` with timeout reason
  - Joining failures MUST validate status updates with proper error details in meeting `data`
  - Container cleanup MUST not leave orphaned meetings in `requested` or `stopping` states


## PRD — Test Set Requirements

- Purpose: Provide minimal scripts to assert lifecycle, validation, joining failures, end-of-life behavior, webhooks, and schema correctness.
- Inputs: API key, meeting URL; base URLs taken from `.env`.
- For concurrency tests, provide 3 meeting URLs (e.g., `--meeting-urls url1 url2 url3`).
- **CRITICAL**: Meeting IDs are ALWAYS user-provided via meeting URLs. Tests MUST NEVER generate or make up meeting IDs.
- Outputs: human-readable logs and exit codes per scenario.


## Coding principles and service responsibilities
- concise: keep code and tests minimal and readable.
- separation of concerns: services, files, and classes do one job well.
- fail-fast: no fallbacks/workarounds; validate early and surface root causes.

Service responsibilities:
- api-gateway: routing only; auth + input validation; bind REST and WS; WS multiplex fan-in; no business logic/DB.
- bot-manager: bot lifecycle; DB meeting.status is the source of truth; enforce duplicates/concurrency; launch/stop; publish status; handle startup/exit callbacks; schedule post-meeting routines (webhook).
- vexa-bot: attend meeting; connect and stubbornly reconnect to a healthy transcription server; stream audio frames and speaker activations; obey config updates; graceful leave.
- transcription-collector: transcript post-processing; segments (mutable/finalized); speaker mapping; publish WS events; expose transcript retrieval.

## Implementation References (verified)
- REST proxy: `services/api-gateway/main.py` → `POST /bots`, `DELETE /bots/{platform}/{native_meeting_id}`
- Bot Manager: `services/bot-manager/app/main.py` (create/stop/status publish, startup/exit callbacks, post-meeting tasks)
- Concurrency: `services/bot-manager/app/orchestrators/common.py`
- WebSocket multiplex: `services/api-gateway/main.py:/ws` (types: `meeting.status`, `transcript.mutable`, `transcript.finalized`, `error`)
- Transcript retrieval: `services/transcription-collector/api/endpoints.py`
- Webhook task: `services/bot-manager/app/tasks/bot_exit_tasks/send_webhook.py` (called via post-meeting routines)
- Schemas/validation: `libs/shared-models/shared_models/schemas.py`

## Single-file suite plan (scenarios 1–7)

We will implement one runnable suite that takes meeting URL and token, and executes all launches in a single run. Base URLs derive from `.env`.

Design:
- Class `MeetingBotTestSuite` with helpers: `start_bot`, `stop_bot`, `ws_connect`, `ws_subscribe`, `wait_for_status`, `wait_for_mutable_segments`, `get_transcript`, `update_bot_config`.
- Minimal Pydantic models inside the file validate:
  - Allowed meeting statuses: `requested`, `active`, `stopping`, `completed`, `failed`.
  - WS events used by tests: `meeting.status` (platform/native_id/status), `transcript.mutable` (segments with `start`, `end_time`, `text`).
- CLI: `--meeting-url <url>` for single-flow scenarios; `--meeting-urls <u1> <u2> <u3>` used for concurrency subtest.
- **Meeting ID Source**: All meeting IDs MUST be extracted from user-provided meeting URLs via `parse_meeting_url()` function. Never hardcode or generate meeting IDs.
- Aggregated run: each launch logs PASS/FAIL; non-zero exit if any fails.

Launches:
1) Validation (negative)
   - Validate token and input: REST auth failures (401/403), invalid platform/native_id (422), WS invalid key and malformed payloads, unauthorized subscription.

2) Stop-before-join
   - Ask user not to allow the bot to join; call stop immediately.
   - Validate on stop: (1) DB status updated immediately (`stopping` then terminal), (2) container exits, (3) bot left meeting, (4) post-meeting routines done, (webhook fired if configured).
   - **CRITICAL**: Test immediate new bot creation after stop request - system must allow new bot instantly without waiting for graceful shutdown.

3) Joining rejected
   - Ask user to reject the bot at join.
   - Expect `meeting.status=completed` with rejection reason in data; details recorded.

4) Not attended (timeout)
   - Ask user not to attend; wait for 5-minute timeout period.
   - Expect `meeting.status=completed` with `reason=admission_failed` in data; details recorded.

5) On-meeting path
   - Subscribe WS, launch bot, ask user to attend.
   - Validate: `active` within timeout; receive ≥3 `transcript.mutable` segments; GET transcript works.
   - Bot update (e.g., language) via `PUT /bots/{platform}/{native_id}/config` → 202; confirm new transcripts align; WS continues to function.

6) Alone end
   - Attend bot, then leave it alone.
   - Expect graceful end; validate stop criteria (DB status → container → left meeting → post-meeting routines; webhook if configured).

7) Evicted end
   - Attend bot, then evict.
   - Expect graceful end with the same validations as above.

Concurrency subtest (requires 3 meeting URLs):
- Start meeting A → success.
- Immediately request another bot for meeting A → must fail (duplicate).
- Start meeting B (max concurrency assumed 2) → success.
- Request meeting C while A and B active → must fail (limit reached).
- Stop B → request meeting C again → success.

Guards and invariants:
- Meeting deletion during run: not allowed; the suite asserts this remains blocked; if deletion occurs, fail and mark as service bug.
- Single source of truth: DB `meeting.status` prevails; user stop overrides other signals; callbacks confirm, others trigger cleanup only.
- Every API call and WS connect/message is validated before forwarding.
- Every stop validation checks: immediate status change, container death, bot left (user), and post-meeting routines completion within bounded timeouts.

File:
- `vexa/tests/meeting_bot_suite.py` (single file; self-contained Pydantic models and helpers).
