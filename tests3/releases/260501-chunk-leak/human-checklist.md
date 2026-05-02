# 260501-chunk-leak — human checklist

Tick boxes. `release-ship` blocks until all are `[x]`. Bugs → `make release-issue-add SOURCE=human` (requires GAP + NEW_CHECKS).

## URLs

**lite**
- dashboard:   http://172.233.208.167:3000
- gateway:     http://172.233.208.167:8056
- admin:       http://172.233.208.167:18056
- ssh:         `ssh root@172.233.208.167`

**compose**
- dashboard:   http://172.233.208.171:3001
- /meetings:   http://172.233.208.171:3001/meetings
- /webhooks:   http://172.233.208.171:3001/webhooks
- gateway:     http://172.233.208.171:8056
- /docs:       http://172.233.208.171:8056/docs
- admin:       http://172.233.208.171:18056
- ssh:         `ssh root@172.233.208.171`

**helm**
- dashboard:   http://172.232.25.127:30001
- /meetings:   http://172.232.25.127:30001/meetings
- gateway:     http://172.232.25.127:30056
- kubectl:     `export KUBECONFIG=/home/dima/dev/vexa/tests3/.state-helm/lke_kubeconfig`

## Always

**Lite VM**
- [ ] Open http://172.233.208.167:3000 → magic-link login as test@vexa.ai → /meetings renders <!-- h:c7b06b2c -->
- [ ] `docker logs vexa-lite 2>&1 | grep -i error | tail -5` → no new errors <!-- h:9a306a4e -->
- [ ] `docker stats --no-stream vexa-lite` → MEM < 2 GiB <!-- h:a540221d -->

**Compose VM**
- [ ] Open http://172.233.208.171:3001 → magic-link login → /meetings renders <!-- h:58b220aa -->
- [ ] Open http://172.233.208.171:8056/docs → OpenAPI page renders <!-- h:bd22e365 -->
- [ ] POST /bots with a real Google Meet URL → 201 + container `meeting-*` appears in `docker ps` <!-- h:3c154567 -->
- [ ] Within 60s bot.status → active; `/transcripts/<platform>/<native_id>` returns segments <!-- h:3da4668a -->
- [ ] DELETE the bot → container gone, meeting.status=completed <!-- h:b5649b66 -->
- [ ] `docker compose -f deploy/compose/docker-compose.yml logs --tail=50 | grep -i error` → no new errors <!-- h:d80a145b -->
- [ ] Re-GET `/transcripts/...` after stop → segments still returned (post-meeting persistence) <!-- h:bfa2e8ac -->

**Helm / LKE**
- [ ] `kubectl get pods` → all Running, 0 CrashLoopBackOff <!-- h:3bcaa667 -->
- [ ] Open http://172.232.25.127:30056/ → gateway root JSON <!-- h:64b8f23a -->
- [ ] Open http://172.232.25.127:30001/ → dashboard renders <!-- h:8fef6b7c -->
- [ ] `kubectl get events --field-selector type=Warning --sort-by=.lastTimestamp | tail` → no new warnings <!-- h:c274d6b8 -->

**Release integrity**
- [ ] Every running image tag == `cat deploy/compose/.last-tag` <!-- h:ef0fc4f8 -->
- [ ] `docker ps -a | grep -E 'lifecycle-|webhook-test|spoof-test'` → empty <!-- h:be779868 -->

## This release

## Issues found
_List anything that failed. Each entry → `release-issue-add SOURCE=human` before ship._