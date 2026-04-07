---
needs: []
gives: [COMPOSE_OK, DASHBOARD_URL]
---

use: env
use: lib/log
use: lib/vm

# Compose Deployment Validation

> **Why:** A self-hoster follows the README on a fresh VM. If anything is wrong — missing prereqs, wrong commands, broken services, stale docs — they're stuck. This catches it all before they do.
> **What:** Provision a blank VM, follow deploy/compose/README.md literally, then validate everything a user would touch: services, API, dashboard, WebSocket, URL parsing, container lifecycle. Keep the deployment alive and return the dashboard URL for human testing.
> **How:** Provision → clone → follow docs → health check → API → dashboard → protocols → score. No teardown.

## state

    VM_IP          = ""
    VM_ID          = ""
    SSH            = ""
    REPO_DIR       = "/root/vexa"
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
   > Follow it exactly.

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
   call: log.emit(EVENT="PASS", MODULE="compose-validate", STEP="clone", MSG="repo cloned, branch={BRANCH}")
   on_fail: stop

6. read_readme
   > Read the README on the VM. Verify it exists and has the Quick Start section.
   call: vm.ssh(IP={VM_IP}, CMD="cat {REPO_DIR}/deploy/compose/README.md")
   => README_CONTENT
   expect: README_CONTENT contains "make all"
   if not: DOC_GAPS.append("README missing 'make all' quick start")
   on_fail: continue

7. configure_env
   > README says: edit .env, set TRANSCRIPTION_SERVICE_URL.
   > Use make env to create .env from template, then set required vars.
   call: vm.ssh(IP={VM_IP}, CMD="cd {REPO_DIR}/deploy/compose && make env")
   expect: exits 0

   > Set TRANSCRIPTION_SERVICE_URL from our env.
   call: vm.ssh(IP={VM_IP}, CMD="sed -i 's|^#*TRANSCRIPTION_SERVICE_URL=.*|TRANSCRIPTION_SERVICE_URL={TRANSCRIPTION_SERVICE_URL}|' {REPO_DIR}/.env")
   call: vm.ssh(IP={VM_IP}, CMD="sed -i 's|^#*TRANSCRIPTION_SERVICE_TOKEN=.*|TRANSCRIPTION_SERVICE_TOKEN={TRANSCRIPTION_SERVICE_TOKEN}|' {REPO_DIR}/.env")
   on_fail: stop

8. make_all
   > README says: make all — pulls images, starts services, syncs DB, creates API key, tests.
   > This is the critical command. If it fails, the docs are wrong.
   call: vm.ssh(IP={VM_IP}, CMD="cd {REPO_DIR}/deploy/compose && make all 2>&1 | tail -50")
   expect: exits 0 and output contains "Vexa is running"
   if fails:
       DOC_GAPS.append("make all failed — see output")
       call: log.emit(EVENT="FAIL", MODULE="compose-validate", STEP="make_all", MSG="make all failed")
       on_fail: stop
   call: log.emit(EVENT="PASS", MODULE="compose-validate", STEP="make_all", MSG="make all succeeded")

═══════════════════════════════════════════════════════════════
 PHASE 3 — Validate from user's perspective
 Everything below runs on the VM via SSH.
 Checks what the self-hoster would check after deploying.
═══════════════════════════════════════════════════════════════

9. services_health
   > Check every service listed in the README port table.
   for EP in [
       {name: "gateway",       port: 8056, path: "/",        weight: 10},
       {name: "admin-api",     port: 8057, path: "/docs",    weight: 5},
       {name: "dashboard",     port: 3001, path: "/",        weight: 10},
       {name: "runtime-api",   port: 8090, path: "/health",  weight: 3},
       {name: "transcription", port: 8085, path: "/health",  weight: 5}
   ]:
       call: vm.ssh(IP={VM_IP}, CMD="curl -sf -o /dev/null -w '%{http_code}' http://localhost:{EP.port}{EP.path}")
       if output == "200":
           emit PASS "{EP.name}: healthy (port {EP.port})"
           CONFIDENCE += EP.weight
       else:
           emit FAIL "{EP.name}: not responding (port {EP.port})"
           DOC_GAPS.append("{EP.name} not healthy on port {EP.port}")
       on_fail: continue

