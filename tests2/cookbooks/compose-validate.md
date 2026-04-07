---
needs: []
gives: [COMPOSE_OK, DASHBOARD_URL]
---

use: env
use: lib/log
use: lib/vm

# Compose Deployment Validation

> **Why:** A self-hoster follows the README on a fresh VM. If anything is wrong — missing prereqs, wrong commands, broken services, wrong user shown, bot won't start, can't transcribe — they're stuck. This catches it all before they do.
> **What:** Provision a blank VM, follow deploy/compose/README.md literally, then validate the core user flow: login as the right user, start a bot, verify transcription is reachable. Keep the deployment alive and return the dashboard URL for human testing.
> **How:** Provision → clone → follow docs → core flow (login, identity, bot, transcription) → plumbing → docs → score. No teardown.

## Weight philosophy

> The score must reflect what the self-hoster experiences, not what the ops team monitors.
> A deployment where redis PINGs but the user sees the wrong name after login is broken.
> Core flow criteria are ceiling: if any fail, confidence cannot reach target.

## state

    VM_IP          = ""
    VM_ID          = ""
    SSH            = ""
    REPO_DIR       = "/root/vexa"
    ADMIN_TOKEN    = ""
    USER_ID        = ""
    API_TOKEN      = ""
    DOC_GAPS       = []
    CONFIDENCE     = 0
    TARGET         = 90

## steps

