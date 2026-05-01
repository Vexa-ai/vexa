# 260427 — human checklist

Tick boxes. `release-ship` blocks until all are `[x]`. Bugs → `make release-issue-add SOURCE=human` (requires GAP + NEW_CHECKS).

## URLs

**lite**
- dashboard:   http://172.239.55.241:3000
- gateway:     http://172.239.55.241:8056
- admin:       http://172.239.55.241:18056
- ssh:         `ssh root@172.239.55.241`

**compose**
- dashboard:   http://172.234.192.145:3001
- /meetings:   http://172.234.192.145:3001/meetings
- /webhooks:   http://172.234.192.145:3001/webhooks
- gateway:     http://172.234.192.145:8056
- /docs:       http://172.234.192.145:8056/docs
- admin:       http://172.234.192.145:18056
- ssh:         `ssh root@172.234.192.145`

**helm**
- dashboard:   http://172.239.55.243:30001
- /meetings:   http://172.239.55.243:30001/meetings
- gateway:     http://172.239.55.243:30056
- kubectl:     `export KUBECONFIG=/home/dima/dev/vexa-260427/tests3/.state-helm/lke_kubeconfig`

## Always

**Lite VM**
- [ ] Open http://172.239.55.241:3000 → magic-link login as test@vexa.ai → /meetings renders <!-- h:f918859c -->
- [ ] `docker logs vexa-lite 2>&1 | grep -i error | tail -5` → no new errors <!-- h:9a306a4e -->
- [ ] `docker stats --no-stream vexa-lite` → MEM < 2 GiB <!-- h:a540221d -->

**Compose VM**
- [ ] Open http://172.234.192.145:3001 → magic-link login → /meetings renders <!-- h:c7191710 -->
- [ ] Open http://172.234.192.145:8056/docs → OpenAPI page renders <!-- h:873cdc56 -->
- [ ] POST /bots with a real Google Meet URL → 201 + container `meeting-*` appears in `docker ps` <!-- h:3c154567 -->
- [ ] Within 60s bot.status → active; `/transcripts/<platform>/<native_id>` returns segments <!-- h:3da4668a -->
- [ ] DELETE the bot → container gone, meeting.status=completed <!-- h:b5649b66 -->
- [ ] `docker compose -f deploy/compose/docker-compose.yml logs --tail=50 | grep -i error` → no new errors <!-- h:d80a145b -->
- [ ] Re-GET `/transcripts/...` after stop → segments still returned (post-meeting persistence) <!-- h:bfa2e8ac -->

**Helm / LKE**
- [ ] `kubectl get pods` → all Running, 0 CrashLoopBackOff <!-- h:3bcaa667 -->
- [ ] Open http://172.239.55.243:30056/ → gateway root JSON <!-- h:db401df8 -->
- [ ] Open http://172.239.55.243:30001/ → dashboard renders <!-- h:4cded063 -->
- [ ] `kubectl get events --field-selector type=Warning --sort-by=.lastTimestamp | tail` → no new warnings <!-- h:c274d6b8 -->

**Release integrity**
- [ ] Every running image tag == `cat deploy/compose/.last-tag` <!-- h:ef0fc4f8 -->
- [ ] `docker ps -a | grep -E 'lifecycle-|webhook-test|spoof-test'` → empty <!-- h:be779868 -->

## This release

**chart-deploy-shape-restored** _(helm)_
- [ ] [helm] Apply `--set global.tolerations='[{key: workload, operator: Equal, value: bot, effect: NoSchedule}]'` to a tainted-cluster install → Every Deployment + StatefulSet + Job pod schedules; zero `Pending` pods after 60 s <!-- h:4e5c0e98 -->
- [ ] [helm] Run `helm upgrade <release> <chart> --set meetingApi.image.tag=<new>` twice in sequence → Both upgrades succeed; zero `Job spec immutable` errors; migrations Job recreated as a hook <!-- h:a2fb3bea -->
- [ ] [helm] Fresh `helm install` on a cluster with provisioning-delayed PVC backend → After install completes, `mc ls vexa/` shows expected buckets; minio-init Job exited 0 <!-- h:6cc14207 -->
- [ ] [helm] Render with `--set capacityReserve.replicas=0` → Rendered output contains `replicas: 0` (zero is honoured, not silently bumped to 3) <!-- h:75eed621 -->
- [ ] [helm] From inside meeting-api: `nc -vz <release>-vexa-transcription-gateway 8084` → Connection succeeds; default-deny does not block the path <!-- h:e6fdabec -->
- [ ] [helm] `helm install vexa-prod ...` (non-default release name) → All services come up Ready; zero `getaddrinfo ENOTFOUND` log entries; `redisConfig.url` resolves to `vexa-prod-vexa-redis` <!-- h:7c4d2abb -->

