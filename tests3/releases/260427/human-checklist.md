# Human validation — v0.10.5 (260427) — Production hardening release

**Audience**: human reviewer with eyes + judgment.
**Goal**: catch what the matrix + Pack X synthetic rig cannot — visual regressions, real-meeting-end-to-end flows, UX feel, and privacy/security spot-checks.

---

## URLs

| mode | dashboard | API gateway | API docs | Notes |
|------|-----------|-------------|----------|-------|
| **Lite** | http://172.234.213.221:3000 | http://172.234.213.221:8056 | http://172.234.213.221:8056/docs | Single-VM, single-container; process backend (no docker-in-bot) |
| **Compose** | http://172.234.26.151:3001 | http://172.234.26.151:8056 | http://172.234.26.151:8056/docs | Multi-container docker compose; closest to platform staging shape |
| **Helm** | http://172.232.190.221:30001 | http://172.232.190.221:30056 | http://172.232.190.221:30056/docs | LKE us-sea cluster, 2 nodes. Pack I.s symmetric anti-affinity verified live |

> **Compose dashboard is on port 3001** (not 3000) per the lite/compose port split. Easy to mis-paste.

---

## Login flow (each mode, ~30s each)

1. Open dashboard URL in browser
2. Enter `test@vexa.ai` (or any seeded email)
3. Magic-link form posts to `/api/auth/send-magic-link`
4. Token in dashboard logs:
   - **Lite**: `ssh root@172.234.213.221 'docker logs vexa-lite 2>&1 | grep -E "magic_link.*token" | tail -1'`
   - **Compose**: `ssh root@172.234.26.151 'docker logs vexa-dashboard-1 2>&1 | grep magic_link | tail -1'`
   - **Helm**: `kubectl logs -n default deployment/vexa-vexa-dashboard --tail=50 | grep magic_link | tail -1`
5. Open `/api/auth/verify?token=<TOKEN>` → should redirect to `/meetings`

**HUMAN-VISIBLE expectations**:
- ☐ `/meetings` page loads (NOT `/agent` — disabled per security-hygiene)
- ☐ Browser address bar shows `Secure 🔒` only on https deployments (lite/compose are http; flag still works correctly)
- ☐ Cookie inspection (DevTools): `vexa-token` cookie has `HttpOnly` + `SameSite=Lax` set

---

## What only humans can validate (in priority order)

### 1. Real-meeting end-to-end (~5 min per platform per mode)

**This is the highest-value human work.** The matrix runs static + synthetic checks; only humans can drive a real Zoom/Meet meeting and look at the transcript.

For **EACH** of the 3 modes:
1. ☐ **Spawn a Google Meet bot** for a real meeting you control (e.g., your personal `meet.google.com/abc-defg-hij`)
   - Use the dashboard "Add bot" flow OR `curl -H "X-API-Key: $KEY" -d '{"meeting_url":"...","platform":"google_meet"}' /bots`
   - Watch the bot join in your Meet tab — it should appear within 30s
2. ☐ **Speak for ~30 seconds** while the bot is in the meeting
3. ☐ **Click Stop** in the dashboard
4. ☐ **Refresh the meeting detail page** — verify:
   - status = `completed` (green badge)
   - completion_reason = `stopped`
   - transcript pane shows your spoken text (or close enough — Whisper will transcribe imperfectly)
   - recording panel shows `audio` media file with `is_final=true`

**If you have time, do the same with Zoom (canonical zoom.us URL).**

### 2. Path 3 — white-label / enterprise URL trust model (NEW in v0.10.5)

**The Linux Foundation Zoom URL test that started this thread.** This validates the architectural shift to (URL + platform) trust.

For **compose mode** only (helm/lite same code, no need to repeat):
1. ☐ Spawn a bot via API with the LFX URL:
```
curl -X POST http://172.234.26.151:8056/bots \
  -H "X-API-Key: <YOUR_KEY>" -H "Content-Type: application/json" \
  -d '{"meeting_url":"https://zoom-lfx.platform.linuxfoundation.org/meeting/96088138284?password=c9e528a8-3852-4b82-89c2-96d6f22526ad","platform":"zoom"}'
```
2. ☐ **Expected**: HTTP 201 with `native_meeting_id="url-<hash>"` (parser failed; trust path kicked in)
3. ☐ Bot transitions `requested → joining` (visible in dashboard meeting list)
4. ☐ The bot will likely end at `needs_human_help` because LFX uses SSO — that's a separate v0.10.6 ask. **What matters here**: the API ACCEPTED the LFX URL without you needing to know the canonical zoom.us shape

