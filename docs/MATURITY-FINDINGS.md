# Maturity campaign — seam findings (0.12)

Autonomous adversarial testing of the system seams (lifecycle · webhooks+backoff · api agility ·
robustness) to raise maturity. Each finding: severity · location · scenario · expected vs actual · fix.
Bugs are fixed at source, deployed via the dev loop (`git pull` on bbb → watchfiles), and L4-verified.

Status legend: 🔴 open · 🟡 fix-ready · 🟢 fixed+verified.

## Findings

### A1 — 🟡 `POST /bots` does not validate `platform` against the sealed `Platform` enum
- **Severity:** P2 (robustness + agility + contract drift). Two manifestations of ONE root cause —
  `platform` is read as a bare `str` (`router.py:99`), never validated against the api.v1 `Platform` enum:
  - **Live (bbb), no `meeting_url`:** `{"platform":"gmeet"}` (typo), `"webex"`, or non-string `123`→`"123"`
    → **500** (the invocation builder can't construct `meetingUrl` → uncaught `jsonschema.ValidationError`
    at `invocation.py:61/66/172`).
  - **Offline (fake spawn):** `{"platform":"discord",...}` → **201** (the fake skips the invocation
    validation, so the bad platform sails through).
- **Where:** `bot_spawn/router.py:99,109` → `service.py:195` → `invocation.py`.
- **Expected:** `422` "unsupported platform" for any non-enum platform. **Actual:** 500 (live) / 201 (fake).
- **Fix:** validate `platform ∈ Platform` (or parse the body through the sealed `MeetingCreate` model) in
  `create_bot` before the spawn flow → `422`; belt-and-braces: catch `ValidationError` in `build_invocation`
  → `422`. (Found live + by the api-agility agent: `test_post_bots_invalid_platform_should_be_422`.)

### A2 — 🟡 `POST /bots` silently coerces `recording_enabled:"false"` → `True` (opposite intent)
- **Severity:** MEDIUM (silent data corruption — a caller disabling recording gets it ENABLED).
- **Where:** `bot_spawn/router.py:120` + `_resolve_recording_enabled` (`bool("false") == True`).
- **Expected:** honor the JSON boolean, or `422` on a non-bool. **Actual:** any non-empty string → `True`.
- **Fix:** validate the JSON type of `recording_enabled` (bool|null); don't `bool()`-coerce arbitrary input.

### A3 — 🟡 `DELETE /bots/{platform}/{native}` invalid platform → 404 (should be 422)
- **Severity:** LOW (contract drift). `stop_router.py:64` reads the path platform as a bare `str`; the
  sealed path param is the `Platform` enum → should `422`. **Fix:** type the path param as `Platform`.

### A4 — 🟡 Missing `ADMIN_TOKEN` → every `POST /bots` 500s (no fail-fast)
- **Severity:** MEDIUM (operational; a deploy that forgets `ADMIN_TOKEN` turns every spawn into a 500).
- **Where:** a valid spawn raises `ValueError` mid-flow at `invocation.py:96`.
- **Expected:** fail-fast at startup (refuse to boot / clear config error). **Actual:** unhandled 500 per request.
- **Fix:** validate required config (`ADMIN_TOKEN`) at app startup; surface a clear config error.

### Contract-vs-implementation surface gaps (api-agility agent)
The sealed api.v1 declares `GET /bots/id/{meeting_id}`, `GET /recordings/{recording_id}`,
`DELETE /recordings/{recording_id}` — **none implemented at meeting-api** (the dashboard uses
`GET /meetings/{id}` instead; recordings expose only `/recordings`, `/recordings/{id}/master`,
`/recordings/{id}/media/{mfid}/raw`). Flag for a contract-vs-impl reconciliation pass (lane:contract).

## Seam test suites added (standing regression)

### L1 — 🟡 No-op terminal redelivery double-counts the in-process `status_change` envelope log
- **Severity:** low-medium (observability/consistency; end-user delivery is still exactly-once).
- **Where:** `app.py:199-200` (the lifecycle callback).
- **Scenario:** the bot retries a terminal (`completed`) up to 3×; each redelivery is a `change.no_op`.
- **Expected:** an idempotent replay appends 0 envelopes to `app.state.status_change_webhooks`.
  **Actual:** it appends 1 (the envelope build+append runs unconditionally, BEFORE the `no_op` guard
  that correctly suppresses the real `webhook_sink.deliver` + the ws publish).