**bot-pod-scheduling-and-storage-env** _(compose,helm)_
- [ ] [helm] On a cluster with a dedicated bot pool tainted `workload=bot:NoSchedule`, install with `--set global.botTolerations='[{key: workload, value: bot}]' --set global.botNodeSelector='{workload: bot}'`; dispatch a bot via meeting-api → Bot pod schedules on the bot-pool node; zero pods landing on non-bot nodes <!-- h:9ef2739b -->
- [ ] [helm] Dispatch a bot, observe `kubectl exec` env on the bot pod → Bot env contains `RECORDING_ENABLED`, `STORAGE_BACKEND`, `MEETING_API_URL`, `MINIO_BUCKET` (or `S3_*` if S3 backend); bot's `/internal/recordings/upload` calls succeed <!-- h:1bc1c7e3 -->
- [ ] [compose] Run a Zoom/Meet/Teams meeting end-to-end → After meeting completes, `mc ls vexa-recordings/...` shows ≥1 chunk for the session; `meetings.data.recordings[].media_files` populated <!-- h:55bfcf90 -->

**redis-client-robustness-layered** _(compose,helm)_
- [ ] [compose] Start meeting + bot streaming transcripts; `kubectl delete pod redis` (or `docker kill` in compose); wait for Redis to come back Ready → After Redis recovers, transcripts table writes resume within 10 s; consumer idle metric resets; no manual restart of meeting-api needed <!-- h:19e7b26e -->
- [ ] [compose] Start meeting-api with Redis NOT YET reachable (delay Redis startup by 30 s) → Meeting-api remains NotReady (`/readyz` returns 503) for 30 s; Service does not route traffic; flips to Ready within 5 s of Redis becoming reachable <!-- h:6a8e2279 -->
- [ ] [helm] Render chart with `--set redis.config.bgsaveErrorBlocksWrites=true` (attempt to force the unsafe combination) → `helm template` exits non-zero with `required` error: AOF must be enabled together with stop-writes-on-bgsave-error: no <!-- h:684e030e -->

**bot-lifecycle-orphan-pod-prevention** _(compose)_
- [ ] [compose] Dispatch 20 bots into one meeting in quick succession; sequentially DELETE each with 3 s spacing while runtime-api experiences brief connectivity blips (`docker pause runtime-api` for 5 s every 4th delete) → All 20 bot pods terminated within (last_delete + IDLE_CHECK_INTERVAL); zero pods in `Running` state with `runtime.managed=true` afterwards <!-- h:6f353366 -->
- [ ] [compose] Dispatch a browser-session bot, then `docker exec <pod> kill -9 <node-pid>` → Pod transitions to `Succeeded` (or `Completed`) within 60 s; without the entrypoint fix it would stay `Running` forever <!-- h:f25d8535 -->