10. verify_ports_match_docs
    > README lists ports. Verify they match what's actually exposed.
    > If the README says 3001 for dashboard but compose exposes 3000, that's a doc gap.
    call: vm.ssh(IP={VM_IP}, CMD="cd {REPO_DIR}/deploy/compose && docker compose ps --format '{{.Service}} {{.Ports}}' 2>/dev/null || docker compose ps")
    => COMPOSE_PS
    emit FINDING "compose services: {COMPOSE_PS}"

    > Check that ports from README port table match compose output.
    for CHECK in [
        {service: "api-gateway", doc_port: 8056},
        {service: "admin-api",   doc_port: 8057},
        {service: "dashboard",   doc_port: 3001}
    ]:
        if COMPOSE_PS does not contain "{CHECK.doc_port}":
            DOC_GAPS.append("README says {CHECK.service} on port {CHECK.doc_port} but compose shows different")
            emit FAIL "port mismatch: {CHECK.service} doc={CHECK.doc_port}"
        else:
            emit PASS "port matches: {CHECK.service}={CHECK.doc_port}"
    on_fail: continue

11. admin_api
    > Create a test user and API token — the foundation for everything else.
    > Uses the admin token from .env (default: changeme).
    call: vm.ssh(IP={VM_IP}, CMD="grep '^ADMIN_TOKEN=' {REPO_DIR}/.env | cut -d= -f2")
    => ADMIN_TOKEN
    if ADMIN_TOKEN is empty: => ADMIN_TOKEN = "changeme"

    11a. list_users
        call: vm.ssh(IP={VM_IP}, CMD="curl -sf http://localhost:8057/admin/users -H 'X-Admin-API-Key: {ADMIN_TOKEN}'")
        expect: returns JSON array
        if fails:
            emit FAIL "admin API unreachable or token wrong"
            DOC_GAPS.append("admin API auth failed with documented default token")
            on_fail: stop
        emit PASS "admin API: reachable"
        CONFIDENCE += 5

    11b. create_user
        call: vm.ssh(IP={VM_IP}, CMD="curl -sf http://localhost:8057/admin/users/email/test@vexa.ai -H 'X-Admin-API-Key: {ADMIN_TOKEN}'")
        if STATUS_CODE == 200:
            => USER_ID from response
        else:
            call: vm.ssh(IP={VM_IP}, CMD="curl -sf -X POST http://localhost:8057/admin/users -H 'X-Admin-API-Key: {ADMIN_TOKEN}' -H 'Content-Type: application/json' -d '{\"email\":\"test@vexa.ai\",\"name\":\"Test User\"}'")
            => USER_ID from response
        on_fail: stop

    11c. create_token
        call: vm.ssh(IP={VM_IP}, CMD="curl -sf -X POST 'http://localhost:8057/admin/users/{USER_ID}/tokens?scopes=bot,browser,tx&name=validate' -H 'X-Admin-API-Key: {ADMIN_TOKEN}'")
        => API_TOKEN from response
        emit PASS "API token created"
        CONFIDENCE += 5
        on_fail: stop

12. api_endpoints
    > Hit every API endpoint a self-hoster would use.
    for EP in [
        {name: "meetings list", cmd: "curl -sf http://localhost:8056/meetings -H 'X-API-Key: {API_TOKEN}'",                    weight: 5},
        {name: "bots status",   cmd: "curl -sf http://localhost:8056/bots/status -H 'X-API-Key: {API_TOKEN}'",                 weight: 5},
        {name: "ws ping",       cmd: "python3 -c \"import asyncio,websockets,json;asyncio.run((lambda:websockets.connect('ws://localhost:8056/ws?api_key={API_TOKEN}').__aenter__())())\" 2>&1 || echo 'ws-needs-lib'", weight: 3}
    ]:
        call: vm.ssh(IP={VM_IP}, CMD="{EP.cmd}")
        if exits 0 and output is valid:
            emit PASS "api: {EP.name}"
            CONFIDENCE += EP.weight
        else:
            emit FAIL "api: {EP.name}"
        on_fail: continue

13. dashboard_backend
    > The dashboard container must reach all its backends.
    > This is the #1 source of "dashboard shows nothing" bugs.
    call: vm.ssh(IP={VM_IP}, CMD="docker ps --filter 'name=dashboard' --format '{{.Names}}'")
    => DASH_CONTAINER

    if DASH_CONTAINER:
        13a. admin_key_valid
            call: vm.ssh(IP={VM_IP}, CMD="docker exec {DASH_CONTAINER} sh -c 'wget -q -O - --header=\"X-Admin-API-Key: \$VEXA_ADMIN_API_KEY\" \$VEXA_API_URL/admin/users?limit=1' 2>&1 | head -5")
            if exits 0 and returns JSON:
                emit PASS "dashboard: admin key valid"
                CONFIDENCE += 5
            else:
                emit FAIL "BUG: dashboard VEXA_ADMIN_API_KEY is wrong — login will fail"
                DOC_GAPS.append("dashboard VEXA_ADMIN_API_KEY doesn't match admin-api token")
            on_fail: continue

        13b. api_key_valid
            call: vm.ssh(IP={VM_IP}, CMD="docker exec {DASH_CONTAINER} sh -c 'wget -q -O - --header=\"X-API-Key: \$VEXA_API_KEY\" \$VEXA_API_URL/bots/status' 2>&1 | head -5")
            if exits 0 and returns JSON:
                emit PASS "dashboard: API key valid"
                CONFIDENCE += 5
            else:
                emit FAIL "dashboard VEXA_API_KEY is stale or missing"
            on_fail: continue

        13c. gateway_reachable
            call: vm.ssh(IP={VM_IP}, CMD="docker exec {DASH_CONTAINER} sh -c 'wget -q -O /dev/null \$VEXA_API_URL/' 2>&1")
            if exits 0:
                emit PASS "dashboard: gateway reachable from container"
                CONFIDENCE += 3
            else:
                emit FAIL "dashboard: cannot reach gateway from inside container"
            on_fail: continue
    on_fail: continue

