# 260421-prod-stabilize — human checklist

Tick boxes. `release-ship` blocks until all are `[x]`. Bugs → `make release-issue-add SOURCE=human` (requires GAP + NEW_CHECKS).

## URLs

**lite**
- dashboard:   http://172.232.0.163:3000
- gateway:     http://172.232.0.163:8056
- admin:       http://172.232.0.163:18056
- ssh:         `ssh root@172.232.0.163`

**compose**
- dashboard:   http://172.239.57.155:3001
- /meetings:   http://172.239.57.155:3001/meetings
- /webhooks:   http://172.239.57.155:3001/webhooks
- gateway:     http://172.239.57.155:8056
- /docs:       http://172.239.57.155:8056/docs
- admin:       http://172.239.57.155:18056
- ssh:         `ssh root@172.239.57.155`

**helm**
- dashboard:   http://172.236.111.198:30001
- /meetings:   http://172.236.111.198:30001/meetings
- gateway:     http://172.236.111.198:30056
- kubectl:     `export KUBECONFIG=/home/dima/dev/vexa/tests3/.state-helm/lke_kubeconfig`

## Always

**Lite VM**
- [ ] Open http://172.232.0.163:3000 → magic-link login as test@vexa.ai → /meetings renders <!-- h:4783bf35 -->
- [ ] `docker logs vexa-lite 2>&1 | grep -i error | tail -5` → no new errors <!-- h:9a306a4e -->
- [ ] `docker stats --no-stream vexa-lite` → MEM < 2 GiB <!-- h:a540221d -->

**Compose VM**
- [ ] Open http://172.239.57.155:3001 → magic-link login → /meetings renders <!-- h:18cd7e06 -->
- [ ] Open http://172.239.57.155:8056/docs → OpenAPI page renders <!-- h:b0581547 -->
- [ ] POST /bots with a real Google Meet URL → 201 + container `meeting-*` appears in `docker ps` <!-- h:3c154567 -->
- [ ] Within 60s bot.status → active; `/transcripts/<platform>/<native_id>` returns segments <!-- h:3da4668a -->
- [ ] DELETE the bot → container gone, meeting.status=completed <!-- h:b5649b66 -->
- [ ] `docker compose -f deploy/compose/docker-compose.yml logs --tail=50 | grep -i error` → no new errors <!-- h:d80a145b -->
- [ ] Re-GET `/transcripts/...` after stop → segments still returned (post-meeting persistence) <!-- h:bfa2e8ac -->

**Helm / LKE**
- [ ] `kubectl get pods` → all Running, 0 CrashLoopBackOff <!-- h:3bcaa667 -->
- [ ] Open http://172.236.111.198:30056/ → gateway root JSON <!-- h:f8e26fc7 -->
- [ ] Open http://172.236.111.198:30001/ → dashboard renders <!-- h:c9454d69 -->
- [ ] `kubectl get events --field-selector type=Warning --sort-by=.lastTimestamp | tail` → no new warnings <!-- h:c274d6b8 -->

**Release integrity**
- [ ] Every running image tag == `cat deploy/compose/.last-tag` <!-- h:ef0fc4f8 -->
- [ ] `docker ps -a | grep -E 'lifecycle-|webhook-test|spoof-test'` → empty <!-- h:be779868 -->

## This release

**chart-every-prod-secret-via-secretkeyref** _(helm)_
- [ ] [helm] Render the chart four times with each of `DB_PASSWORD` / `TRANSCRIPTION_SERVICE_TOKEN` / `JWT_SECRET` / `NEXTAUTH_SECRET` absent from values (and without `--set`) → Each render exits non-zero with a `required` error naming the missing secret <!-- h:9d2654f1 -->
- [ ] [helm] After a successful install, `kubectl get deploy -o yaml | grep -B1 -A3 'DB_PASSWORD\|TRANSCRIPTION_SERVICE_TOKEN\|JWT_SECRET\|NEXTAUTH_SECRET'` → Every hit shows `valueFrom.secretKeyRef`; no plain `value:` entries <!-- h:9fc085b4 -->

**bot-recording-incremental-chunk-upload** _(compose,helm)_
- [ ] [helm] Start a bot, let it record for >10 minutes, then `DELETE /bots/<unknown:id>` → Every 30-s chunk is present in MinIO at `recordings/<user>/<id>/<session>/NNNNNN.webm`; `media_files` array has one entry per chunk; `Recording.status=COMPLETED`; pod exits code 0 <!-- h:8157489d -->
- [ ] [compose] Start a bot, record for ~90 s (expect 3 chunks), SIGKILL the pod → First 2-3 chunks survive in MinIO; `Recording.status=IN_PROGRESS` stays (never COMPLETED); retrieving the recording returns the partial data (or a 'partial' marker) <!-- h:24345ac8 -->

**transcript-rendering-dedup-prefers-confirmed-on-containment** _(lite)_
- [ ] [lite] Open a live meeting on the dashboard, speak a phrase, then watch the segment for ~30 s → Italic draft is replaced by confirmed on the first confirmation tick; no italic remnants remain after speech stops <!-- h:da16fd93 -->

**engine-pool-reset-on-return-guarded** _(compose,helm,lite)_
- [ ] [compose] From api-gateway container, `for i in {1..200}; do curl -s -H "X-API-Key: $TOKEN" $GATEWAY/bots/status; done` while watching `SELECT count(*) FROM pg_stat_activity WHERE application_name LIKE '%meeting-api%'` → Active connection count stays ≤ `DB_POOL_SIZE + DB_MAX_OVERFLOW`; no growth into 'idle in transaction' <!-- h:fabc1eb8 -->

**chart-rolling-update-zero-pool-overlap** _(helm)_
- [ ] [helm] Trigger a helm upgrade that changes one subchart Deployment field (e.g. image tag); watch `kubectl get rs -w` for the rolled service → Old ReplicaSet scales 1→0 BEFORE new scales 0→1 (zero overlap); for api-gateway, 2 replicas allow rolling one at a time with zero downtime <!-- h:55ee576c -->

**pgbouncer-as-optional-oss-chart-component** _(helm)_
- [ ] [helm] Render chart with default values (`pgbouncer.enabled: false`); then render again with `--set pgbouncer.enabled=true` → Default render has no pgbouncer Deployment/Service. Enabled render contains both and every service's `DB_HOST` env resolves to the pgbouncer Service name (not the postgres Service name) <!-- h:491166e7 -->

**runtime-api-exit-callback-delivery-is-durable** _(compose)_
- [ ] [compose] Create a bot; SIGSTOP meeting-api (or blackhole its URL); stop the bot; wait 2× IDLE_CHECK_INTERVAL; SIGCONT meeting-api → Within another IDLE_CHECK_INTERVAL, the meeting row transitions out of `active` to `completed` (or `failed` with exit_code if the bot was SIGKILLed). No manual intervention needed. <!-- h:8e0b90d0 -->

**lite-postgres-publicly-exposed** _(compose,lite)_
- [ ] [lite] From a host outside the VM run `nc -zv 172.232.0.163 5432` (external scan) → Connection is refused or filtered (NOT 'Connection succeeded'). <!-- h:d86caa6f -->
- [ ] [lite] `docker exec vexa-postgres psql -U postgres -l` — from inside the VM → database 'vexa' is present; 'readme_to_recover' is absent. <!-- h:6d4d89e2 -->
- [ ] [compose] From a host outside the compose VM run `nc -zv 172.239.57.155 5458` → Connection is refused or filtered. <!-- h:123fe2d1 -->

## Issues found
_List anything that failed. Each entry → `release-issue-add SOURCE=human` before ship._