# Full Validation Plan

Reproducible procedure to validate all features, services, and deployments.

**Full retest** — every DoD item across all READMEs gets re-validated, not just UNTESTED ones. After each test passes, immediately update the referenced DoD row (Status, Evidence, Last checked). PASS items get fresh dates. Logs in `tests3/.state/` are the source of truth.

## Prerequisites

Three deployment modes must be validated:

| Mode | Container | Gateway | Dashboard | Status |
|------|-----------|---------|-----------|--------|
| Helm/LKE | K8s cluster | http://{LKE_NODE_IP}:30056 | http://{LKE_NODE_IP}:30001 | `make -C tests3 lke-status` |
| Lite | `vexa` | http://localhost:8056 | http://localhost:3000 | `docker ps \| grep vexa` |
| Compose | `vexa-*` | http://localhost:8056 | http://localhost:3001 | `cd deploy/compose && make ps` |

Required files:
- Production DB dump: `~/dev/2/secrets/production-dump.sql` (101 MB)
- `.env` with TRANSCRIPTION_SERVICE_URL and TRANSCRIPTION_SERVICE_TOKEN set

Test user: `test@vexa.ai` (user_id=1523, `max_concurrent_bots=10`). Use this user for all deployments unless creating speaker bots (which get throwaway users). After DB restore, create a token for this user:
```bash
curl -sf -X POST "$GATEWAY/admin/users/1523/tokens?scopes=bot,browser,tx&name=tests3" \
  -H "X-Admin-API-Key: $ADMIN_TOKEN" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])"
```

---

## Parallel execution plan

Compose and Lite share localhost:8056 so they must be sequential. Helm runs on LKE (remote) and is fully independent.

```
TIME ──────────────────────────────────────────────────────────────────────────►

LANE 1 — Helm (LKE, remote)        LANE 2 — Local (Compose, then Lite)     LANE 3 — Human
─────────────────────────────       ────────────────────────────────────     ──────────────────

[1c] verify helm images pulled      [1a] compose pull + up + test
  → DoD: helm#3                       → DoD: compose#12                     (wait)
         │                                      │
[3c] lke-load-db (dump)             [2a] compose build + up + test
  → DoD: helm#5                       → DoD: compose#2                      (wait)
         │                                      │
[4a] helm smoke                     [3a] compose restore-db + test          [6] check compose dashboard
  → DoD: helm#10                      → DoD: compose#13                          (while tests run)
[4a] helm dashboard                          │
[4a] helm containers                [4c] compose smoke/dashboard/
[4a] helm webhooks                       containers/webhooks
[4a] helm browser-session             → DoD: per feature                    (wait)
[4a] helm auth-meeting                       │
  → DoD: per feature               compose down
         │                                      │
[6] check helm dashboard            [1b] lite pull + up + test
         │                            → DoD: lite#2                         (wait)
[5a] Teams meeting ◄──── human admits bots ◄───────────────────── HUMAN
  → DoD: speaking-bot, realtime-tx,          │
         meeting-chat, post-tx      [2b] lite build + up + test
                                      → DoD: lite#1                         [6] check lite dashboard
(helm lane done)                             │
                                    [3b] lite restore-db + test
                                      → DoD: lite#6                         (wait)
                                             │
                                    [4b] lite smoke/dashboard/
                                         containers
                                      → DoD: lite#12, per feature           (wait)
                                             │
                                    [5b] GMeet meeting ◄────────── HUMAN admits bot
                                      → DoD: lite#7, speaking-bot,
                                             realtime-tx, meeting-chat
                                             │
                                    lite down
                                    (all lanes done)

                                    [7] verify + commit
```

**Key constraints:**
- Compose and Lite cannot run simultaneously (port 8056 conflict)
- Helm lane is fully independent — start it first, it takes longest (LKE + DB load)
- Teams meeting test (5a) runs on helm while local switches from compose to lite
- GMeet meeting test (5b) runs on lite at the end
- Human dashboard checks (Phase 6) fit into gaps while automated tests run

**Estimated wall-clock time:** ~45 min with parallelism (vs ~90 min sequential)

---

## DoD cross-reference

Which validation step updates which DoD row. Update the row immediately when the step passes.

### deploy/compose/README.md

| DoD# | Item                                           | Validated by |
|------|------------------------------------------------|--------------|
| 1    | `make all` from clean clone                    | Phase 1a     |
| 2    | `make build` produces VERSION-YYMMDD-HHMM tags | Phase 2a     |
| 3    | `make up` starts all healthy                   | Phase 1a     |
| 12   | Pre-built images (skip build)                  | Phase 1a     |
| 13   | restore-db works                               | Phase 3a     |

### deploy/lite/README.md

| DoD# | Item                                     | Validated by |
|------|------------------------------------------|--------------|
| 1    | `docker build` produces working image    | Phase 2b     |
| 2    | Pre-built image pulls and starts         | Phase 1b     |
| 3    | 14 supervisor services running           | Phase 1b     |
| 5    | Post-startup self-check reports healthy  | Phase 1b     |
| 6    | DB restore from dump                     | Phase 3b     |
| 7    | Bots join meetings + produce transcripts | Phase 5b     |
| 9    | Dashboard on port 3000                   | Phase 6      |
| 12   | Smoke checks pass                        | Phase 4b     |

### deploy/helm/README.md