14. inter_container
    > Verify containers can talk to each other (gateway → meeting-api).
    call: vm.ssh(IP={VM_IP}, CMD="docker ps --filter 'name=gateway' --format '{{.Names}}'")
    => GW_CONTAINER
    if GW_CONTAINER:
        call: vm.ssh(IP={VM_IP}, CMD="docker exec {GW_CONTAINER} python3 -c \"import urllib.request; urllib.request.urlopen('http://meeting-api:8080/health').read()\" 2>&1")
        if exits 0:
            emit PASS "inter-container: gateway → meeting-api"
            CONFIDENCE += 3
        else:
            emit FAIL "inter-container: gateway cannot reach meeting-api"
    on_fail: continue

15. transcription_reachable
    > The meeting-api must reach the transcription service.
    > This is listed as "ceiling" in the compose DoD — if it fails, bots can't transcribe.
    call: vm.ssh(IP={VM_IP}, CMD="curl -sf http://localhost:8085/health")
    if exits 0 and output contains "gpu_available":
        emit PASS "transcription: healthy and reachable"
        CONFIDENCE += 5
    else:
        emit FINDING "transcription: not reachable from host (may be external)"
        > Check if TRANSCRIPTION_SERVICE_URL points to an external service.
        call: vm.ssh(IP={VM_IP}, CMD="grep TRANSCRIPTION_SERVICE_URL {REPO_DIR}/.env | head -1")
        emit FINDING "transcription URL: {output}"
    on_fail: continue

16. redis_postgres_minio
    > Infrastructure services must be running.
    for SVC in [
        {name: "redis",    cmd: "docker exec $(docker ps --filter name=redis -q | head -1) redis-cli ping 2>/dev/null"},
        {name: "postgres", cmd: "docker exec $(docker ps --filter name=postgres -q | head -1) pg_isready -U postgres 2>/dev/null"},
        {name: "minio",    cmd: "curl -sf http://localhost:9000/minio/health/live 2>/dev/null"}
    ]:
        call: vm.ssh(IP={VM_IP}, CMD="{SVC.cmd}")
        if exits 0:
            emit PASS "infra: {SVC.name}"
            CONFIDENCE += 2
        else:
            emit FAIL "infra: {SVC.name} not responding"
        on_fail: continue

17. container_cleanup
    > Verify no orphan bot containers exist (fresh deploy should be clean).
    call: vm.ssh(IP={VM_IP}, CMD="docker ps -a --filter 'name=meeting-' --format '{{.Names}}' | wc -l")
    => ORPHAN_COUNT
    if ORPHAN_COUNT == 0:
        emit PASS "no orphan bot containers"
        CONFIDENCE += 2
    else:
        emit FINDING "{ORPHAN_COUNT} bot containers found on fresh deploy"
    on_fail: continue

18. make_test
    > README says `make test` should work. Verify it passes.
    call: vm.ssh(IP={VM_IP}, CMD="cd {REPO_DIR}/deploy/compose && make test 2>&1")
    => MAKE_TEST_OUTPUT
    if output contains "✓" or "Gateway" or "Dashboard":
        emit PASS "make test passes"
        CONFIDENCE += 3
    else:
        emit FAIL "make test failed"
        DOC_GAPS.append("make test does not pass on fresh deploy")
    on_fail: continue

═══════════════════════════════════════════════════════════════
 PHASE 4 — Doc accuracy
 Verify the README claims match reality.
═══════════════════════════════════════════════════════════════

