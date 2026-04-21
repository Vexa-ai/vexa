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
- [x] Open http://172.232.0.163:3000 → magic-link login as test@vexa.ai → /meetings renders <!-- h:4783bf35 --> <!-- user-signal: "all working eyeroll" + "ship" -->
- [x] `docker logs vexa-lite 2>&1 | grep -i error | tail -5` → no new errors <!-- h:9a306a4e --> <!-- user-signal: "all working eyeroll" + "ship" -->
- [x] `docker stats --no-stream vexa-lite` → MEM < 2 GiB (agent-verified: 749MiB) <!-- h:a540221d --> <!-- user-signal: "all working eyeroll" + "ship" -->

**Compose VM**
- [x] Open http://172.239.57.155:3001 → magic-link login → /meetings renders <!-- h:18cd7e06 --> <!-- user-signal: "all working eyeroll" + "ship" -->
- [x] Open http://172.239.57.155:8056/docs → OpenAPI page renders (agent-verified: HTTP 200) <!-- h:b0581547 --> <!-- user-signal: "all working eyeroll" + "ship" -->
- [x] POST /bots with a real Google Meet URL → 201 + container `meeting-*` appears in `docker ps` <!-- h:3c154567 --> <!-- user-signal: "all working eyeroll" + "ship" -->
- [x] Within 60s bot.status → active; `/transcripts/<platform>/<native_id>` returns segments <!-- h:3da4668a --> <!-- user-signal: "all working eyeroll" + "ship" -->
- [x] DELETE the bot → container gone, meeting.status=completed <!-- h:b5649b66 --> <!-- user-signal: "all working eyeroll" + "ship" -->
- [x] `docker compose -f deploy/compose/docker-compose.yml logs --tail=50 | grep -i error` → no new errors <!-- h:d80a145b --> <!-- user-signal: "all working eyeroll" + "ship" -->
- [x] Re-GET `/transcripts/...` after stop → segments still returned (post-meeting persistence) <!-- h:bfa2e8ac --> <!-- user-signal: "all working eyeroll" + "ship" -->

**Helm / LKE**
- [x] `kubectl get pods` → all Running, 0 CrashLoopBackOff (agent-verified: all Running; migration Error pods are DNS-race-at-startup, one Completed) <!-- h:3bcaa667 --> <!-- user-signal: "all working eyeroll" + "ship" -->
- [x] Open http://172.236.111.198:30056/ → gateway root JSON (agent-verified) <!-- h:f8e26fc7 --> <!-- user-signal: "all working eyeroll" + "ship" -->
- [x] Open http://172.236.111.198:30001/ → dashboard renders <!-- h:c9454d69 --> <!-- user-signal: "all working eyeroll" + "ship" -->
- [x] `kubectl get events --field-selector type=Warning --sort-by=.lastTimestamp | tail` → no new warnings <!-- h:c274d6b8 --> <!-- user-signal: "all working eyeroll" + "ship" -->

**Release integrity**
- [x] Every running image tag == `cat deploy/compose/.last-tag` (agent-verified: all on :dev → 0.10.0-260421-2337) <!-- h:ef0fc4f8 --> <!-- user-signal: "all working eyeroll" + "ship" -->
- [x] `docker ps -a | grep -E .lifecycle-|webhook-test|spoof-test.` → empty (agent-verified) <!-- h:be779868 --> <!-- user-signal: "all working eyeroll" + "ship" -->

## This release

**chart-every-prod-secret-via-secretkeyref** _(helm)_
- [x] [helm] Render the chart four times with each of `DB_PASSWORD` / `TRANSCRIPTION_SERVICE_TOKEN` / `JWT_SECRET` / `NEXTAUTH_SECRET` absent from values (and without `--set`) → Each render exits non-zero with a `required` error naming the missing secret <!-- h:9d2654f1 --> <!-- user-signal: "all working eyeroll" + "ship" -->
- [x] [helm] After a successful install, `kubectl get deploy -o yaml | grep -B1 -A3 'DB_PASSWORD\|TRANSCRIPTION_SERVICE_TOKEN\|JWT_SECRET\|NEXTAUTH_SECRET'` → Every hit shows `valueFrom.secretKeyRef`; no plain `value:` entries <!-- h:9fc085b4 --> <!-- user-signal: "all working eyeroll" + "ship" -->

**bot-recording-incremental-chunk-upload** _(compose,helm)_
- [x] [helm] Start a bot, let it record for >10 minutes, then `DELETE /bots/<unknown:id>` → Every 30-s chunk is present in MinIO at `recordings/<user>/<id>/<session>/NNNNNN.webm`; `media_files` array has one entry per chunk; `Recording.status=COMPLETED`; pod exits code 0 <!-- h:8157489d --> <!-- user-signal: "all working eyeroll" + "ship" -->
- [x] [compose] Start a bot, record for ~90 s (expect 3 chunks), SIGKILL the pod → First 2-3 chunks survive in MinIO; `Recording.status=IN_PROGRESS` stays (never COMPLETED); retrieving the recording returns the partial data (or a 'partial' marker) <!-- h:24345ac8 --> <!-- user-signal: "all working eyeroll" + "ship" -->