**Counter-test** (should fail):
1. ☐ Same POST but WITHOUT `"platform":"zoom"` → HTTP 422 "Either provide platform + native_meeting_id, OR platform + meeting_url, OR set agent_enabled=true, OR set mode='browser_session'"

### 3. Dashboard UX spot-checks (~5 min, each mode)

These are visual regressions the matrix can't catch:

- ☐ **Meetings list** loads without spinner-stuck states
- ☐ **Meetings list** payload is small — open DevTools Network tab, filter `meetings`, inspect response size: should be ~10-50 KB for any reasonable list (Pack L: data field omitted by default; full data only on `?include=data`)
- ☐ **Meeting detail** page renders all panels without console errors (DevTools → Console)
- ☐ **Transcript panel** wraps long lines, scrolls cleanly, doesn't overflow
- ☐ **Recording player** (if recording exists) loads + plays audio
- ☐ **Status badges** color-code correctly (green=completed, red=failed, yellow=stopping, gray=requested)
- ☐ **Pagination** (limit/offset) works without page-overlap

### 4. Privacy / security spot-checks

The matrix asserts `webhook_secret` is stripped from list responses but only humans can confirm it doesn't leak elsewhere:

- ☐ Open a meeting that has a `webhook_secret` set
- ☐ Inspect dashboard's network requests in DevTools — search response bodies for the secret string
- ☐ ☑ **PASS**: secret never appears
- ☐ ☒ **FAIL**: secret appears in any API response