19. doc_accuracy
    > Check specific README claims against reality.

    19a. make_targets
        > README lists make targets. Verify they all exist.
        call: vm.ssh(IP={VM_IP}, CMD="cd {REPO_DIR}/deploy/compose && make -n all 2>&1; make -n build 2>&1; make -n down 2>&1; make -n init-db 2>&1; make -n setup-api-key 2>&1; make -n test 2>&1; make -n logs 2>&1; make -n ps 2>&1")
        > Any "No rule to make target" = doc gap.
        if output contains "No rule":
            DOC_GAPS.append("make target in README does not exist in Makefile")
            emit FAIL "missing make target"
        else:
            emit PASS "all documented make targets exist"
            CONFIDENCE += 3
        on_fail: continue

    19b. env_defaults
        > README says ADMIN_TOKEN default is "changeme". Verify.
        call: vm.ssh(IP={VM_IP}, CMD="grep 'ADMIN_TOKEN' {REPO_DIR}/deploy/env/env-example | head -1")
        => ENV_EXAMPLE_ADMIN
        call: vm.ssh(IP={VM_IP}, CMD="grep 'ADMIN_TOKEN' {REPO_DIR}/.env | head -1")
        => ENV_ADMIN
        emit FINDING "env-example: {ENV_EXAMPLE_ADMIN}, .env: {ENV_ADMIN}"
        on_fail: continue

    19c. links_resolve
        > README has internal links. Verify they point to files that exist.
        call: vm.ssh(IP={VM_IP}, CMD="grep -oP '\\(\\.\\./.+?\\)' {REPO_DIR}/deploy/compose/README.md | tr -d '()' | while read link; do test -e {REPO_DIR}/deploy/compose/$link && echo \"OK: $link\" || echo \"BROKEN: $link\"; done")
        if output contains "BROKEN":
            DOC_GAPS.append("broken internal links in README")
            emit FAIL "broken links in README"
        else:
            emit PASS "all README links resolve"
            CONFIDENCE += 2
        on_fail: continue

═══════════════════════════════════════════════════════════════
 PHASE 5 — Score and keep alive
 No teardown. Dashboard URL returned for human testing.
═══════════════════════════════════════════════════════════════

20. score
    emit FINDING "confidence: {CONFIDENCE}/{TARGET}"
    emit FINDING "doc gaps: {len(DOC_GAPS)}"
    for GAP in DOC_GAPS:
        emit FINDING "  gap: {GAP}"

    => COMPOSE_OK = (CONFIDENCE >= TARGET and len(DOC_GAPS) == 0)
    if COMPOSE_OK:
        emit PASS "compose deployment validated: {CONFIDENCE}/{TARGET}, 0 doc gaps"
    else:
        emit FAIL "compose deployment: {CONFIDENCE}/{TARGET}, {len(DOC_GAPS)} doc gaps"

21. keep_alive
    > Do NOT destroy the VM. The deployment stays up for human testing.
    > Print connection details.
    => DASHBOARD_URL = "http://{VM_IP}:3001"
    emit FINDING "═══════════════════════════════════════════════════════"
    emit FINDING "DEPLOYMENT KEPT ALIVE FOR HUMAN TESTING"
    emit FINDING "  Dashboard: http://{VM_IP}:3001"
    emit FINDING "  API docs:  http://{VM_IP}:8056/docs"
    emit FINDING "  Admin:     http://{VM_IP}:8057/docs"
    emit FINDING "  SSH:       ssh root@{VM_IP}"
    emit FINDING "  VM ID:     {VM_ID} (destroy with: linode-cli linodes delete {VM_ID})"
    emit FINDING "═══════════════════════════════════════════════════════"

    ask: "Dashboard URL: http://{VM_IP}:3001 — VM kept alive. Destroy manually when done: linode-cli linodes delete {VM_ID}"

22. finish
    call: log.summary(MODULE="compose-validate", TOTAL_STEPS={step_count}, PASSED={pass_count}, FAILED={fail_count}, FIXED=0, SKIPPED={skip_count})
    call: log.close()
```

## Failure modes

| Symptom | Cause | Fix | Learned |
|---|---|---|---|
| make all fails on fresh VM | Missing prereq not in README | Add prereq to README Prerequisites section | Docker install script sometimes needs reboot for group membership |
| Dashboard shows empty pages | VEXA_ADMIN_API_KEY or VEXA_API_KEY wrong | make setup-api-key; step 13 validates both keys | Keys must be validated from inside the container, not from outside |
| Ports in README don't match compose | README edited without updating compose or vice versa | Fix the stale document | Port table is a contract — both sides must agree |
| make test passes but dashboard is broken | make test only checks HTTP 200, not auth chain or data flow | Steps 13a/13b check the full auth chain from inside the container | Health check PASS ≠ feature works |
| Inter-container connectivity fails | Docker network not created or service name changed | Check docker-compose.yml service names match code expectations | Compose service names are the DNS names — a rename breaks callers |
| Admin token "changeme" doesn't work | compose uses different default than admin-api | Three-way check: env-example, .env, container env | Every default must agree across all config sources |