**recording-durability-across-restart** _(compose)_
- [ ] [compose] Start a meeting; mid-recording, `docker restart meeting-api`; wait for restart; bot continues uploading; after meeting ends, query `meetings.data.recordings[]` → All chunks present in MinIO; `media_files: [...]` populated with all chunks; zero meetings stuck with empty `media_files` in `select * from meetings where data->>'status'='completed' and data->'recordings'->0->>'media_files'='[]';` <!-- h:8d8eb94d -->
- [ ] [compose] Start a Zoom Web meeting (or fixture'd equivalent); click Stop within 30 s → At least one chunk reaches MinIO under `recordings/.../<session_uid>/`; meeting transitions cleanly to `completed` <!-- h:8e975c9f -->
- [ ] [compose] Dispatch bot, DELETE it (status → stopping), `docker kill runtime-api` to prevent the exit-callback delivery, restart meeting-api during the would-be 90 s window, restore runtime-api → Meeting transitions to `completed` within IDLE_CHECK_INTERVAL via stale_stopping sweep (`completion_source: stale_stopping_sweep` in transition record); Prometheus alert `MeetingApiSweepStaleStoppingFiring` fires <!-- h:5d642e60 -->

**helm-prod-shape-smoke-matrix** _(helm)_
- [ ] [helm] Run `make validate-helm-prod-shape` against an ephemeral cluster; review the assertion harness output → All assertions pass: 0 Pending pods, 0 immutable-Job errors, recording chunk(s) present, JSONB populated, 0 orphan pods after 30 s-abort meeting <!-- h:920efa64 -->

**bot-pod-log-capture-compliance-coupled** _(compose)_
- [ ] [compose] Dispatch a bot, kill it with SIGKILL, query `meetings.data.bot_logs` after 30 s → JSONB array populated with structured records; final entries include the SIGKILL signal capture from `--previous` <!-- h:c7754d27 -->
- [ ] [compose] Run a meeting with verbose bot logging; verify `meetings.data.bot_logs` JSONB stays under 50 KB → Truncation marker present; head-N + tail-N preserved; total under 50 KB <!-- h:7cc741ef -->
- [ ] [compose] Trigger data-retention purge against an aged meeting → `bot_logs` JSONB purged with the meetings row; no orphan log artifacts <!-- h:c3c1e1fb -->

**post-meeting-aggregation-state-machine** _(compose)_
- [ ] [compose] Run a meeting end-to-end; mid-aggregation, `docker restart transcription-gateway`; observe meeting status + JSONB → Either retries succeed (status `completed`, no aggregation_failure_class set) OR meeting goes to status=`failed` WITH `data.aggregation_failure_class='transient_infra'` AND `meeting.aggregation_failed` event fires (NOT `meeting.failed`). No PG schema migration ran during the test. <!-- h:9b9d53a9 -->
- [ ] [compose] Stress: hold tx-gateway down for 8 days, observe idle_loop sweep behavior → Meeting initially has `data.aggregation_failure_class='transient_infra'`; after 7-day retry budget, flips to `data.aggregation_failure_class='permanent_infra'`; status remains `failed` throughout; `MeetingApiAggregationGivenUp` critical alert fires <!-- h:dd65b0d0 -->

**chart-availability-defaults-stateless** _(helm)_
- [ ] [helm] Render with default values; `helm template ... | grep -c 'replicas: [1-9]'` per stateless Deployment → Every stateless Deployment renders with `replicas: ≥ 2`; every one has a sibling PDB with `minAvailable: 1`; every one has `readinessProbe` + `livenessProbe` blocks <!-- h:85817ede -->
- [ ] [helm] Drain the node hosting a stateless service (e.g. meeting-api) → Service stays available throughout drain; one pod always Ready; zero 5xx during the drain window <!-- h:74002e3a -->

**bot-exit-classification-refinement** _(compose)_
- [ ] [compose] Dispatch a bot, kill it mid-active with SIGKILL (simulate OOM); wait for meeting to terminalize; query DB → Meeting status='failed', `data.completion_reason`='OOM_KILLED' or 'BOT_CRASHED'; NOT 'completed' <!-- h:0f954305 -->
- [ ] [compose] Dispatch a bot to a meeting where bot's admission flow fails (test fixture: admit button not present); wait for terminal state → Meeting status='failed', `data.completion_reason`='ADMISSION_BUTTON_NOT_FOUND'; NOT 'completed' <!-- h:1c23652a -->
- [ ] [compose] Run a clean meeting end-to-end (segments produced, clean exit); query DB → Meeting status='completed' (NOT 'failed' per #233); `data.completion_reason`='STOPPED' or unset <!-- h:27bf00b6 -->

**browser-session-idle-eviction** _(compose)_
- [ ] [compose] Dispatch a browser-session bot, do NOT `/touch` it for 70 min (test override of idle_timeout to 60s + buffer) → Pod transitions to Succeeded/Completed within idle_timeout + 5×IDLE_CHECK_INTERVAL; no manual cleanup needed <!-- h:9eb80c31 -->
- [ ] [compose] Manually delete a browser-session entry from Redis state index while leaving the K8s pod running → Within IDLE_CHECK_INTERVAL, reconcile_browser_sessions sweep reaps the orphan pod; `runtime_api_browser_session_pods_orphaned_total` metric increments; alert fires <!-- h:b829335d -->

**slim-meeting-list-endpoint** _(compose)_
- [ ] [compose] Populate test fixture with 50 meetings; `curl -H 'X-API-Key: $TOKEN' http://meeting-api/bots?limit=50 | wc -c` → Response < 100 KB total (~2 KB per meeting); zero `status_transition[]`/`recordings[]`/`webhook_deliveries[]` arrays in response <!-- h:ef2d8ee5 -->
- [ ] [compose] Same request with `?include=data` → Full blob backward-compat path returns ~35 KB per meeting <!-- h:e11e3b25 -->
- [ ] [compose] GET /bots/id/<id> for one meeting → Detail endpoint returns full `m.data` JSONB unchanged <!-- h:480791b0 -->

**bot-server-contract-parity-checks** _(compose,helm,lite)_
- [ ] [lite] Rename a field in `MeetingCreate` Pydantic schema (e.g. `voice_agent_enabled` → `tts_enabled`); run the contract-parity check → Check fails with diff showing bot-side `BotConfig` is now out of sync; PR cannot merge <!-- h:a7285e06 -->

**make-all-non-interactive-shell** _(compose)_
- [ ] [compose] From a fresh clone in a non-interactive shell: `make env TRANSCRIPTION=cpu && make all < /dev/null > make.log 2>&1 &`; wait 15 minutes → Process exits cleanly within 10 minutes; `make.log` does not grow unbounded; no `Invalid Google Meet ID format` spam <!-- h:0480fb0e -->

**synthetic-bot-test-rig** _(compose,lite)_
- [ ] [compose] On a running compose stack: `BASE=http://localhost:8056 bash tests3/synthetic/run-all.sh` → All 4 scenarios pass; report at .state-compose/reports/compose/synthetic.json shows status=pass <!-- h:dba9669b -->
- [ ] [compose] Verify endpoint gating: with VEXA_ENV=production, `curl -X POST http://localhost:8056/bots/internal/test/session-bootstrap -d '{}'` returns 404 → 404 Not Found <!-- h:218a3f3b -->

## Issues found
_List anything that failed. Each entry → `release-issue-add SOURCE=human` before ship._