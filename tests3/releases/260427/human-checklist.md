# Human validation checklist — v0.10.5 (260427) — Production hardening

## Mode URLs

| mode | dashboard | API gateway | API docs | SSH |
|------|-----------|-------------|----------|-----|
| **Lite** | http://172.234.213.221:3000 | http://172.234.213.221:8056 | http://172.234.213.221:8056/docs | `make -C tests3 vm-ssh` (with STATE=tests3/.state-lite) |
| **Compose** | http://172.234.26.151:3001 | http://172.234.26.151:8056 | http://172.234.26.151:8056/docs | `make -C tests3 vm-ssh` (with STATE=tests3/.state-compose) |
| **Helm** | ❌ NOT VALIDATED — LKE provision blocked on Linode operations issue (us-ord LKE nodes failing to boot, [PLATFORM] investigating) | — | — | — |

> Compose dashboard is on port **3001** (not 3000) per VM lite/compose port split.

---

## Login

For both lite and compose, the dashboard requires a magic-link login flow:

1. Open the dashboard URL in a browser
2. Enter `test@vexa.ai` (or any valid email seeded by admin-api)
3. Magic-link form posts to `/api/auth/send-magic-link`
4. Check the dashboard logs for the `magic_link_token`:
   - **Lite**: `ssh root@172.234.213.221 'docker logs vexa-dashboard --tail 50 | grep magic'`
   - **Compose**: `ssh root@172.234.26.151 'docker logs vexa-dashboard-1 --tail 50 | grep magic'`
5. Open `http://<host>:<port>/api/auth/verify?token=<token>`
6. Should redirect to `/meetings` — NOT to `/agent` (which is disabled per Pack security-hygiene)

**Expected**: `vexa-token` cookie set, `/meetings` page loads.

---

## ✅ Pack-by-pack validation TODO

Tick each as you confirm. Each pack maps to a dashboard surface or
API behavior the human eye should verify.

### Pack J — Bot exit classification refinement (#255)

> Classifier routes `STOPPED_BEFORE_ADMISSION`, `STOPPED_WITH_NO_AUDIO`,
> `LEFT_ALONE`, normal `STOPPED` distinctly to COMPLETED vs FAILED.

- [ ] **J.1 — Self-initiated leave is COMPLETED, not FAILED**
  - Spawn a bot via dashboard "Add bot" button
  - Wait for bot to reach `active` status on the meeting detail page
  - Click "Stop" on the bot
  - Refresh; meeting shows status = **completed** (green) not failed (red)
  - Compose: also check `select status, data->>'completion_reason' from meetings order by id desc limit 1;` returns `completed | self_initiated_leave`

- [ ] **J.2 — Quick-stop before admission is FAILED + STOPPED_BEFORE_ADMISSION**
  - Spawn a bot for a Meet/Zoom URL where admission won't happen (locked meeting, or hit Stop within 5s of create)
  - Meeting should end with status = **failed**
  - Inspect `meetings.data.completion_reason` = `stopped_before_admission`
  - This is a NEW completion_reason in v0.10.5; prior cycles routed this to generic `failed`

### Pack R — failure_stage correctness (#276)

> failure_stage tracks the lifecycle stage where failure occurred,
> not a transitional/terminal status.

- [ ] **R.1 — failure_stage is a lifecycle stage, never 'stopping'/'completed'**
  - Browse meetings list in dashboard
  - For any failed meeting, the meeting detail page should show failure_stage as one of:
    `requested | joining | awaiting_admission | active`
  - Should NEVER show `stopping`, `completed`, `failed`, `left_alone` as failure_stage
  - **Bug pre-iter-6**: legacy DB records may show invalid values; the iter-6 schema validator strips them on read (reads return `failure_stage: null` + warns to logs)

- [ ] **R.2 — /meetings list endpoint returns 200 even with bad legacy data**
  - From terminal: `curl -H "X-API-Key: <key>" http://<host>:<port>/meetings | jq '.meetings | length'`
  - Returns the list; no 500 errors even if some rows have legacy invalid failure_stage
  - Check meeting-api logs for `MeetingResponse: stripping invalid failure_stage` warnings (if any)

### Pack E.1.a — Per-chunk durable media_files write (#268)

> Every chunk upload updates media_files; bot dying mid-meeting
> leaves a partial recording, not an empty media_files array.

