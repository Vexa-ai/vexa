# Human validation — `260417-webhooks-dbpool`

> Webhook delivery hardening (gateway injection + status webhooks + delivery
tracking), DB connection pool fix + Postgres-side timeout, collector
NOGROUP recovery, structured test reports + per-feature DoD rollup, and
release validation refactor into a 7-step commanded process.


Check each box by editing `- [ ]` → `- [x]`. **`make release-ship` refuses to run until every box is checked.** If something fails, note it in the `## Issues found` section at the bottom and resolve (either re-run the pipeline with a fix, or annotate the exception) before merging.

## Access

| Mode | URL | SSH / kubectl |
|------|-----|---------------|
| lite | http://172.239.194.217:3000 | `ssh root@172.239.194.217` |
| compose | http://172.239.194.222:3001 | `ssh root@172.239.194.222` |
| helm | http://172.238.169.249:30001 | `export KUBECONFIG=/home/dima/dev/vexa/tests3/.state-helm/lke_kubeconfig` |

## ALWAYS — applies to every release

_Source: `tests3/human-always.yaml`. These verify the product works regardless of what changed._

### Lite VM

- [ ] Dashboard at http://172.239.194.217:3000 loads; magic-link login works (test@vexa.ai).
- [ ] /meetings page renders (even if empty).
- [ ] Create a browser session via API; CDP URL returns a working session.
- [ ] Logs clean: `docker logs vexa-lite 2>&1 | grep -i error | tail -20` shows nothing concerning.
- [ ] Container stays under ~2 GB: `docker stats --no-stream vexa-lite`.

### Compose VM

- [ ] Dashboard at http://172.239.194.222:3001 loads; magic-link login works.
- [ ] API docs page at http://172.239.194.222:8056/docs renders.
- [ ] Create a bot via dashboard UI with a REAL Google Meet URL; bot container appears (`docker ps --filter name=meeting-`).
- [ ] Bot joins the meeting within 60s; status transitions to active.
- [ ] Transcript segments appear live (WS) and via REST at `/transcripts/<platform>/<native_id>`.
- [ ] DELETE bot; container fully removed; meeting status=completed.
- [ ] Logs clean: `cd /root/vexa && docker compose -f deploy/compose/docker-compose.yml logs --tail=50 2>&1 | grep -i error`.
- [ ] Post-meeting transcript persisted: same /transcripts endpoint returns non-empty segments after bot stop.

### Helm / LKE cluster

- [ ] `kubectl get pods` shows every vexa component in Running state (no CrashLoopBackOff).
- [ ] Gateway reachable at http://172.238.169.249:30056/.
- [ ] Dashboard reachable at http://172.238.169.249:30001/.
- [ ] `kubectl get events --field-selector type=Warning --sort-by=.lastTimestamp` has no new warnings.
- [ ] Create a bot via API (X-API-Key); bot pod spawns; no scheduling errors.

### Release integrity

- [ ] Versions: all running images carry the SAME timestamp tag. `docker inspect` (compose/lite) or `kubectl describe pod` (helm) agrees with the build tag in `deploy/compose/.last-tag`.
- [ ] No stale containers from prior test runs: `docker ps -a | grep -E 'lifecycle-|webhook-test|spoof-test'` empty.
- [ ] No error spike in last 5 minutes of meeting-api logs.

## THIS RELEASE — scope-specific

_Source: `tests3/releases/260417-webhooks-dbpool/scope.yaml` → `issues[].human_verify[]`._

### `webhook-gateway-injection`  _(required modes: compose)_

**Problem**: User-configured webhooks (via PUT /user/webhook) are not delivered — user.data has webhook_url/secret/events but meeting.data on new bots never gets them. "We only see meeting.completed; status webhooks never arrive."

- [ ] **[compose]** Do: PUT /user/webhook with {webhook_url: https://httpbin.org/post, webhook_events: {meeting.completed:true}}  →  Expect: POST /bots (no X-User-Webhook-* header) → response.data.webhook_url == your URL
- [ ] **[compose]** Do: Repeat POST /bots but WITH a client-supplied X-User-Webhook-URL: https://attacker.example.com/steal  →  Expect: response.data.webhook_url is your URL, NOT the attacker URL (header stripped)

### `webhook-status-fast-path`  _(required modes: compose)_

**Problem**: Status-change webhooks (meeting.started, meeting.stopping, bot.failed) never fire end-to-end even after gateway injection works. Completion webhook fires once but webhook_deliveries[] stays empty.

- [ ] **[compose]** Do: PUT /user/webhook with webhook_events={meeting.completed:true, meeting.status_change:true}; POST /bots with a fake google_meet URL; DELETE immediately (<5s → hits fast-path)  →  Expect: After 20s, meeting.data has webhook_delivery.status=delivered AND webhook_deliveries[] has >= 1 entry

### `db-pool-exhaustion`  _(required modes: compose, helm, lite)_

**Problem**: GET /bots/status returns 504 after ~10 sequential requests in compose and helm. Pool (5+5=10) fills; new requests block 30s on pool_timeout then fail.

- [ ] **[compose]** Do: Hit GET /bots/status 30 times in a loop (`for i in $(seq 1 30); do curl -sf -H 'X-API-Key: ...' http://172.239.194.222:8056/bots/status -o /dev/null -w '%<unknown:http_code>\n'; done`)  →  Expect: Every response is 200; no 504s
- [ ] **[compose]** Do: Check Postgres for idle-in-transaction connections: `docker exec vexa-postgres-1 psql -U postgres -d vexa -c "SELECT count(*) FROM pg_stat_activity WHERE state='idle in transaction';"`  →  Expect: count is 0 (or near 0) even after sustained load

### `transcripts-gone-after-stop`  _(required modes: compose, lite)_

**Problem**: Short meetings show transcripts during recording but 0 rows in transcriptions table after completion. Reported on lite meeting 2.

- [ ] **[lite]** Do: Force Redis group loss: `docker exec vexa-lite redis-cli XGROUP DESTROY transcription_segments collector_group; docker exec vexa-lite redis-cli XGROUP DESTROY speaker_events_relative collector_speaker_group` — wait 15s  →  Expect: Logs show `Recreated consumer group 'collector_group'` within 10s; `docker exec vexa-lite redis-cli XINFO GROUPS transcription_segments` shows the group back
- [ ] **[lite]** Do: Run a real ~60s meeting end-to-end (bot + audio), stop bot  →  Expect: meeting.data.end_time set AND `SELECT COUNT(*) FROM transcriptions WHERE meeting_id=<id>` > 0

### `recording-enabled-default`  _(required modes: compose)_

**Problem**: Lite bots recorded by default but compose/helm defaulted to no recording (BOT_CONFIG recordingEnabled=false), so recordings were empty unless RECORDING_ENABLED=true was explicitly set.

- [ ] **[compose]** Do: POST /bots without `recording_enabled` in the body  →  Expect: response.data.recording_enabled == true

## Issues found

_Leave empty if clean. Any bug surfaced here must be resolved (fix + new pipeline run) before this checklist is signed off._


## Sign-off

- [ ] All ALWAYS items checked.
- [ ] All THIS RELEASE items checked.
- [ ] No unresolved entries in `Issues found`.

Once all three boxes are checked AND `make release-full SCOPE=...` succeeded, `make release-ship` is unblocked.