Repeat for `data` field on `/meetings/{id}` detail (full data is allowed there if explicit; just verify it's not echoed back via webhooks list, transcripts list, etc).

### 5. Failed-meeting classification visibility (Pack J + completion_reason persistence — REAL BUG fix)

**This was the actual silent-class bug Pack X surfaced.** Verify the fix is visible:

- ☐ Find or create a meeting that ends in `failed` status (e.g., spawn bot for a meeting that doesn't exist; let it timeout)
- ☐ On the meeting detail page, the failure reason should be VISIBLE — one of:
  - `awaiting_admission_timeout` (bot waited too long for host to admit)
  - `stopped_with_no_audio` (bot ran ≥30s, transcribe enabled, 0 transcripts)
  - `stopped_before_admission` (bot stopped before reaching active)
  - `evicted` (host removed bot)
  - `max_bot_time_exceeded` (scheduler timed out the bot)

- ☐ ☑ **PASS**: completion_reason field is non-empty
- ☐ ☒ **FAIL**: completion_reason shows null/empty/None despite status=failed (the silent-class bug pre-iter-7)

### 6. Bot lifecycle observability (Pack G.1 + Pack K.5 + ASK 2 log lines)

These are runtime invariants — humans should spot-check the log streams:

- ☐ During an active meeting, SSH into the VM and tail the bot logs:
  - **Lite**: `docker exec vexa-lite tail -f /var/log/containers/meeting-<id>-*.log`
  - **Compose**: bot logs are inside the runtime-api process backend; `docker logs vexa-runtime-api-1 2>&1 | grep meeting_id=<id>`
- ☐ Each line should be **structured JSON** (Pack G.1):
  ```json
  {"ts":"...","level":"info","meeting_id":42,"session_uid":"...","platform":"google_meet","subsystem":"...","msg":"..."}
  ```
- ☐ ☑ **PASS**: every line is single-JSON-object with `meeting_id`, `session_uid`, `platform`, `subsystem`
- ☐ ☒ **FAIL**: free-form `print()` strings or missing context fields

- ☐ Tail meeting-api logs for `[E1A] chunk_write` lines (Pack ASK 2):
  ```
  [E1A] chunk_write meeting_id=42 recording_id=... media_type=audio chunk_seq=0 prior_count=0 action=appended is_final=False
  ```
  - ☐ ☑ **PASS**: ≥1 line per chunk written, with `action=appended` (first) or `in_place` (subsequent)

- ☐ Tail runtime-api logs for `[K5] idle_loop iteration` lines (Pack K.5):
  ```
  [K5] idle_loop iteration=12 ts=2026-04-27T...
  ```
  - ☐ ☑ **PASS**: ≥1 line per `IDLE_CHECK_INTERVAL` (default 30s)

### 7. Helm-specific — Pack I.s anti-affinity in production-shape

**This is the one Pack I.s test that needs human eyes.** The matrix verified statefuls were spread; you should LOOK at the Linode console:

```bash
export KUBECONFIG=$(pwd)/tests3/.state-helm/lke_kubeconfig
kubectl get pods -n default -o wide | grep -E "postgres|redis|minio|tts"
```

- ☐ ☑ **PASS**: postgres + tts on one node; redis + minio on the other
- ☐ ☒ **FAIL**: 4 stateful pods on the same node (would hit Linode's 7-vol cap on chart-rolling-upgrade)

### 8. dry_run flag spot-check (Pack X)

**Production safety**: `dry_run` must NEVER work in production. Human verification:

- ☐ On compose VM, override env temporarily and verify 422:
```
ssh root@172.234.26.151 'docker exec vexa-meeting-api-1 sh -c "VEXA_ENV=production python3 -c \"import os; print(os.environ.get(\\\"VEXA_ENV\\\"))\""'
```
- ☐ Then in another terminal, try a dry_run POST against a hypothetical "production" instance — should 422 with "dry_run=true is a test-mode flag; not allowed in production"

(Realistically, this is automated by `PACK_X_DRY_RUN_FLAG_GATED` static check + `PACK_X_DRY_RUN_SCHEMA_FIELD`, so this human-check is a third-line defense.)

---

## What I (the AI agent) ALREADY validated autonomously

For context — these are GREEN already, you don't need to re-verify:

| layer | what | status |
|-------|------|--------|
| Static checks | 66/66 PASS on lite, compose, helm modes | ✅ |
| Smoke contract | 27/27 PASS | ✅ |
| Smoke env | 7/7 PASS | ✅ |
| Smoke health | 17/17 PASS | ✅ |
| Pack X synthetic rig | 7 scenarios — verifying with dry_run isolation right now | ⏳ |
| Pack R schema tolerance | ✅ verified |
| Pack T idempotent terminal | ✅ verified |
| Resource leaks (Redis keys) | ✅ no leak across lifecycle |
| Adversarial input fuzz | ✅ all malformed inputs return 4xx, no 5xx |
| 12 unit tests for URL parser + dry_run | ✅ all pass |

---

## How to report findings

Open a GitHub issue or comment on #272 with:

- **Mode**: lite / compose / helm
- **Pack / surface**: which of the sections above
- **What I expected**: copy-paste from this doc
- **What I saw**: screenshot + curl output if relevant
- **Severity**: blocking / annoying / cosmetic

For SHIP-BLOCKING findings only:
- **Reproduction steps**: minimal sequence
- **Probability**: 100%/intermittent/once

---

## Sign-off

When all checked items above PASS (or are explicitly accepted as gaps for v0.10.6):

- [ ] All sections 1-8 reviewed
- [ ] Any FAILs have an issue opened OR documented gap
- [ ] Reviewer name + date below

Reviewer: ___________________
Date: 2026-04-27

---

## Notes for the AI agent (post-human-review)

When the human signs off, the AI agent should:
1. Verify all checkboxes are ticked OR document accepted gaps in `release-narrative.md`
2. Transition stage from `human` → `ship` via `python3 tests3/lib/stage.py enter ship`
3. Tag the release: `git tag vexa-0.10.5 && git push --tags`
4. Bump Chart.yaml version to 0.10.5 in the same commit
5. Merge `release/260427` to `main` per the release-protocol
6. Promote `:dev` images to `:latest` and `:0.10.5` tags
7. Update milestone #14 with shipped issues
