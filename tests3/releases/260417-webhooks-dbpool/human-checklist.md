# 260417-webhooks-dbpool — human checklist

Tick boxes. `release-ship` blocks until all are `[x]`. Bugs → `make release-issue-add SOURCE=human` (requires GAP + NEW_CHECKS).

## URLs

**lite**
- dashboard:   http://172.239.194.217:3000
- gateway:     http://172.239.194.217:8056
- admin:       http://172.239.194.217:18056
- ssh:         `ssh root@172.239.194.217`

**compose**
- dashboard:   http://172.239.194.222:3001
- /meetings:   http://172.239.194.222:3001/meetings
- /webhooks:   http://172.239.194.222:3001/webhooks
- gateway:     http://172.239.194.222:8056
- /docs:       http://172.239.194.222:8056/docs
- admin:       http://172.239.194.222:18056
- ssh:         `ssh root@172.239.194.222`

**helm**
- dashboard:   http://172.238.169.249:30001
- /meetings:   http://172.238.169.249:30001/meetings
- gateway:     http://172.238.169.249:30056
- kubectl:     `export KUBECONFIG=/home/dima/dev/vexa/tests3/.state-helm/lke_kubeconfig`

## Always

**Lite VM**
- [ ] Open http://172.239.194.217:3000 → magic-link login as test@vexa.ai → /meetings renders <!-- h:252bcda9 -->
- [ ] `docker logs vexa-lite 2>&1 | grep -i error | tail -5` → no new errors <!-- h:9a306a4e --> _(pre-existing: SQLAlchemy Transaction.rollback on closed connection — cosmetic; not new to this release)_
- [x] `docker stats --no-stream vexa-lite` → MEM < 2 GiB <!-- h:a540221d -->

**Compose VM**
- [ ] Open http://172.239.194.222:3001 → magic-link login → /meetings renders <!-- h:0743882c -->
- [x] Open http://172.239.194.222:8056/docs → OpenAPI page renders <!-- h:5706e604 -->
- [ ] POST /bots with a real Google Meet URL → 201 + container `meeting-*` appears in `docker ps` <!-- h:3c154567 -->
- [ ] Within 60s bot.status → active; `/transcripts/<platform>/<native_id>` returns segments <!-- h:3da4668a -->
- [ ] DELETE the bot → container gone, meeting.status=completed <!-- h:b5649b66 -->
- [ ] `docker compose -f deploy/compose/docker-compose.yml logs --tail=50 | grep -i error` → no new errors <!-- h:d80a145b --> _(pre-existing: same SQLAlchemy rollback noise + webhook_retry DNS lookup for agent-api:8100 — OSS doesn't include agent-api, harmless retry noise)_
- [ ] Re-GET `/transcripts/...` after stop → segments still returned (post-meeting persistence) <!-- h:bfa2e8ac -->

**Helm / LKE**
- [x] `kubectl get pods` → all Running, 0 CrashLoopBackOff <!-- h:3bcaa667 -->
- [x] Open http://172.238.169.249:30056/ → gateway root JSON <!-- h:f354a411 -->
- [x] Open http://172.238.169.249:30001/ → dashboard renders <!-- h:0ff40a16 -->
- [x] `kubectl get events --field-selector type=Warning --sort-by=.lastTimestamp | tail` → no new warnings <!-- h:c274d6b8 -->

**Release integrity**
- [x] Every running image tag == `cat deploy/compose/.last-tag` <!-- h:ef0fc4f8 -->
- [x] `docker ps -a | grep -E 'lifecycle-|webhook-test|spoof-test'` → empty <!-- h:be779868 -->

## This release

**webhook-gateway-injection** _(compose)_
- [x] [compose] PUT /user/webhook {webhook_url:httpbin.org/post}; POST /bots → response.data.webhook_url == httpbin URL <!-- h:82c9fb64 -->
- [x] [compose] POST /bots with `X-User-Webhook-URL: attacker.example.com/steal` → response.data.webhook_url is user's URL, not attacker's <!-- h:8153d5fa -->

**webhook-status-fast-path** _(compose)_
- [x] [compose] POST /bots with fake URL → DELETE within 5s → wait 20s → meeting.data.webhook_delivery.status=delivered AND webhook_deliveries[] not empty <!-- h:b7744d0e -->

**db-pool-exhaustion** _(compose,helm,lite)_
- [x] [compose] `for i in $(seq 1 30); do curl -sfo/dev/null -w '%<unknown:http_code>\n' -H "X-API-Key: $T" http://172.239.194.222:8056/bots/status; done` → 30× 200, zero 504 <!-- h:ceb6a6fd -->
- [x] [compose] `docker exec vexa-postgres-1 psql -U postgres -d vexa -c "SELECT count(*) FROM pg_stat_activity WHERE state='idle in transaction'"` → count ≤ 1 <!-- h:e6628277 -->

**transcripts-gone-after-stop** _(compose,lite)_
- [x] [lite] `docker exec vexa-lite redis-cli XGROUP DESTROY transcription_segments collector_group` → wait 15s → `docker logs vexa-lite 2>&1 | grep 'Recreated consumer group'` matches within 15s <!-- h:3380f3a0 -->
- [ ] [lite] Run a live ~60s meeting, stop bot → `SELECT COUNT(*) FROM transcriptions WHERE meeting_id=<id>` > 0 <!-- h:adcbd278 -->

**recording-enabled-default** _(compose)_
- [x] [compose] POST /bots without `recording_enabled` in body → response.data.recording_enabled == true <!-- h:9ec56ae5 -->

**dashboard-webhooks-ui-rollup** _(compose)_
- [x] [compose] Load http://172.239.194.222:3001/webhooks after a meeting with status-change events → Delivery History table has rows with event column = meeting.started AND meeting.status_change, not only meeting.completed <!-- h:6c70cb66 -->

**lite-vexa-db-missing** _(compose,lite)_
- [x] [lite] After 1h idle on the lite VM, curl http://172.239.194.217:3000/login AND docker exec vexa-lite psql -U postgres -l → /login returns 200 (not 500) AND psql list shows vexa DB AND no readme_to_recover DB <!-- h:11a5715e -->

**helm-meetings-all-failed-pollution** _(compose,helm,lite)_
- [x] [helm] Load http://172.238.169.249:30001/meetings after release-full completes (fresh reset) → Either no meetings (fresh) or mixed status (not 100% failed) <!-- h:3ff630b9 -->

## Issues found
_List anything that failed. Each entry → `release-issue-add SOURCE=human` before ship._