| DoD# | Item                            | Validated by |
|------|---------------------------------|--------------|
| 1    | vexa chart installs on K8s      | Phase 4a     |
| 3    | Images pulled from registry     | Phase 1c     |
| 4    | Images built + pushed work      | Phase 2c     |
| 5    | DB load from dump               | Phase 3c     |
| 10   | Smoke checks pass               | Phase 4a     |

### features/infrastructure/README.md (6 items, all UNTESTED)

| DoD# | Item | Validated by |
|------|------|--------------|
| 1 | make build produces immutable tagged images | 2a compose build |
| 2 | make up starts all services healthy | 1a compose pull, 4 smoke |
| 3 | Gateway, admin, dashboard respond | 4 smoke, dashboard |
| 4 | Transcription service has GPU | 4 smoke health checks |
| 5 | Database migrated and accessible | 3 db restore, 4 smoke |
| 6 | MinIO bucket exists | 4a browser-session |

### features/auth-and-limits/README.md (6 items, 4 UNTESTED, 2 SKIP)

| DoD# | Item | Validated by |
|------|------|--------------|
| 1 | API rejects requests without valid token | 4 smoke contracts |
| 2 | Token scopes enforced (bot, browser, tx) | 4 smoke contracts |
| 3 | Concurrent bot limit enforced | 4 containers |
| 4 | Rate limiting works (429 on excess) | SKIP — no test |
| 5 | Token create/revoke via admin API | SKIP — no test |
| 6 | Dashboard token has correct scopes | 4 dashboard |

### features/dashboard/README.md (15 items, 1 FAIL, rest PASS)

| DoD# | Item | Validated by |
|------|------|--------------|
| 1 | Dashboard container reaches all backends | 4 smoke |
| 2 | No false "failed" for successful meetings | 4 dashboard — currently FAIL |
| 3 | Magic link login returns 200 + sets cookie | 4 dashboard-auth |
| 4 | Meetings list loads with auth cookie | 4 dashboard-proxy |
| 5 | GET /meetings/{id} returns native_meeting_id | 4 dashboard-proxy |
| 6 | Transcript via proxy returns segments | 4 dashboard-proxy |
| 7 | Meeting page renders transcript in browser | 6 human dashboard check |
| 8 | Meeting page shows correct status | 4 dashboard-proxy |
| 9 | Cache headers prevent stale JS bundles | 4 smoke |
| 10 | Dashboard credentials valid | 4 dashboard-auth |
| 11 | Platform icons render | 6 human dashboard check |
| 12 | Meetings list paginates | 4 dashboard-proxy |
| 13 | Login as email X shows user X | 4 dashboard-auth |
| 14 | After login, redirects to /meetings | 4 smoke static |
| 15 | Bot creation through dashboard returns bot | 4 dashboard-proxy |

### features/meeting-urls/README.md (9 items, all PASS)

| DoD# | Item | Validated by |
|------|------|--------------|
| 1-9 | URL parsing (GMeet, Teams x6, direct POST, invalid) | 4 smoke static |

### features/browser-session/README.md (18 items, all PASS)

| DoD# | Item | Validated by |
|------|------|--------------|
| 1-9 | Session create, CDP, S3 sync, save, cookies, auto-save, roundtrip, locks, auth config | 4a browser-session |
| 10-11 | Google login persists, meet.new works | 4a browser-login (human gate) |
| 12-18 | Graceful shutdown, idle timeout, keep-alive, gateway touch, profile, transitions | 4a browser-session, containers |

### features/remote-browser/README.md (6 items, 5 UNTESTED)

| DoD# | Item | Validated by |
|------|------|--------------|
| 1 | Browser session creates and container runs | 4a browser-session |
| 2 | CDP accessible through gateway proxy | 4a browser-session |
| 3 | Login state persists across sessions | 4a browser-session |
| 4 | VNC accessible via dashboard | 4a browser-session, 6 human |
| 5 | Container stops cleanly on DELETE /bots | 4a browser-session |
| 6 | No orphan containers after session ends | 4 containers |

### features/container-lifecycle/README.md (15 items, 1 UNTESTED)

| DoD# | Item | Validated by |
|------|------|--------------|
| 1-14 | Create, remove, orphans, limits, idle timeout, touch, callback, reconciliation, consumer-managed, browser idle/keepalive, gateway touch, profile values, no :latest | 4 containers |
| 15 | Profiles propagate correctly to K8s | 4a helm containers |

### features/bot-lifecycle/README.md (14 items, 1 UNTESTED, 1 SKIP)

| DoD# | Item | Validated by |
|------|------|--------------|
| 1-8 | Create, active, delete, status, timeout, platforms, no false fail, auto-admit | 4 containers, 5 meeting-tts |
| 9 | Unauthenticated GMeet join (name prompt) | 5b gmeet meeting-tts |
| 10 | meeting_url parsed server-side | 4 smoke static |
| 11 | needs_human_help escalation | SKIP — no test infra |
| 12-14 | Exit=completed, concurrency release, status transitions | 4 containers |

### features/authenticated-meetings/README.md (10 items, 1 FAIL)

| DoD# | Item | Validated by |
|------|------|--------------|
| 1-4 | S3 config, cookie download, "Join now", account identity | 4a auth-meeting |
| 5 | Fallback: expired cookies → anonymous join | 4a auth-meeting — currently FAIL |
| 6-9 | Password store, S3 path, diagnostic screenshot, schema field | 4a auth-meeting |
| 10 | (if exists) | 4a auth-meeting |