- [ ] **E.1.a.1 — In-progress recording shows media_files entry mid-meeting**
  - Spawn a bot, wait until it's recording (`active` status)
  - Wait ~30 seconds (≥1 chunk uploaded)
  - In dashboard meeting detail, recordings panel shows recording with status = **in_progress** + media_files entry
  - Pre-iter-5, media_files would be empty until is_final=true; now should populate from chunk_seq=0

- [ ] **E.1.a.2 — Single entry per media_type (Option α canonical shape)**
  - For a finished meeting with a recording, inspect `meetings.data.recordings[0].media_files`
  - Should have 1 entry (audio only) or 2 entries (audio + video) — never N entries per type
  - Compose query: `select data->'recordings'->0->'media_files' from meetings where status='completed' order by id desc limit 1;`

- [ ] **E.1.a.3 — Concurrent-chunk race fix (defense-in-depth)**
  - Hard to verify by eye; covered by automated test
    `services/meeting-api/tests/test_recordings_concurrent_chunks.py`
  - Confirm test passes locally: `cd services/meeting-api && pytest tests/test_recordings_concurrent_chunks.py -v`

### Pack D.1 + D.2 — Bot lifecycle on K8s (orphan-pod prevention) (#261, #266)

> container_id is pod NAME (kubectl-resolvable);
> DELETE container-stop is durable via Redis Stream outbox.

- [ ] **D.1 — container_id resolves with kubectl** (helm only — N/A this release)
  - Skipped: helm cluster not validated this iter

- [ ] **D.2 — DELETE survives runtime-api restart**
  - Compose only: spawn bot, wait active
  - SSH to compose VM: `docker stop vexa-runtime-api-1`
  - Click Stop on dashboard for the bot
  - `docker start vexa-runtime-api-1`
  - Within ~30s, bot pod should be terminated (compose: `docker ps | grep vexa-bot` → empty)
  - Pre-iter-5, runtime-api outage would silently drop the stop → bot ran forever

### Pack G.1 — Bot logs structured JSON (#272#6)

> Bot logs are single-line JSON with auto-injected meeting_id/session_uid.

- [ ] **G.1.1 — Bot stdout is JSON, not freeform**
  - SSH to lite/compose VM
  - `docker logs <bot-container-id> --tail 20 2>&1 | head -5`
  - Each line is a JSON object: `{"level":"info","msg":"...","meeting_id":"...","session_uid":"..."}`
  - Pre-G.1, lines were plain strings without context

### Pack L — Slim meetings list endpoint

> /meetings response by default omits the fat `data` JSONB blob; only
> a `data_summary` projection. Full data via `?include=data`.

- [ ] **L.1 — Meetings list payload size bounded**
  - From terminal:
    ```
    curl -H "X-API-Key: <key>" http://<host>:<port>/meetings -o /tmp/m1.json
    curl -H "X-API-Key: <key>" "http://<host>:<port>/meetings?include=data" -o /tmp/m2.json
    wc -c /tmp/m1.json /tmp/m2.json
    ```
  - `m1.json` should be ~10-50× smaller than `m2.json` for any real-world meeting count
  - Pre-Pack-L, every list call returned full data → MB-level payloads

### Pack S — Webhook retry worker error logging

> Webhook errors include exception type + repr, not just str().

- [ ] **S.1 — Webhook delivery error logs are diagnostic**
  - Configure a meeting with a webhook_url pointing at a 500-returning endpoint (e.g. `https://httpbin.org/status/500`)
  - Trigger meeting completion
  - Check meeting-api logs for webhook delivery failure:
    `docker logs vexa-meeting-api-1 2>&1 | grep -A2 "webhook delivery failed"`
  - Log line should include exception type (e.g., `httpx.HTTPStatusError`) AND repr (full URL/headers context), not just `str(e)`

### Pack T — Idempotent terminal status re-fire

> Setting completed/failed status when meeting already at that status
> returns success (DEBUG-logged), doesn't error.

- [ ] **T.1 — Re-firing same terminal status is idempotent**
  - Hard to drive from dashboard; covered by unit tests
  - If you have admin DB access:
    `select id, status from meetings where status='completed' order by id desc limit 1;`
    Note the id, then via API or DB, set status to 'completed' again. Should return success, not error.