```
1. init_log
   call: log.init(COOKBOOK="compose-validate")
   => LOG_FILE, JSONL_FILE
   call: log.emit(EVENT="START", MODULE="compose-validate", STEP="init", MSG="starting compose deployment validation")

═══════════════════════════════════════════════════════════════
 PHASE 1 — Provision fresh VM
 A blank Ubuntu machine. Nothing installed.
═══════════════════════════════════════════════════════════════

2. provision
   call: vm.provision(LABEL="vexa-compose-validate", REGION={VM_REGION}, TYPE={VM_TYPE})
   => VM_IP, VM_ID
   => SSH = "ssh -o StrictHostKeyChecking=no root@{VM_IP}"
   call: log.emit(EVENT="PASS", MODULE="compose-validate", STEP="provision", MSG="VM {VM_ID} at {VM_IP}")
   on_fail: stop

3. wait_ssh
   call: vm.wait_ssh(IP={VM_IP})
   call: log.emit(EVENT="PASS", MODULE="compose-validate", STEP="wait_ssh", MSG="SSH ready")
   on_fail: stop

═══════════════════════════════════════════════════════════════
 PHASE 2 — Follow the docs literally
 Read deploy/compose/README.md. Execute every instruction.
 A failure here = a doc gap. The self-hoster would be stuck.
═══════════════════════════════════════════════════════════════

4. prereqs
   > README says: apt-get update && apt-get install -y make; curl -fsSL https://get.docker.com | sh

   4a. install_make
       call: vm.ssh(IP={VM_IP}, CMD="apt-get update -qq && apt-get install -y -qq make git")
       expect: exits 0
       on_fail: stop

   4b. install_docker
       call: vm.ssh(IP={VM_IP}, CMD="curl -fsSL https://get.docker.com | sh")
       expect: exits 0
       call: vm.ssh(IP={VM_IP}, CMD="docker --version")
       expect: exits 0
       on_fail: stop

   call: log.emit(EVENT="PASS", MODULE="compose-validate", STEP="prereqs", MSG="make + docker installed")

5. clone
   call: vm.ssh(IP={VM_IP}, CMD="git clone {REPO_URL} {REPO_DIR} && cd {REPO_DIR} && git checkout {BRANCH}")
   expect: exits 0
   on_fail: stop

6. configure_env
   > README says: edit .env, set TRANSCRIPTION_SERVICE_URL.
   call: vm.ssh(IP={VM_IP}, CMD="cd {REPO_DIR}/deploy/compose && make env")
   expect: exits 0
   call: vm.ssh(IP={VM_IP}, CMD="sed -i 's|^#*TRANSCRIPTION_SERVICE_URL=.*|TRANSCRIPTION_SERVICE_URL={TRANSCRIPTION_SERVICE_URL}|' {REPO_DIR}/.env")
   call: vm.ssh(IP={VM_IP}, CMD="sed -i 's|^#*TRANSCRIPTION_SERVICE_TOKEN=.*|TRANSCRIPTION_SERVICE_TOKEN={TRANSCRIPTION_SERVICE_TOKEN}|' {REPO_DIR}/.env")
   on_fail: stop

7. make_all
   > This is the critical command. If it fails, the docs are wrong.
   call: vm.ssh(IP={VM_IP}, CMD="cd {REPO_DIR}/deploy/compose && make all 2>&1 | tail -50")
   expect: exits 0 and output contains "Vexa is running"
   if fails:
       DOC_GAPS.append("make all failed")
       on_fail: stop
   emit PASS "make all succeeded"

═══════════════════════════════════════════════════════════════
 PHASE 3 — Core user flow                          weight: 60
 What the self-hoster actually does after deploying.
 Every step here is ceiling — if it fails, score cannot reach 90.
═══════════════════════════════════════════════════════════════

8. login_and_identity [ceiling]
   > Self-hoster logs in. They must see THEIR name, not someone else's.
   > weight: 15

   8a. magic_link
       call: vm.ssh(IP={VM_IP}, CMD="curl -sv -X POST http://localhost:3001/api/auth/send-magic-link -H 'Content-Type: application/json' -d '{\"email\":\"test@vexa.ai\"}' -c /tmp/test-cookies 2>&1")
       => LOGIN_RESP_AND_HEADERS
       expect: response contains "success":true
       if fails:
           emit FAIL "login failed"
           on_fail: stop

   8b. correct_user
       do: parse LOGIN_RESP → user.email
       expect: user.email == "test@vexa.ai"
       if fails:
           emit FAIL "BUG: login as test@vexa.ai returned different user"
           on_fail: stop

   8c. cookie_flags
       > Bug found 2026-04-07: cookie has Secure flag on HTTP deployment.
       > Browsers reject Secure cookies on non-HTTPS → login redirect loop.
       do: grep Set-Cookie header for vexa-token
       expect: on HTTP deployment, Secure flag must NOT be present
       if Secure flag present on HTTP:
           emit FAIL "BUG: Secure cookie on HTTP — browser will reject it, login loops"
           on_fail: stop

   8d. identity_via_cookie
       > /api/auth/me must return the logged-in user, not the VEXA_API_KEY user.
       call: vm.ssh(IP={VM_IP}, CMD="curl -s -b /tmp/test-cookies http://localhost:3001/api/auth/me")
       => ME_RESP
       do: parse ME_RESP → user.email
       expect: user.email == "test@vexa.ai"
       if user.email != "test@vexa.ai":
           emit FAIL "BUG: /api/auth/me returns {user.email} instead of test@vexa.ai"
       else:
           emit PASS "identity: login + cookie flags + /me all correct"
           CONFIDENCE += 15
       on_fail: continue

9. login_redirect [ceiling]
   > After login, the user must land on /meetings, not a disabled feature page.
   > weight: 5
   call: vm.ssh(IP={VM_IP}, CMD="grep -n 'router.push' {REPO_DIR}/services/dashboard/src/app/login/page.tsx")
   > The direct-login path (result.mode === "direct") must push "/" or "/meetings".
   if output contains 'push("/agent")' or 'push("/admin")':
       emit FAIL "BUG: login redirects to wrong page"
   else:
       emit PASS "login redirects to / (→ /meetings)"
       CONFIDENCE += 5
   on_fail: continue

10. bot_creation [ceiling]
    > The self-hoster clicks "Start Bot". It must work.
    > weight: 15

    10a. bot_image_exists
        > Bot containers are not in docker-compose. The image must be pulled separately.
        call: vm.ssh(IP={VM_IP}, CMD="docker images --format '{{.Repository}}:{{.Tag}}' | grep vexa-bot || echo 'NO_BOT_IMAGE'")
        if output contains "NO_BOT_IMAGE":
            emit FAIL "no bot image — bot creation will fail"
            > Don't stop — continue to test POST /bots and confirm the 500.
        else:
            emit PASS "bot image present"

    10b. create_user_and_token
        > Need an API token to test bot creation.
        call: vm.ssh(IP={VM_IP}, CMD="grep '^ADMIN_TOKEN=' {REPO_DIR}/.env | cut -d= -f2")
        => ADMIN_TOKEN
        if ADMIN_TOKEN is empty: => ADMIN_TOKEN = "changeme"

        call: vm.ssh(IP={VM_IP}, CMD="curl -sf http://localhost:8057/admin/users/email/test@vexa.ai -H 'X-Admin-API-Key: {ADMIN_TOKEN}'")
        if STATUS_CODE == 200:
            => USER_ID from response
        else:
            call: vm.ssh(IP={VM_IP}, CMD="curl -sf -X POST http://localhost:8057/admin/users -H 'X-Admin-API-Key: {ADMIN_TOKEN}' -H 'Content-Type: application/json' -d '{\"email\":\"test@vexa.ai\",\"name\":\"Test User\"}'")
            => USER_ID from response

        call: vm.ssh(IP={VM_IP}, CMD="curl -sf -X POST 'http://localhost:8057/admin/users/{USER_ID}/tokens?scopes=bot,browser,tx&name=validate' -H 'X-Admin-API-Key: {ADMIN_TOKEN}'")
        => API_TOKEN from response
        on_fail: stop

    10c. post_bots
        call: vm.ssh(IP={VM_IP}, CMD="curl -s -X POST http://localhost:8056/bots -H 'X-API-Key: {API_TOKEN}' -H 'Content-Type: application/json' -d '{\"platform\":\"google_meet\",\"native_meeting_id\":\"validate-test\",\"bot_name\":\"validate-test\"}' -w '\nHTTP:%{http_code}'")
        => BOT_RESP, HTTP_CODE
        if HTTP_CODE in [200, 201, 202]:
            emit PASS "POST /bots → {HTTP_CODE}, bot created"
            CONFIDENCE += 15
            > Clean up: stop the test bot
            call: vm.ssh(IP={VM_IP}, CMD="curl -s -X DELETE http://localhost:8056/bots/google_meet/validate-test -H 'X-API-Key: {API_TOKEN}'")
        elif HTTP_CODE == 403:
            > Concurrent limit. API works, just can't start more bots.
            emit PASS "POST /bots → 403 (limit reached, API works)"
            CONFIDENCE += 10
        elif HTTP_CODE == 500:
            emit FAIL "POST /bots → 500"
            > Diagnose
            call: vm.ssh(IP={VM_IP}, CMD="curl -sf http://localhost:8090/health && echo 'runtime=UP' || echo 'runtime=DOWN'")
            call: vm.ssh(IP={VM_IP}, CMD="docker images --format '{{.Repository}}:{{.Tag}}' | grep bot || echo 'NO_BOT_IMAGE'")
            call: vm.ssh(IP={VM_IP}, CMD="docker logs vexa-meeting-api-1 --tail 10 2>&1 | grep -i 'error\\|fail' | tail -3")
        else:
            emit FAIL "POST /bots → unexpected {HTTP_CODE}"
        on_fail: continue

11. transcription_works [ceiling]
    > Without transcription, the product doesn't work. This is not optional.
    > A health check proves nothing — the real test is sending audio and getting text back.
    > `make test-transcription` sends tests/testdata/test-speech-en.wav to the
    > configured TRANSCRIPTION_SERVICE_URL with TRANSCRIPTION_SERVICE_TOKEN.
    > If it returns text: transcription works. If 401/403: wrong token. If 000: unreachable.
    > weight: 15

    call: vm.ssh(IP={VM_IP}, CMD="cd {REPO_DIR}/deploy/compose && make test-transcription 2>&1")
    if output contains "✓ Transcription works":
        emit PASS "transcription: real audio → real text"
        CONFIDENCE += 15
    elif output contains "auth failed":
        emit FAIL "transcription: token rejected — check TRANSCRIPTION_SERVICE_TOKEN"
    elif output contains "not set":
        emit FAIL "transcription: TRANSCRIPTION_SERVICE_URL not configured"
    else:
        emit FAIL "transcription: make test-transcription failed"
    on_fail: continue

12. dashboard_shows_meetings [ceiling]
    > After login, the dashboard must show the meetings page with data.
    > weight: 10
    call: vm.ssh(IP={VM_IP}, CMD="curl -s -b /tmp/test-cookies http://localhost:3001/api/vexa/meetings -w '\nHTTP:%{http_code}'")
    => MEETINGS_RESP, HTTP_CODE
    if HTTP_CODE == 200:
        emit PASS "dashboard: meetings endpoint returns 200 with cookie"
        CONFIDENCE += 10
    else:
        emit FAIL "dashboard: meetings endpoint returns {HTTP_CODE} — user sees empty page"
    on_fail: continue

═══════════════════════════════════════════════════════════════
 PHASE 4 — Plumbing                                weight: 25
 Infrastructure that supports the core flow.
 Important but not ceiling — plumbing failures don't hide behind
 a passing score when core flow is broken.
═══════════════════════════════════════════════════════════════

13. services_health
    for EP in [
        {name: "gateway",     port: 8056, path: "/",       weight: 3},
        {name: "admin-api",   port: 8057, path: "/docs",   weight: 2},
        {name: "dashboard",   port: 3001, path: "/",       weight: 3},
        {name: "runtime-api", port: 8090, path: "/health",  weight: 2}
    ]:
        call: vm.ssh(IP={VM_IP}, CMD="curl -sf -o /dev/null -w '%{http_code}' http://localhost:{EP.port}{EP.path}")
        if output == "200":
            emit PASS "{EP.name}: healthy"
            CONFIDENCE += EP.weight
        else:
            emit FAIL "{EP.name}: not responding"
        on_fail: continue

14. dashboard_backend
    call: vm.ssh(IP={VM_IP}, CMD="docker ps --filter 'name=dashboard' --format '{{.Names}}'")
    => DASH_CONTAINER
    if DASH_CONTAINER:
        14a. admin_key_valid
            call: vm.ssh(IP={VM_IP}, CMD="docker exec {DASH_CONTAINER} sh -c 'wget -q -O - --header=\"X-Admin-API-Key: \$VEXA_ADMIN_API_KEY\" \$VEXA_API_URL/admin/users?limit=1' 2>&1 | head -5")
            if exits 0 and returns JSON:
                emit PASS "dashboard: admin key valid"
                CONFIDENCE += 3
            else:
                emit FAIL "dashboard: VEXA_ADMIN_API_KEY wrong"
            on_fail: continue

        14b. api_key_valid
            call: vm.ssh(IP={VM_IP}, CMD="docker exec {DASH_CONTAINER} sh -c 'wget -q -O - --header=\"X-API-Key: \$VEXA_API_KEY\" \$VEXA_API_URL/bots' 2>&1 | head -5")
            if exits 0 and returns JSON:
                emit PASS "dashboard: API key valid"
                CONFIDENCE += 3
            else:
                emit FAIL "dashboard: VEXA_API_KEY wrong"
            on_fail: continue

15. infra_services
    for SVC in [
        {name: "redis",    cmd: "docker exec $(docker ps --filter name=redis -q | head -1) redis-cli ping 2>/dev/null", weight: 1},
        {name: "postgres", cmd: "docker exec $(docker ps --filter name=postgres -q | head -1) pg_isready -U postgres 2>/dev/null", weight: 1},
        {name: "minio",    cmd: "curl -sf http://localhost:9000/minio/health/live 2>/dev/null", weight: 1}
    ]:
        call: vm.ssh(IP={VM_IP}, CMD="{SVC.cmd}")
        if exits 0:
            emit PASS "infra: {SVC.name}"
            CONFIDENCE += SVC.weight
        else:
            emit FAIL "infra: {SVC.name}"
        on_fail: continue

16. inter_container
    call: vm.ssh(IP={VM_IP}, CMD="docker ps --filter 'name=gateway' --format '{{.Names}}'")
    => GW_CONTAINER
    if GW_CONTAINER:
        call: vm.ssh(IP={VM_IP}, CMD="docker exec {GW_CONTAINER} python3 -c \"import urllib.request; urllib.request.urlopen('http://meeting-api:8080/health').read()\" 2>&1")
        if exits 0:
            emit PASS "inter-container: gateway → meeting-api"
            CONFIDENCE += 2
        else:
            emit FAIL "inter-container: broken"
    on_fail: continue

17. container_cleanup
    call: vm.ssh(IP={VM_IP}, CMD="docker ps -a --filter 'name=meeting-' --format '{{.Names}}' | grep -v meeting-api | wc -l")
    => ORPHAN_COUNT
    if ORPHAN_COUNT == 0:
        emit PASS "no orphan bot containers"
        CONFIDENCE += 1
    else:
        emit FINDING "{ORPHAN_COUNT} orphan containers"
    on_fail: continue

18. make_test
    call: vm.ssh(IP={VM_IP}, CMD="cd {REPO_DIR}/deploy/compose && make test 2>&1")
    if output contains "✓" or output contains "Gateway":
        emit PASS "make test passes"
        CONFIDENCE += 3
    else:
        emit FAIL "make test failed"
    on_fail: continue

═══════════════════════════════════════════════════════════════
 PHASE 5 — Doc accuracy                            weight: 5
═══════════════════════════════════════════════════════════════

19. doc_accuracy

    19a. make_targets
        call: vm.ssh(IP={VM_IP}, CMD="cd {REPO_DIR}/deploy/compose && for t in all build down init-db setup-api-key test logs ps env; do make -n $t >/dev/null 2>&1 || echo \"MISSING: $t\"; done")
        if output contains "MISSING":
            DOC_GAPS.append("make target missing")
            emit FAIL "missing make target"
        else:
            emit PASS "all make targets exist"
            CONFIDENCE += 2
        on_fail: continue

    19b. ports_match
        call: vm.ssh(IP={VM_IP}, CMD="cd {REPO_DIR}/deploy/compose && docker compose ps --format '{{.Service}} {{.Ports}}' 2>/dev/null")
        => COMPOSE_PS
        for CHECK in [{service: "api-gateway", port: 8056}, {service: "admin-api", port: 8057}, {service: "dashboard", port: 3001}]:
            if COMPOSE_PS does not contain "{CHECK.port}":
                DOC_GAPS.append("port mismatch: {CHECK.service}")
        on_fail: continue

    19c. links_resolve
        call: vm.ssh(IP={VM_IP}, CMD="cd {REPO_DIR}/deploy/compose && grep -oP '\\(\\.\\./.+?\\)' README.md | tr -d '()' | while read link; do test -e $link || echo \"BROKEN: $link\"; done")
        if output contains "BROKEN":
            DOC_GAPS.append("broken links")
            emit FAIL "broken README links"
        else:
            emit PASS "all links resolve"
            CONFIDENCE += 1
        on_fail: continue

    19d. env_defaults
        call: vm.ssh(IP={VM_IP}, CMD="grep '^ADMIN_TOKEN=' {REPO_DIR}/deploy/env/env-example | cut -d= -f2")
        => EXAMPLE_TOKEN
        call: vm.ssh(IP={VM_IP}, CMD="docker exec vexa-admin-api-1 sh -c 'echo \$ADMIN_API_TOKEN' 2>/dev/null")
        => CONTAINER_TOKEN
        if EXAMPLE_TOKEN != CONTAINER_TOKEN:
            DOC_GAPS.append("admin token default mismatch: env-example={EXAMPLE_TOKEN} container={CONTAINER_TOKEN}")
        else:
            emit PASS "env defaults agree"
            CONFIDENCE += 2
        on_fail: continue

═══════════════════════════════════════════════════════════════
 PHASE 6 — Score and keep alive
═══════════════════════════════════════════════════════════════

20. score
    emit FINDING "confidence: {CONFIDENCE}/{TARGET}"
    emit FINDING "doc gaps: {len(DOC_GAPS)}"
    for GAP in DOC_GAPS:
        emit FINDING "  gap: {GAP}"

    => COMPOSE_OK = (CONFIDENCE >= TARGET and len(DOC_GAPS) == 0)
    if COMPOSE_OK:
        emit PASS "compose deployment validated: {CONFIDENCE}/{TARGET}"
    else:
        emit FAIL "compose deployment: {CONFIDENCE}/{TARGET}, {len(DOC_GAPS)} doc gaps"

21. keep_alive
    > Do NOT destroy the VM. The deployment stays up for human testing.
    => DASHBOARD_URL = "http://{VM_IP}:3001"
    emit FINDING "═══════════════════════════════════════════════════════"
    emit FINDING "DEPLOYMENT KEPT ALIVE FOR HUMAN TESTING"
    emit FINDING "  Dashboard: http://{VM_IP}:3001"
    emit FINDING "  API docs:  http://{VM_IP}:8056/docs"
    emit FINDING "  Admin:     http://{VM_IP}:8057/docs"
    emit FINDING "  SSH:       ssh root@{VM_IP}"
    emit FINDING "  VM ID:     {VM_ID} (destroy with: linode-cli linodes delete {VM_ID})"
    emit FINDING "═══════════════════════════════════════════════════════"

    ask: "Dashboard URL: http://{VM_IP}:3001 — VM kept alive. Destroy when done: linode-cli linodes delete {VM_ID}"

22. finish
    call: log.summary(MODULE="compose-validate", TOTAL_STEPS={step_count}, PASSED={pass_count}, FAILED={fail_count}, FIXED=0, SKIPPED={skip_count})
    call: log.close()
```