### features/webhooks/README.md (6 items, all UNTESTED)

| DoD# | Item | Validated by |
|------|------|--------------|
| 1 | POST_MEETING_HOOKS configured and fires | 4a, 4c webhooks |
| 2 | Webhook envelope has correct shape | 4a, 4c webhooks |
| 3 | HMAC signing works when secret provided | 4a, 4c webhooks |
| 4 | Delivery logged (success or failure) | 4a, 4c webhooks |
| 5 | No internal fields leaked in payload | 4a, 4c webhooks |
| 6 | webhook_secret not leaked in API responses | 4a, 4c webhooks |

### features/speaking-bot/README.md (5 items, 3 UNTESTED, 2 SKIP)

| DoD# | Item | Validated by |
|------|------|--------------|
| 1 | POST /speak returns 202 and bot speaks | 5a, 5b meeting-tts |
| 2 | Other participants hear the speech | 5a, 5b human verifies |
| 3 | Multiple voices distinguishable | SKIP — no test |
| 4 | Interrupt stops playback | SKIP — no test |
| 5 | Works on GMeet and Teams | 5a + 5b both platforms |

### features/realtime-transcription/README.md (7 items, 5 UNTESTED, 1 SKIP)

| DoD# | Item | Validated by |
|------|------|--------------|
| 1 | Google Meet confidence >= 70 | 5b gmeet meeting-tts |
| 2 | MS Teams confidence >= 70 | 5a teams meeting-tts |
| 3 | WS delivery matches REST | 4 smoke contracts |
| 4 | Zoom confidence >= 50 | SKIP — not implemented |
| 5 | Rapid speaker alternation >= 75% | 5a, 5b meeting-tts |
| 6 | Live WS transcript non-empty during meeting | 5a, 5b human verifies |
| 7 | Dashboard renders REST-loaded transcript | 6 human dashboard check |

### features/realtime-transcription/gmeet/README.md (6 items)

| DoD# | Item | Validated by |
|------|------|--------------|
| 1-6 | Join, speaker attribution, content match, no hallucinations, completeness, DOM selectors | 5b gmeet meeting-tts |

### features/realtime-transcription/msteams/README.md (8 items, 2 SKIP)

| DoD# | Item | Validated by |
|------|------|--------------|
| 1-3 | Join, speaker attribution, content match | 5a teams meeting-tts |
| 4 | No missed GT lines under stress | SKIP — needs 20+ utterances |
| 5 | No hallucinated segments | 5a teams meeting-tts |
| 6-7 | Speaker transitions, Teams URL formats | 5a teams meeting-tts, 4 smoke |
| 8 | Overlapping speech | SKIP — needs multi-speaker |

### features/realtime-transcription/zoom/README.md (5 items, all UNKNOWN)

All items SKIP — Zoom not implemented.

### features/meeting-chat/README.md (4 items, all UNTESTED)

| DoD# | Item | Validated by |
|------|------|--------------|
| 1 | POST /chat sends message | 5a, 5b meeting-tts |
| 2 | GET /chat returns messages | 5a, 5b meeting-tts |
| 3 | Works on GMeet and Teams | 5a + 5b both platforms |
| 4 | Chat persisted after meeting ends | 5a, 5b post-meeting |

### features/post-meeting-transcription/README.md (5 items, 3 UNTESTED)

| DoD# | Item | Validated by |
|------|------|--------------|
| 1 | Recording uploaded to MinIO | 5a, 5b meeting-tts finalize |
| 2 | POST /meetings/{id}/transcribe returns segments | 5a, 5b meeting-tts finalize |
| 3 | Speaker names attributed | 5a, 5b meeting-tts finalize |
| 4 | Deferred segments consistent with realtime | 5a, 5b meeting-tts finalize |
| 5 | Works for GMeet and Teams | 5a + 5b both platforms |

---

## Phase 1: Deploy — Pull (pre-built images)

Validate that each deployment mode works with **pre-built images from DockerHub** (the default `make up` path).

### 1a. Compose — pull

```bash
cd deploy/compose
make env                          # create .env from template
# Ensure IMAGE_TAG=dev in .env (default — pulls from DockerHub)
rm -f .last-tag                   # ensure no local build override
make up 2>&1 | tee tests3/.state/log-compose-pull-up.txt
make test 2>&1 | tee tests3/.state/log-compose-pull-test.txt
```

**Human check**: verify `docker images | grep vexaai` shows `:dev` tags (pulled, not locally built).

**Update DoDs now:**
- `deploy/compose/README.md` #12 Pre-built images → PASS, #3 `make up` starts all healthy → PASS
- `features/infrastructure/README.md` #2 make up starts all services → PASS, #3 Gateway/admin/dashboard respond → PASS, #4 Transcription service → PASS (if `make test` transcription check passed)

### 1b. Lite — pull

```bash
docker run -d --name vexa-lite \
  --env-file .env \
  -p 8056:8056 -p 3000:3000 \
  vexaai/vexa-lite:dev
# Wait ~30s for startup
curl -sf http://localhost:8056/health 2>&1 | tee tests3/.state/log-lite-pull-health.txt
```

