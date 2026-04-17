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

- [ ] Open http://172.239.194.217:3000 → magic-link login as test@vexa.ai → /meetings renders <!-- h:252bcda9 -->
- [ ] `docker logs vexa-lite 2>&1 | grep -i error | tail -5` → no new errors <!-- h:9a306a4e -->
- [ ] `docker stats --no-stream vexa-lite` → MEM < 2 GiB <!-- h:a540221d -->

### Compose VM

- [ ] Open http://172.239.194.222:3001 → magic-link login → /meetings renders <!-- h:0743882c -->
- [ ] Open http://172.239.194.222:8056/docs → OpenAPI page renders <!-- h:5706e604 -->
- [ ] POST /bots with a real Google Meet URL → 201 + container `meeting-*` appears in `docker ps` <!-- h:3c154567 -->
- [ ] Within 60s bot.status → active; `/transcripts/<platform>/<native_id>` returns segments <!-- h:3da4668a -->
- [ ] DELETE the bot → container gone, meeting.status=completed <!-- h:b5649b66 -->
- [ ] `docker compose -f deploy/compose/docker-compose.yml logs --tail=50 | grep -i error` → no new errors <!-- h:d80a145b -->
- [ ] Re-GET `/transcripts/...` after stop → segments still returned (post-meeting persistence) <!-- h:bfa2e8ac -->

### Helm / LKE

- [ ] `kubectl get pods` → all Running, 0 CrashLoopBackOff <!-- h:3bcaa667 -->
- [ ] Open http://172.238.169.249:30056/ → gateway root JSON <!-- h:f354a411 -->
- [ ] Open http://172.238.169.249:30001/ → dashboard renders <!-- h:0ff40a16 -->
- [ ] `kubectl get events --field-selector type=Warning --sort-by=.lastTimestamp | tail` → no new warnings <!-- h:c274d6b8 -->

### Release integrity

- [ ] Every running image tag == `cat deploy/compose/.last-tag` <!-- h:ef0fc4f8 -->
- [ ] `docker ps -a | grep -E 'lifecycle-|webhook-test|spoof-test'` → empty <!-- h:be779868 -->

## THIS RELEASE — scope-specific

_Source: `tests3/releases/260417-webhooks-dbpool/scope.yaml` → `issues[].human_verify[]`._

### `webhook-gateway-injection`  _(required modes: compose)_

**Problem**: User-configured webhooks (via PUT /user/webhook) are not delivered — user.data has webhook_url/secret/events but meeting.data on new bots never gets them. "We only see meeting.completed; status webhooks never arrive."

- [ ] **[compose]** Do: PUT /user/webhook {webhook_url:httpbin.org/post}; POST /bots  →  Expect: response.data.webhook_url == httpbin URL <!-- h:2f661dbb -->
- [ ] **[compose]** Do: POST /bots with `X-User-Webhook-URL: attacker.example.com/steal`  →  Expect: response.data.webhook_url is user's URL, not attacker's <!-- h:1ece2438 -->

### `webhook-status-fast-path`  _(required modes: compose)_

**Problem**: Status-change webhooks (meeting.started, meeting.stopping, bot.failed) never fire end-to-end even after gateway injection works. Completion webhook fires once but webhook_deliveries[] stays empty.

- [ ] **[compose]** Do: POST /bots with fake URL → DELETE within 5s → wait 20s  →  Expect: meeting.data.webhook_delivery.status=delivered AND webhook_deliveries[] not empty <!-- h:331b2d9a -->

### `db-pool-exhaustion`  _(required modes: compose, helm, lite)_

**Problem**: GET /bots/status returns 504 after ~10 sequential requests in compose and helm. Pool (5+5=10) fills; new requests block 30s on pool_timeout then fail.

- [ ] **[compose]** Do: `for i in $(seq 1 30); do curl -sfo/dev/null -w '%<unknown:http_code>\n' -H "X-API-Key: $T" http://172.239.194.222:8056/bots/status; done`  →  Expect: 30× 200, zero 504 <!-- h:e340e33d -->
- [ ] **[compose]** Do: `docker exec vexa-postgres-1 psql -U postgres -d vexa -c "SELECT count(*) FROM pg_stat_activity WHERE state='idle in transaction'"`  →  Expect: count ≤ 1 <!-- h:1bcea834 -->

### `transcripts-gone-after-stop`  _(required modes: compose, lite)_

**Problem**: Short meetings show transcripts during recording but 0 rows in transcriptions table after completion. Reported on lite meeting 2.

- [ ] **[lite]** Do: `docker exec vexa-lite redis-cli XGROUP DESTROY transcription_segments collector_group` → wait 15s  →  Expect: `docker logs vexa-lite 2>&1 | grep 'Recreated consumer group'` matches within 15s <!-- h:2bc630e3 -->
- [ ] **[lite]** Do: Run a live ~60s meeting, stop bot  →  Expect: `SELECT COUNT(*) FROM transcriptions WHERE meeting_id=<id>` > 0 <!-- h:c8307383 -->

### `recording-enabled-default`  _(required modes: compose)_

**Problem**: Lite bots recorded by default but compose/helm defaulted to no recording (BOT_CONFIG recordingEnabled=false), so recordings were empty unless RECORDING_ENABLED=true was explicitly set.

- [ ] **[compose]** Do: POST /bots without `recording_enabled` in body  →  Expect: response.data.recording_enabled == true <!-- h:02dc0fe7 -->

### `dashboard-webhooks-ui-rollup`  _(required modes: compose)_

**Problem**: /webhooks dashboard page shows only meeting.completed deliveries; backend delivered all event types (verified via psql) but the UI hid them.

- [ ] **[compose]** Do: Load http://172.239.194.222:3001/webhooks after a meeting with status-change events  →  Expect: Delivery History table has rows with event column = meeting.started AND meeting.status_change, not only meeting.completed <!-- h:63c05a0f -->

### `lite-vexa-db-missing`  _(required modes: compose, lite)_

**Problem**: Lite /login returned 500 'Server Configuration Error' hours after a successful validation run. admin-api crashed on every request with asyncpg.InvalidCatalogNameError: database vexa does not exist. pg_database only had template0/template1/postgres and a Linode-injected readme_to_recover marker.

- [ ] **[lite]** Do: After 1h idle on the lite VM, curl http://172.239.194.217:3000/login AND docker exec vexa-lite psql -U postgres -l  →  Expect: /login returns 200 (not 500) AND psql list shows vexa DB AND no readme_to_recover DB <!-- h:47fcc278 -->

### `helm-meetings-all-failed-pollution`  _(required modes: compose, helm, lite)_

**Problem**: /meetings on helm dashboard shows 27 rows, all status=failed. Human reads this as 'everything broken'; actual cause is accumulated test-pollution from multiple validation runs without a reset between them.

- [ ] **[helm]** Do: Load http://172.238.169.249:30001/meetings after release-full completes (fresh reset)  →  Expect: Either no meetings (fresh) or mixed status (not 100% failed) <!-- h:7b405bd4 -->

## Issues found

_Leave empty if clean. Any bug surfaced here must be resolved (fix + new pipeline run) before this checklist is signed off._


## Sign-off

- [ ] All ALWAYS items checked. <!-- h:5b828958 -->
- [ ] All THIS RELEASE items checked. <!-- h:5a288caf -->
- [ ] No unresolved entries in `Issues found`. <!-- h:d7dd9f2b -->

Once all three boxes are checked AND `make release-full SCOPE=...` succeeded, `make release-ship` is unblocked.