## Weight budget

| Phase | Weight | What it proves |
|---|---|---|
| Core flow | 60 | The product works for the user |
| Plumbing | 25 | Infrastructure supports the product |
| Doc accuracy | 5 | Docs aren't lying |
| **Total** | **90** | **= target** |

### Core flow breakdown (all ceiling)

| Step | Weight | What it proves |
|---|---|---|
| 8. login + identity | 15 | User logs in, sees their own name |
| 9. login redirect | 5 | User lands on /meetings |
| 10. bot creation | 15 | User can start a bot |
| 11. transcription | 15 | Audio can be transcribed |
| 12. dashboard shows meetings | 10 | User sees their data |

If ANY core flow step fails, max possible = 90 - weight = below target. Cannot reach 90 with broken core.

### Plumbing breakdown

| Step | Weight | What it proves |
|---|---|---|
| 13. service health (4 services) | 10 | Services respond |
| 14. dashboard keys | 6 | Dashboard auth chain |
| 15. redis/postgres/minio | 3 | Backing services |
| 16. inter-container | 2 | Containers can talk |
| 17. no orphans | 1 | Clean state |
| 18. make test | 3 | README's test works |
| **Subtotal** | **25** | |

## Failure modes

| Symptom | Cause | Fix | Learned |
|---|---|---|---|
| make all fails on fresh VM | Missing prereq not in README | Add prereq to README | Docker install sometimes needs reboot for group membership |
| Dashboard shows empty pages | VEXA_ADMIN_API_KEY or VEXA_API_KEY wrong | make setup-api-key; step 14 validates | Keys must be validated from inside the container |
| Login shows wrong user | /api/auth/me falls back to VEXA_API_KEY env var | Remove fallback — cookie is the only identity source | Two identity sources = wrong user |
| Login redirects to /agent | login/page.tsx hardcodes router.push("/agent") | Change to router.push("/") | Disabled features must not be default |
| Bot creation 500 | Bot image not pulled by make all | Added docker pull in Makefile up target | Service images ≠ bot image. Bot is a sidecar. |
| Bot image 404 | make all pulls compose images but bot spawns outside compose | Makefile up target pulls bot image if missing | compose-managed ≠ all images needed |
| Transcription health OK but auth fails | /health doesn't require auth — returns OK for anyone. Real endpoint returns 403. | `make test-transcription` sends real audio with real token. Health check replaced. | Health check ≠ function. Must test the actual API with actual credentials. |
| Transcription token mismatch | Local token used against hosted service (or vice versa). 403. | `make test-transcription` catches 403 with clear message. Runs on every `make all`. | Different services need different tokens. The test proves the configured pair works. |
| Score inflated by plumbing | Infrastructure checks weighted same as core flow | Core = 60, plumbing = 25, docs = 5 | redis PONG doesn't mean the product works |
| Login loops back to /login after "Welcome back" | Cookie set with `Secure` flag on HTTP. Browser rejects it. Every page load has no cookie → /api/auth/me 401 → redirect to login. | `secure: isSecureRequest()` checks protocol, not NODE_ENV. All 4 auth routes fixed. Step 8c checks cookie flags. | NODE_ENV=production ≠ HTTPS. Self-hosters deploy on HTTP. curl ignores Secure flag so proc must check explicitly. |
| make test passes but product broken | make test checks HTTP 200 only | Core flow phase tests actual user experience | Health check ≠ feature works |