**Update DoDs now:**
- `deploy/lite/README.md` #2 Pre-built image pulls and starts → PASS, #3 14 services running → PASS, #5 Post-startup self-check → PASS

### 1c. Helm — pull

Helm always pulls from DockerHub. If LKE is already running:

```bash
make -C tests3 lke-status
kubectl get pods -o jsonpath='{range .items[*]}{.spec.containers[*].image}{"\n"}{end}' \
  2>&1 | tee tests3/.state/log-helm-pull-images.txt
```

**Update DoDs now:**
- `deploy/helm/README.md` #3 Images pulled from registry → PASS

---

## Phase 2: Deploy — Build (from source)

### 2a. Compose — build

```bash
cd deploy/compose
make build 2>&1 | tee tests3/.state/log-compose-build.txt
make up 2>&1 | tee tests3/.state/log-compose-build-up.txt
make test 2>&1 | tee tests3/.state/log-compose-build-test.txt
```

**Human check**: verify `cat deploy/compose/.last-tag` shows a fresh timestamp (e.g. `0.0.0-260408-1430`).

**Update DoDs now:**
- `deploy/compose/README.md` #2 `make build` produces VERSION-YYMMDD-HHMM tags → PASS (evidence: tag from .last-tag)
- `features/infrastructure/README.md` #1 make build produces immutable tagged images → PASS

### 2b. Lite — build

```bash
cd deploy/lite
make build 2>&1 | tee tests3/.state/log-lite-build.txt
docker run -d --name vexa-lite \
  --env-file ../../.env \
  -p 8056:8056 -p 3000:3000 \
  vexa-lite:dev
curl -sf http://localhost:8056/health 2>&1 | tee tests3/.state/log-lite-build-health.txt
```

**Update DoDs now:**
- `deploy/lite/README.md` #1 `docker build` produces working image → PASS

### 2c. Helm — build (optional, compose images work)

Helm uses the same images as compose. After `make -C deploy/compose build && make -C deploy/compose publish`:

```bash
TAG=$(cat deploy/compose/.last-tag)
helm upgrade vexa deploy/helm/charts/vexa --set image.tag=$TAG --wait
kubectl get pods -o jsonpath='{range .items[*]}{.spec.containers[*].image}{"\n"}{end}' \
  2>&1 | tee tests3/.state/log-helm-build-images.txt
```

**Update DoDs now:**
- `deploy/helm/README.md` #4 Images built + pushed work → PASS

---

## Phase 3: DB migrate from dump

Dump file: `~/dev/2/secrets/production-dump.sql` (101 MB, Supabase-origin Postgres)

### 3a. Compose — restore-db

```bash
cd deploy/compose
make restore-db DUMP=~/dev/2/secrets/production-dump.sql \
  2>&1 | tee tests3/.state/log-compose-restore-db.txt
make test 2>&1 | tee tests3/.state/log-compose-restore-db-test.txt
```

**Human check**: open dashboard at http://localhost:3001 — verify real meetings and transcripts appear.

**Update DoDs now:**
- `deploy/compose/README.md` #13 restore-db works → PASS (evidence: row counts from log)
- `features/infrastructure/README.md` #5 Database migrated and accessible → PASS

### 3b. Lite — restore-db

Lite uses an external Postgres. Load the dump into the Postgres instance that lite connects to:

```bash
PGPASSWORD=$DB_PASSWORD psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
sed '/^\\restrict/d; /^\\set/d; /^ALTER.*OWNER TO "supabase/d; /^GRANT.*supabase/d; /^REVOKE.*supabase/d' \
  ~/dev/2/secrets/production-dump.sql | \
  PGPASSWORD=$DB_PASSWORD psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -q \
  2>&1 | tee tests3/.state/log-lite-restore-db.txt
docker restart vexa-lite
curl -sf http://localhost:8056/health 2>&1 | tee tests3/.state/log-lite-restore-db-health.txt
```

**Human check**: open dashboard at http://localhost:3000 — verify real meetings and transcripts appear.

**Update DoDs now:**
- `deploy/lite/README.md` #6 DB restore from dump → PASS

### 3c. Helm — lke-load-db

```bash
make -C tests3 lke-load-db DUMP_FILE=~/dev/2/secrets/production-dump.sql \
  2>&1 | tee tests3/.state/log-helm-load-db.txt
```

Drops/recreates DB, loads dump, verifies row counts, restarts services, re-bootstraps credentials.

**Human check**: open dashboard at http://{LKE_NODE_IP}:30001 — verify real meetings and transcripts appear.

**Update DoDs now:**
- `deploy/helm/README.md` #5 DB load from dump → PASS (evidence: row counts from log)

---

## Phase 4: Automated tests — all deployments

Run the full automated suite. After each target passes, update the relevant feature DoD rows.

### 4a. Helm

```bash
make -C tests3 clean
make -C tests3 smoke 2>&1 | tee tests3/.state/log-helm-smoke.txt
make -C tests3 dashboard 2>&1 | tee tests3/.state/log-helm-dashboard.txt
make -C tests3 containers 2>&1 | tee tests3/.state/log-helm-containers.txt
make -C tests3 webhooks 2>&1 | tee tests3/.state/log-helm-webhooks.txt
make -C tests3 browser-session 2>&1 | tee tests3/.state/log-helm-browser.txt
make -C tests3 auth-meeting 2>&1 | tee tests3/.state/log-helm-auth.txt
```