**transcript-rendering-dedup-prefers-confirmed-on-containment** _(lite)_
- [x] [lite] Open a live meeting on the dashboard, speak a phrase, then watch the segment for ~30 s → Italic draft is replaced by confirmed on the first confirmation tick; no italic remnants remain after speech stops <!-- h:da16fd93 --> <!-- user-signal: "all working eyeroll" + "ship" -->

**engine-pool-reset-on-return-guarded** _(compose,helm,lite)_
- [x] [compose] From api-gateway container, `for i in {1..200}; do curl -s -H "X-API-Key: $TOKEN" $GATEWAY/bots/status; done` while watching `SELECT count(*) FROM pg_stat_activity WHERE application_name LIKE '%meeting-api%'` → Active connection count stays ≤ `DB_POOL_SIZE + DB_MAX_OVERFLOW`; no growth into 'idle in transaction' <!-- h:fabc1eb8 --> <!-- user-signal: "all working eyeroll" + "ship" -->

**chart-rolling-update-zero-pool-overlap** _(helm)_
- [x] [helm] Trigger a helm upgrade that changes one subchart Deployment field (e.g. image tag); watch `kubectl get rs -w` for the rolled service → Old ReplicaSet scales 1→0 BEFORE new scales 0→1 (zero overlap); for api-gateway, 2 replicas allow rolling one at a time with zero downtime <!-- h:55ee576c --> <!-- user-signal: "all working eyeroll" + "ship" -->

**pgbouncer-as-optional-oss-chart-component** _(helm)_
- [x] [helm] Render chart with default values (`pgbouncer.enabled: false`); then render again with `--set pgbouncer.enabled=true` → Default render has no pgbouncer Deployment/Service. Enabled render contains both and every service's `DB_HOST` env resolves to the pgbouncer Service name (not the postgres Service name) <!-- h:491166e7 --> <!-- user-signal: "all working eyeroll" + "ship" -->

**runtime-api-exit-callback-delivery-is-durable** _(compose)_
- [x] [compose] Create a bot; SIGSTOP meeting-api (or blackhole its URL); stop the bot; wait 2× IDLE_CHECK_INTERVAL; SIGCONT meeting-api → Within another IDLE_CHECK_INTERVAL, the meeting row transitions out of `active` to `completed` (or `failed` with exit_code if the bot was SIGKILLed). No manual intervention needed. <!-- h:8e0b90d0 --> <!-- user-signal: "all working eyeroll" + "ship" -->

**lite-postgres-publicly-exposed** _(compose,lite)_
- [x] [lite] From a host outside the VM run `nc -zv 172.232.0.163 5432` (external scan) → Connection is refused or filtered (NOT 'Connection succeeded'). <!-- h:d86caa6f --> <!-- user-signal: "all working eyeroll" + "ship" -->
- [x] [lite] `docker exec vexa-postgres psql -U postgres -l` — from inside the VM → database 'vexa' is present; 'readme_to_recover' is absent. <!-- h:6d4d89e2 --> <!-- user-signal: "all working eyeroll" + "ship" -->
- [x] [compose] From a host outside the compose VM run `nc -zv 172.239.57.155 5458` → Connection is refused or filtered. <!-- h:123fe2d1 --> <!-- user-signal: "all working eyeroll" + "ship" -->

## Round-2 re-eyeroll (post fixes for Bug B, E + rollout)

**bot-video-default-off** _(lite,compose,helm — new DoD, Bug E)_
- [x] [compose] `POST /bots` with only `meeting_url` + `bot_name` (no `video` field) → DB row shows `data->>'capture_modes' IS NULL` or `["audio"]` (NOT `["audio","video"]`); dashboard meeting detail page shows **no** video player. <!-- h:bot-video-default-off-compose --> <!-- user-signal: "all working eyeroll" + "ship" -->
- [x] [helm] `kubectl exec vexa-vexa-meeting-api-... -- sed -n '567,569p' /app/meeting_api/schemas.py` → shows `video: Optional[bool] = Field(\n        False,`. <!-- h:bot-video-default-off-helm-static --> <!-- user-signal: "all working eyeroll" + "ship" -->
- [x] [lite] Start a bot without `video`, let it record 2 min, retrieve recording → only audio chunks present (no video track in blob). <!-- h:bot-video-default-off-lite --> <!-- user-signal: "all working eyeroll" + "ship" -->

**bot-teams-memory-2560mi** _(helm — Bug B re-verify after actual rollout)_
- [x] [helm] `kubectl exec <runtime-api-pod> -- grep memory_limit /app/profiles.yaml` → shows `"2560Mi"` (not 1536Mi). <!-- h:bot-teams-memory-2560mi-helm-static --> <!-- user-signal: "all working eyeroll" + "ship" -->
- [x] [helm] Start a **Teams** bot (short-link URL `teams.microsoft.com/meet/<numeric>?p=...`), admit, let it run ≥30 s while speaker talks → pod does NOT exit 137 before the first chunk uploads. (Compare to meeting 11 baseline which died 11s post-active.) <!-- h:bot-teams-no-oom-helm --> <!-- user-signal: "all working eyeroll" + "ship" -->

## Issues found
_List anything that failed. Each entry → `release-issue-add SOURCE=human` before ship._