### Pack C.1, C.3, C.4 — Redis client + readiness probe robustness

> meeting-api Redis client has bounded timeouts; /readyz gates traffic;
> /health/collector models actual consumer health.

- [ ] **C.1 — Redis client timeouts present**
  - From terminal: `curl http://<host>:<port>/health` should respond < 1s
  - SSH check: `docker exec vexa-meeting-api-1 grep socket_timeout /app/meeting_api/main.py` returns the value

- [ ] **C.4 — /readyz returns 503 until consumer ready, then 200**
  - During fresh container start, `curl http://<host>:<port>/readyz` may return 503 for a few seconds
  - After warm-up, returns 200
  - Pre-Pack-C.4, /readyz didn't exist; pods could be Ready without a working consumer

- [ ] **C.3 — /health/collector lag-aware**
  - During normal operation: `curl http://<host>:<port>/health/collector` returns 200
  - If Redis stream lag > 100 + idle > 60s, returns 503
  - Hard to drive without chaos; treat as automated-test-only

### Pack K.5 — Runtime-api idle_loop heartbeat

> idle_loop emits structured `[K5] idle_loop iteration=… ts=…` line per tick.

- [ ] **K.5.1 — Idle loop heartbeat visible in logs**
  - SSH lite/compose VM
  - `docker logs vexa-runtime-api-1 2>&1 | grep "\[K5\]" | tail -5`
  - Should see periodic lines, ~1 per IDLE_CHECK_INTERVAL (default 30s)

### Pack J observability — chunk_write log line ([PLATFORM] ASK 2)

> Every meeting-api chunk write emits a structured `[E1A] chunk_write` line.

- [ ] **E1A.log — Chunk write log lines visible during recording**
  - During an in-progress meeting recording:
    `docker logs vexa-meeting-api-1 2>&1 | grep "\[E1A\] chunk_write" | tail -10`
  - Each chunk produces a line with: `meeting_id, recording_id, media_type, chunk_seq, prior_count, action=appended|in_place, is_final`
  - This is the signal [PLATFORM]'s production observer keys off post-deploy

---

## Dashboard surface checks (general)

- [ ] **D.dashboard.1 — Login redirects to /meetings (not /agent)**
- [ ] **D.dashboard.2 — Cookies are HttpOnly + SameSite + Secure-when-https**
- [ ] **D.dashboard.3 — `/api/auth/me` returns logged-in user email (not env fallback)**
- [ ] **D.dashboard.4 — Meetings list paginates without overlap (limit/offset)**
- [ ] **D.dashboard.5 — Transcript view loads through proxy `/api/vexa/meetings/<id>/transcript`**
- [ ] **D.dashboard.6 — Bot creation through proxy `POST /api/vexa/bots` returns 201/403/409 (not 500)**

---

## What [PLATFORM] should verify post-deploy (post-image-promotion)

- [ ] **OBS.1 — Production observer running**: `len(media_files) < num_distinct_media_types_in_S3` audit returns 0 orphans across 7-day soak
- [ ] **OBS.2 — `[E1A] chunk_write` log lines flowing through log pipeline**
- [ ] **OBS.3 — `MeetingResponse: stripping invalid failure_stage` warnings either zero (clean DB) or trending to zero (operators repairing)**
- [ ] **OBS.4 — No production 500s on `/meetings` list** (defense-in-depth verified)

---

## Helm-mode gap (this release only)

Helm runtime validation skipped this cycle due to Linode LKE us-ord nodes
failing to boot (verified: 3 successive 2-node provision attempts; nodes
allocated but stayed `status=offline`; non-LKE linodes in same region
are running fine). Tracked as Linode operations issue, not OSS code.

iter-5 chart fixes (symmetric podAntiAffinity on the 4 stateful pods,
templates/deployment-minio.yaml + statefulset-postgres.yaml +
deployment-redis.yaml + deployment-tts-service.yaml) ARE shipped and
will be exercised when [PLATFORM] picks up `release/260427` for staging.

If you have access to a different LKE region or a non-LKE k8s cluster,
manual helm install + spawn-bot test is welcome but not required for ship.

---

## Sign-off

- [ ] All checked items above are PASS
- [ ] Any FAIL items have an issue opened or are explicitly accepted as gaps
- [ ] Reviewer name + date below

Reviewer: ___________________
Date: 2026-04-27