**Update DoDs now** (after each target, not after all):
- After smoke: `deploy/helm/README.md` #10 → PASS. `features/infrastructure/README.md` #3, #4, #6. `features/auth-and-limits/README.md` #1, #2. `features/meeting-urls/README.md` #1-9. `features/dashboard/README.md` #1, #9, #14.
- After dashboard: `features/dashboard/README.md` #2-8, #10, #12-13, #15. `features/auth-and-limits/README.md` #6.
- After containers: `features/container-lifecycle/README.md` #1-15. `features/bot-lifecycle/README.md` #1-8, #12-14. `features/auth-and-limits/README.md` #3.
- After webhooks: `features/webhooks/README.md` #1-6.
- After browser-session: `features/browser-session/README.md` #1-9, #12-18. `features/remote-browser/README.md` #1-6.
- After auth-meeting: `features/authenticated-meetings/README.md` #1-9.

### 4b. Lite

```bash
export DEPLOY_MODE=lite GATEWAY_URL=http://localhost:8056 DASHBOARD_URL=http://localhost:3000
make -C tests3 clean
make -C tests3 smoke 2>&1 | tee tests3/.state/log-lite-smoke.txt
make -C tests3 dashboard 2>&1 | tee tests3/.state/log-lite-dashboard.txt
make -C tests3 containers 2>&1 | tee tests3/.state/log-lite-containers.txt
```

**Update DoDs now** (after each target):
- After smoke: `deploy/lite/README.md` #12 → PASS. Re-confirm `features/infrastructure/README.md` #3, `features/auth-and-limits/README.md` #1-2.
- After dashboard: Re-confirm `features/dashboard/README.md` #3-6, #8, #10, #12-13, #15.
- After containers: Re-confirm `features/container-lifecycle/README.md` #1-14, `features/bot-lifecycle/README.md` #1-8, #12-14.

### 4c. Compose

```bash
cd deploy/compose && make up
export DEPLOY_MODE=compose
make -C tests3 clean
make -C tests3 smoke 2>&1 | tee tests3/.state/log-compose-smoke.txt
make -C tests3 dashboard 2>&1 | tee tests3/.state/log-compose-dashboard.txt
make -C tests3 containers 2>&1 | tee tests3/.state/log-compose-containers.txt
make -C tests3 webhooks 2>&1 | tee tests3/.state/log-compose-webhooks.txt
cd deploy/compose && make down
```

**Update DoDs now** (after each target):
- After smoke: Re-confirm `features/infrastructure/README.md` #3, `features/auth-and-limits/README.md` #1-2.
- After dashboard: Re-confirm `features/dashboard/README.md` #3-6, #8, #10, #12-13, #15.
- After containers: Re-confirm `features/container-lifecycle/README.md` #1-14, `features/bot-lifecycle/README.md` #1-8, #12-14.
- After webhooks: Re-confirm `features/webhooks/README.md` #1-6.

---

## Phase 5: Platform transcript tests (human in the loop)

A human must be present to admit bots and verify audio/transcripts.

### What the human needs to do

1. **Before starting**: have a Teams meeting URL ready. For Google Meet, open a new meeting in the browser.
2. **During the test**: the script sends bots to the meeting. The human must:
   - Watch for bots appearing in the meeting lobby
   - **Admit all bots** when they appear
   - Stay in the meeting for ~2 minutes while TTS audio plays
   - Confirm you can hear the TTS audio (robot voice speaking test phrases)
3. **After the test**: the script checks for transcripts automatically. The human verifies in the dashboard.

### 5a. Teams meeting test

```bash
echo "https://teams.microsoft.com/l/meetup-join/..." > tests3/.state/meeting_url

TEAMS_MEETING_URL="$(cat tests3/.state/meeting_url)" \
  make -C tests3 meeting-tts-teams 2>&1 | tee tests3/.state/log-teams-transcript.txt
```

Human checklist:
- [ ] Bots appear in Teams lobby
- [ ] Admitted all bots
- [ ] TTS audio audible in meeting
- [ ] Transcript appears in dashboard with speaker labels
- [ ] Transcript text roughly matches TTS phrases

**Update DoDs now:**
- `features/speaking-bot/README.md` #1 POST /speak → PASS, #2 participants hear speech → PASS
- `features/realtime-transcription/README.md` #2 Teams confidence >= 70 → PASS/FAIL, #6 live WS non-empty → PASS/FAIL
- `features/realtime-transcription/msteams/README.md` #1-3, #5-7 → PASS/FAIL per results
- `features/meeting-chat/README.md` #1-4 → PASS/FAIL per results
- `features/post-meeting-transcription/README.md` #1-5 → PASS/FAIL per results

### 5b. Google Meet test

```bash
make -C tests3 meeting-tts 2>&1 | tee tests3/.state/log-gmeet-transcript.txt
```

Human checklist:
- [ ] Bot joins Google Meet (via CDP browser)
- [ ] Admitted bot when prompted
- [ ] TTS audio audible in meeting
- [ ] Transcript appears in dashboard with speaker labels
- [ ] Transcript text roughly matches TTS phrases