- **Fix:** gate the `build_status_change_envelope` + append on `not change.no_op`, mirroring the persist
  (L208) + ws-publish (L242) guards. (Agent-found; left as `xfail(strict=True)` in `test_lifecycle_seam.py`.)

### R1 — 🟡 Recordings media route ignores HTTP Range (returns 200 + full file)
- **Severity:** P3 (efficiency/UX; seeking works but downloads the whole file).
- **Where:** the recordings raw byte route (`recordings/router.py` / `service.py`) → served via gateway.
- **Scenario (live, bbb):** `GET /recordings/{id}/media/{mid}/raw` with `Range: bytes=0-1023` (and
  `bytes=10000-20000`) → `200 OK` + the **entire** 15.27 MB body; no `Content-Range`, no `Accept-Ranges`.
- **Expected:** `206 Partial Content` + `Content-Range` + `Accept-Ranges: bytes` + only the requested slice
  (the dashboard proxy already forwards Range/Content-Range — the SOURCE just doesn't honor it).
- **Fix:** parse the `Range` header in the raw route; return `206` with the byte slice + `Content-Range` +
  `Accept-Ranges: bytes`; `416` on an unsatisfiable range. Enables instant seek + resumable downloads.

### WH1 — 🟡 No dead-letter queue: exhausted/expired webhooks silently dropped
- **Severity:** MEDIUM (delivery visibility; a `meeting.completed` can be abandoned with no trace).
- **Where:** `webhooks/retry.py:131-138` (drain `continue` on exhaustion/age-expiry).
- **Scenario:** receiver down past the schedule (`[60,300,1800,7200]`, max age 24h) → the envelope vanishes
  — no DLQ, no `log_event`, no operator visibility.
- **Fix:** push exhausted/expired entries to a `webhook:dead_letter` redis list and/or
  `log_event("webhook_dead_lettered", ...)`. (xfail `test_dead_letter_on_permanent_failure`.)

### WH2 — 🟡 SSRF guard is TOCTOU-vulnerable (DNS rebinding bypass)
- **Severity:** LOW-MEDIUM (security).
- **Where:** `webhooks/ssrf.py:116` + `__main__.py:84,148`.
- **Scenario:** `validate_webhook_url` returns the original hostname; httpx **re-resolves DNS at connect**,
  independently of the guard → an attacker flipping the A-record between validate and connect reaches an
  internal IP.
- **Fix:** resolve + pin the IP at validation and dial that exact IP (httpx resolution hook), re-validating
  at connect. (xfail `test_ssrf_toctou_connect_time_pinning_present`.)

### WH3 — 🟡 First backoff (60s) applied twice → one extra retry
- **Severity:** LOW (timing).
- **Where:** `webhooks/retry.py:53` (`enqueue` sets `next_retry_at = now + schedule[0]`) + `:136`
  (attempt-0 drain uses `backoff_idx = min(0,3) = 0` = 60s again).
- **Scenario:** effective wait sequence is `60, 60, 300, 1800, 7200` (not `60,300,1800,7200`); total bounded
  HTTP attempts = 6 (1 sync + 5 drain) rather than the intended 5.
- **Fix:** index the drain backoff by `attempt+1` (or align `enqueue` so the schedule isn't double-counted).

## Seam test suites added (standing regression)
- `tests/test_lifecycle_seam.py` — 60 passed / 2 skip / 1 xfail (L1). Exhaustive illegal-edge matrix,
  both-terminal idempotency, rehydration for all 8 statuses, malformed callbacks, stop-reconcile races.

## Live public-surface probe (bbb gateway) — MATURE baseline
Proper `401` (missing/invalid key) · `404` (unknown meeting/transcript/recording) · `405` · **bounded+typed
pagination** (`limit=-1/999999/abc`, `offset=-5` → `422`) · clean `422` (missing fields / malformed JSON /
empty body) · idempotent `DELETE` (404 on nonexistent). Only gap = A1.

## Robustness findings (ROB) + priority

### ROB1 — 🟡 max-bots cap TOCTOU → concurrent over-provision (HIGH)
`bot_spawn/service.py:142-176` — `count_active_bots()` THEN `create_meeting()`, non-atomic. N concurrent
`POST /bots` (N>cap) all pass the pre-check before any insert → all N provisioned (cap=2, 5 concurrent →
**5 workloads**). Fix: atomic enforcement (conditional INSERT `WHERE active<cap` / advisory lock / unique
partial index).

### ROB2 — 🟡 dedup TOCTOU → double-spawn (HIGH)
`service.py:124-176` — `find_active()` THEN `create_meeting()`. Two concurrent identical requests → **two
bots for one meeting**. Fix: unique partial index on active `(user_id,platform,native_id)` or per-key lock.

### ROB3 — 🟡 partial-spawn orphan (MEDIUM)
`service.py:227-248` — `create_workload()` ok, then a post-spawn DB write fails → workload not torn down,
no session row, meeting stuck `requested`. Fix: compensating teardown on post-spawn DB failure.

### ROB4 — 🟡 collector `:mutable` publish not fault-isolated (MEDIUM)
`collector/ingest.py:129` — `redis.publish()` un-wrapped; a blip aborts the batch before its ack. Fix:
try/except + log, still return the persisted count (match the lifecycle path).

### Synthesis status (live progress)
- 🟢 **A1** platform→422 — fixed (router) + **L4-verified live on bbb** (gmeet/webex/non-string → 422).
- 🟢 **A2** recording_enabled type-validate — fixed + **L4-verified** (bad type → 422).
- 🟢 **L1** no-op envelope dedup — fixed (app.py) + offline-green.
- 🟢 **ROB4** collector publish fault-isolation — fixed (ingest.py) + offline-green.
- 🟢 **R1** recordings HTTP Range — fixed + **L4-verified live** (bytes=0-1023 → 206 + 1024 bytes; mid-file 206; no-range 200).
- 🟢 **WH3** double-backoff — fixed (retry.py: drain indexes by `attempt+1`); effective schedule now
  `[60,300,1800,7200]`, total bounded attempts = 5 (was 6). Offline-green.
- 🟢 **WH1** dead-letter queue — fixed: exhausted/age-expired envelopes pushed to redis `webhook:dead_letter`
  (LTRIM-capped 1000) + `webhook_dead_lettered` log. Offline-green.
- 🟢 **ROB1+ROB2** TOCTOU — fixed: `create_meeting_guarded` does dedup+cap+insert in ONE txn behind a
  cluster-wide `pg_advisory_xact_lock(user_id)` (serializes concurrent same-user spawns) + a unique
  partial-index backstop (`IntegrityError`→`DuplicateMeeting`). Reviewed: advisory lock IS the
  cross-process mechanism (sound). Offline-green (SlowRepo+gather). L4 (live PG) verify next.
- 🟢 **ROB3** partial-spawn orphan — fixed: post-spawn DB writes wrapped → on failure `runtime.delete_workload`
  teardown + re-raise `SpawnFailed` (502). `delete_workload` added to the runtime port/adapters. Offline-green.
- 🟢 **A4** ADMIN_TOKEN fail-fast — `build_production_app` validates required env at boot (clear RuntimeError),
  not a 500/spawn. (ADMIN_TOKEN confirmed set on bbb → safe reload.) Offline-green.
- ⚠️ **Index backstop deploy note:** the unique partial index is in the meeting-api model MIRROR; the DDL
  SSOT is admin-api `ensure_schema` (create_all only makes it on a FRESH DB). bbb's existing `meetings`
  table relies on the advisory lock (correct); the index must be added to admin-api models + manually
  `CREATE`d on existing DBs for the multi-replica backstop. Follow-up (advisory lock suffices for correctness).
- ⏸️ **WH2** SSRF-TOCTOU (DNS rebinding) — deferred (needs IP-pin + httpx resolution hook); xfail kept.
- ⏸️ **A3** DELETE invalid platform → 404 vs 422 — left as documented drift (idempotent-delete semantics OK).

Batch 1 (A1/A2/L1/ROB4) committed `657036b` + deployed to bbb via the dev loop (watchfiles reload).

**Already-mature (confirmed):** lifecycle publish-failure → still 200 + DB advances; segment durability
under publish failure; all 4 background loops survive a throwing tick; sequential cap+dedup correct; auth
fail-closed; owner-scoped 404 (no leak); bounded/typed pagination; idempotent DELETE.

## Seam suites added (standing regression, ~190 cases)
`test_lifecycle_seam.py` (60p/2s/1xf) · `test_webhook_seam.py` (59p/2xf) · `test_api_agility.py` (58p/3xf)
· `test_robustness_seam.py` (11p/4xf). Each xfail is `strict=True` → flips to a pass when its bug is fixed.