**Update DoDs now:**
- `deploy/lite/README.md` #7 Bots join + transcripts → PASS
- `features/speaking-bot/README.md` #5 Works on GMeet and Teams → PASS (both platforms now tested)
- `features/realtime-transcription/README.md` #1 GMeet confidence >= 70 → PASS/FAIL
- `features/realtime-transcription/gmeet/README.md` #1-6 → PASS/FAIL per results
- `features/bot-lifecycle/README.md` #9 Unauthenticated GMeet join → PASS/FAIL
- Re-confirm meeting-chat, post-meeting-transcription on second platform

### 5c. Cross-platform comparison

| Check | Teams | Google Meet |
|-------|-------|-------------|
| Bot joined | [ ] | [ ] |
| TTS heard | [ ] | [ ] |
| Transcript rendered | [ ] | [ ] |
| Speaker labels correct | [ ] | [ ] |
| Latency acceptable (<30s) | [ ] | [ ] |

---

## Phase 6: Human dashboard validation

Open each dashboard URL in a browser. Confirms what automated tests claim.

- [ ] Dashboard loads without errors
- [ ] Login works (admin key or magic link)
- [ ] Meetings list visible with past meetings (real data from dump if loaded)
- [ ] Click a meeting — transcript renders with speaker labels
- [ ] Live status updates (if a bot is active)

| Mode | URL | Verified |
|------|-----|----------|
| Helm | http://{LKE_NODE_IP}:30001 | [ ] |
| Lite | http://localhost:3000 | [ ] |
| Compose | http://localhost:3001 | [ ] |

**Update DoDs now:**
- `deploy/lite/README.md` #9 Dashboard on port 3000 → PASS
- `features/dashboard/README.md` #7 Meeting page renders transcript → PASS, #11 Platform icons render → PASS
- `features/realtime-transcription/README.md` #7 Dashboard renders REST-loaded transcript → PASS

---

## Phase 7: Verify and commit

```bash
make -C tests3 docs                # doc drift checks pass
make -C tests3 locks               # static regression locks pass
grep -r 'UNTESTED' features/*/README.md  # should only show genuinely untested items
grep -r 'UNTESTED' deploy/*/README.md    # should be empty after full run
```

Remaining fixes:
- [ ] Dashboard feature: change "Confidence target: 90" to actual score
- [ ] Browser-session: mark known bug #1 as resolved (idle timeout implemented)
- [ ] Main README: verify all scores still match individual READMEs

Commit all changes.

---

## State log reference

| Log file | Phase | What it proves |
|----------|-------|----------------|
| `log-compose-pull-up.txt` | 1a | Compose starts with pre-built images |
| `log-compose-pull-test.txt` | 1a | Services healthy after pull |
| `log-lite-pull-health.txt` | 1b | Lite starts with pre-built image |
| `log-helm-pull-images.txt` | 1c | Helm pods use correct image tags |
| `log-compose-build.txt` | 2a | Compose images build from source |
| `log-compose-build-up.txt` | 2a | Compose starts with locally-built images |
| `log-compose-build-test.txt` | 2a | Services healthy after build |
| `log-lite-build.txt` | 2b | Lite image builds from source |
| `log-lite-build-health.txt` | 2b | Lite starts with locally-built image |
| `log-helm-build-images.txt` | 2c | Helm pods run locally-built images |
| `log-compose-restore-db.txt` | 3a | Compose DB restore from dump |
| `log-compose-restore-db-test.txt` | 3a | Services healthy with production data |
| `log-lite-restore-db.txt` | 3b | Lite DB restore from dump |
| `log-lite-restore-db-health.txt` | 3b | Lite healthy with production data |
| `log-helm-load-db.txt` | 3c | Helm DB load + credential re-bootstrap |
| `log-helm-smoke.txt` | 4a | Helm smoke checks |
| `log-helm-dashboard.txt` | 4a | Helm dashboard tests |
| `log-helm-containers.txt` | 4a | Helm container lifecycle |
| `log-helm-webhooks.txt` | 4a | Helm webhook validation |
| `log-helm-browser.txt` | 4a | Helm browser session |
| `log-helm-auth.txt` | 4a | Helm authenticated meetings |
| `log-lite-smoke.txt` | 4b | Lite smoke checks |
| `log-lite-dashboard.txt` | 4b | Lite dashboard tests |
| `log-lite-containers.txt` | 4b | Lite container lifecycle |
| `log-compose-smoke.txt` | 4c | Compose smoke checks |
| `log-compose-dashboard.txt` | 4c | Compose dashboard tests |
| `log-compose-containers.txt` | 4c | Compose container lifecycle |
| `log-compose-webhooks.txt` | 4c | Compose webhook validation |
| `log-teams-transcript.txt` | 5a | Teams platform e2e transcript |
| `log-gmeet-transcript.txt` | 5b | Google Meet platform e2e transcript |

---

## Test observations (2026-04-08)

### Meeting 9835 — GMeet recorder (Phase 5b, lite)
- Created 16:46, bots admitted, reached active
- Segments stored in Redis: yes (1 segment at 16:47:05)
- Container restart at ~16:48 killed Redis → `Error 111 connecting to localhost:6379`
- Bot entered stopping loop: `[Delayed Stop] Waiting 90s for container 8274` repeating
- Invalid transition `stopping → stopping` at 17:12
- **Never completed** — Redis was down when delayed stop tried to finalize

### Meetings 9838-9840 — GMeet multi-bot TTS (Phase 5b, lite)
- 3 bots (recorder + Alice + Bob), all reached active at 16:51
- Segments stored in Redis: yes — multiple segments per bot from 16:56 to 16:57
- TTS sent and heard by human
- After DELETE: bots stopped, no recording upload (bots DELETE'd, not graceful leave)
- 0 segments via REST — queried before db_writer flushed (30s immutability + 10s interval)

### Meeting 9841 — Teams single bot + human (lite retest)
- Created 17:10, admitted at 17:10:43
- Segments stored in Redis: yes — 8 segments from 17:11:01 to 17:11:56
- `[Delayed Stop] Waiting 90s` at 17:12:06
- Bot callback arrived, post-meeting ran at 17:12:57 (50s after stop)
- `Aggregated transcription data for meeting 9841` — success
- **Completed.** Transcription PASS. Dashboard showed transcript during meeting.
- Warning: `completed → completed` (delayed stop safety net fired after callback)

### Meeting 9842 — GMeet single bot + human (lite retest)
- Created 17:14:56, reached active
- Segments stored in Redis: yes — 7 segments from 17:15:26 to 17:15:57
- `[Delayed Stop] Waiting 90s` at 17:15:57
- **Recording upload failed 3 times**: `Could not connect to http://minio:9000` (500)
- Post-meeting ran at 17:16:47, aggregation succeeded
- **Completed.** Transcription PASS, recording FAIL (MinIO DNS).

### K8s (helm) — Teams multi-bot TTS (Phase 5a)
- 3 bots joined, TTS 4/4 sent, human heard audio
- Script saw 4 segments inline during active meeting
- After bot stop: 0 segments via REST, meetings not queryable
- Bots stuck in "stopping" — did not reach completed
- **Root cause**: K8s-specific — delayed stop task lost on pod restart, exit callback not fired from DELETE endpoint

### Summary

| Issue | Meetings | Root cause | File |
|-------|----------|------------|------|
| 90s Delayed Stop blocks UX | All | `BOT_STOP_DELAY_SECONDS=90` by design | `config.py:28` |
| Recording upload fails (lite) | 9841, 9842 | `MINIO_ENDPOINT=http://minio:9000` unresolvable in host-network | `recordings.py:194`, `storage.py:90` |
| Redis down → bot stuck forever | 9835 | No reconnect in `main.py:101-108` | `main.py:101-108` |
| Multi-bot 0 segments (lite) | 9838-9840 | PulseAudio loopback — not a transcription bug | lite-specific |
| 0 segments on quick REST query | 9838-9840 | db_writer needs 40s (30s immutability + 10s interval) | `db_writer.py`, `collector/config.py:19-21` |
| K8s segments lost after stop | Phase 5a | Exit callback not fired from DELETE endpoint | `runtime-api/api.py:308-318` |
| K8s delayed stop lost on restart | Phase 5a | In-memory asyncio task, no persistence | `meetings.py:494-544` |
| Double completion warning | 9841, 9842 | Safety net fires after callback already completed. Harmless. | `meetings.py:522` |

### Theories (2026-04-08)

| Theory | Prob | Implication |
|--------|------|-------------|
| A: Transcription pipeline works everywhere, failures are lifecycle/infra | 90% | No transcription code changes. K8s uses remote transcription (internet latency), lite uses local (fast). |
| B: K8s pod restart loses delayed stop task + redis_client → both symptoms | 70% | Fix: Redis-backed timer + Redis reconnect. One event explains stuck stopping + 0 segments. |
| C: Lite stopping is working, just slow UX (50s via callback, 90s safety net) | 85% | Reduce `BOT_STOP_DELAY_SECONDS`. Not a bug, just bad UX. |
| D: Recording upload = independent config bug (MinIO DNS in host-network) | 95% | Fix `MINIO_ENDPOINT`. Completely separate from transcription. |
| E: Multi-bot 0 segments was premature REST query, not pipeline failure | 75% | Wait 40s before asserting. Or test WS during meeting instead of REST after. |

### Next: make targets to isolate issues

Each target isolates one theory. Run them, update DoDs with results, fix what's broken.

**`make -C tests3 bot-stop-timing`** — isolate Theory C (stopping UX)
```
1. Create bot on a meeting (Teams or GMeet, needs human)
2. Wait for active
3. Record timestamp T0
4. DELETE /bots
5. Poll status every 2s, record:
   - T1: status changed to stopping
   - T2: bot callback arrived (status_change log line)
   - T3: delayed stop fired (log line)
   - T4: status changed to completed
   - T5: post-meeting tasks ran
6. Report: T1-T0, T2-T0, T4-T0, T5-T0
7. PASS if T4-T0 < 120s. Log all timestamps.
```
Run on: lite, then K8s. Compare. If K8s T4 never arrives → confirms Theory B.

**`make -C tests3 transcription-replay`** — isolate Theory A (pipeline works) + latency + speaker accuracy
```
Ground truth: tests3/meeting_saved_closed_caption.txt (window 10:41:21–10:46:17)
  - 70 utterances, 4 speakers (Chris Davis, Alex Chen, Marco Rivera, Raj), 29 switches
  - Natural conversation with rapid alternation (1-5s gaps)
  - Anonymized dataset — safe for public use

1. Parse GT file, extract 5-min window
2. Create 1 recorder bot + 4 speaker bots (one per GT speaker) on a Teams meeting
3. Human admits bots
4. Replay GT as TTS: each utterance sent to correct speaker bot at correct relative timing
   - Use real timing gaps from GT (not fixed intervals)
   - Voice per speaker: alloy=Chris, echo=Alex, fable=Marco, onyx=Raj
5. During replay, subscribe to WS and log:
   - For each received segment: receive_timestamp, speaker, text
6. After replay completes + 30s drain:
   - Query REST /transcripts for final segments
   - Query Postgres directly for persisted count
7. Score against ground truth:
   a. Text matching: fuzzy match each GT line to closest output segment (≥70% similarity = match)
   b. Speaker accuracy: for each matched line, GT speaker == output speaker?
   c. Latency: TTS_send_time → WS_receive_time per segment
   d. Completeness: matched_lines / total_GT_lines
   e. Persistence: WS_count vs REST_count vs Postgres_count
8. Report:
   - Similarity: avg fuzzy score across matched lines
   - Speaker accuracy: X/Y lines correctly attributed (%)
   - Latency: p50, p90, max (seconds)
   - Completeness: X/Y GT lines found in output (%)
   - Persistence gap: segments seen in WS but missing from REST/Postgres
9. PASS if: completeness ≥70%, speaker accuracy ≥60%, p90 latency <30s

Output: tests3/.state/replay-results.json (machine-readable for DoD updates)
```
Run on: lite (Teams, local transcription), then K8s (Teams, remote transcription). Compare.

**`make -C tests3 segment-persistence`** — isolate Theory E (flush timing)
```
1. Create bot, wait for active, human speaks for 30s
2. Stop bot, record T0
3. Query REST at T0+0s, T0+10s, T0+20s, T0+40s, T0+60s
4. Report segment count at each interval
5. Also check Postgres directly: SELECT count(*) FROM transcriptions WHERE meeting_id=X
6. PASS if segments appear in REST within 60s of stop
```
Shows exactly when segments become visible. If REST shows 0 but Postgres has data → redis_client issue. If both 0 → db_writer didn't flush.

**`make -C tests3 recording-upload`** — isolate Theory D (MinIO config)
```
1. Create bot, wait for active, speak briefly, stop bot
2. Wait for completed
3. Check MinIO for recording file: mc ls vexa/recordings/{user_id}/{native_meeting_id}/
4. If missing, check meeting-api logs for upload errors
5. PASS if recording file exists in MinIO
```
Run on: lite (expect FAIL with current config, PASS after MINIO_ENDPOINT fix), compose (expect PASS), K8s (expect PASS if MinIO is in-cluster).

**`make -C tests3 k8s-restart-resilience`** — isolate Theory B (restart survival)
```
1. Create bot, wait for active
2. kubectl rollout restart deploy/vexa-vexa-meeting-api
3. Wait for meeting-api pod to be ready
4. Stop bot
5. Check: does bot reach completed? Do segments persist?
6. PASS if bot completes and segments visible via REST within 120s
```
This directly tests whether a meeting-api restart during an active meeting causes permanent damage.

### Needed code fixes

| Fix | File | Issue |
|-----|------|-------|
| Redis reconnect | `main.py:101-108` | `redis_client=None` forever if Redis down at startup |
| Explicit segment flush | `collector/db_writer.py` | No flush on meeting end — background loop only |
| MinIO endpoint for lite | `.env` / entrypoint | Needs `localhost` not `minio` in host-network |
| K8s exit callback from DELETE | `runtime-api/api.py:308-318` | DELETE calls `set_stopped()` but not `_fire_exit_callback()` |
| Delayed Stop persistence | `meetings.py:494-544` | In-memory asyncio lost on restart — needs Redis timer |
| Reduce stop delay | `config.py:28` | 90s too long — consider 15-30s |

## What this plan does NOT cover

- **GMeet flow** — blocked by Google login in CDP session (use `browser-login` human gate)
- **VM provisioning** — not needed when LKE is already up
- **mcp, calendar-service, telegram-bot** — no infra to test against (remain at confidence 0)
- **Zoom** — not implemented

---

## Coverage matrix

| Feature | smoke | dashboard | containers | webhooks | browser | auth-meeting | teams-tts | gmeet-tts |
|---------|-------|-----------|------------|----------|---------|--------------|-----------|-----------|
| infrastructure | x | | | | | | | |
| auth-and-limits | x | x | | | | | | |
| dashboard | x | x | | | | | | |
| meeting-urls | x | | | | | | | |
| container-lifecycle | x | | x | | | | | |
| bot-lifecycle | x | | x | | | | x | x |
| browser-session | x | | | | x | | | |
| remote-browser | x | | | | x | | | |
| authenticated-meetings | x | | | | | x | | |
| webhooks | x | | | x | | | | |
| speaking-bot | | | | | | | x | x |
| realtime-transcription | x | | | | | | x | x |
| meeting-chat | | | | | | | x | x |
| post-meeting-transcription | | | | | | | x | x |

| Deployment | pull | build | db-restore | smoke | dashboard | containers | webhooks | browser | transcript |
|------------|------|-------|------------|-------|-----------|------------|----------|---------|------------|
| Compose | x | x | x | x | x | x | x | | |
| Lite | x | x | x | x | x | x | | | |
| Helm | x | x | x | x | x | x | x | x | x |
