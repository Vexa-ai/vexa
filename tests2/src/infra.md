---
needs: []
gives: [GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN, DEPLOY_MODE, DASHBOARD_URL]
---

use: lib/http
use: lib/docker

# Infra

> **Why:** Every other test needs working infrastructure. This is the gate.
> **What:** Detect which deployment mode is running (compose/lite/helm), health-check every service, produce base URLs and admin token.
> **How:** Probe Docker/k8s to detect mode, curl each service endpoint, verify inter-container connectivity and transcription reachability from inside containers.

## state

    DEPLOY_MODE   = ""
    GATEWAY_URL   = ""
    ADMIN_URL     = ""
    ADMIN_TOKEN   = ""
    DASHBOARD_URL = ""
    SERVICES_UP   = 0
    SERVICES_TOTAL = 0

## steps

```
1. detect
   call: docker.detect_mode()
   expect: MODE in [compose, lite, helm]
   => DEPLOY_MODE = MODE
   if DEPLOY_MODE == "none":
       emit FAIL "no deployment detected"
       on_fail: stop

2. verify_code_current
   > Stale images cause false failures. Verify running containers match current code.
   > Get the current git commit, then check the image tag running in Docker.

   do: git rev-parse --short HEAD
   => CURRENT_COMMIT

   if DEPLOY_MODE == "compose":
       do: docker inspect vexa-api-gateway-1 --format '{{.Config.Image}}'
       => RUNNING_IMAGE
       do: cat deploy/compose/.last-tag 2>/dev/null || echo "no-tag"
       => LAST_BUILD_TAG

   if DEPLOY_MODE == "lite":
       do: docker inspect vexa --format '{{.Config.Image}}'
       => RUNNING_IMAGE

   emit FINDING "current commit={CURRENT_COMMIT} running image={RUNNING_IMAGE} last build={LAST_BUILD_TAG}"

   > If the image is old, warn. The agent decides whether to rebuild.
   > Check: was the image built today? Does the tag contain today's date?
   do: date +%y%m%d
   => TODAY

   if RUNNING_IMAGE does not contain TODAY:
       emit FINDING "WARNING: running image may be stale (built before today)"
       ask: "Running image {RUNNING_IMAGE} may not contain latest code (commit {CURRENT_COMMIT}). Rebuild? (yes/no/skip)"
       if response == "yes":
           if DEPLOY_MODE == "compose":
               do: cd deploy/compose && make build && make up
               emit FIX "rebuilt and restarted compose stack"
           if DEPLOY_MODE == "lite":
               do: |
                   TAG=$(date +%y%m%d-%H%M)
                   docker build -f deploy/lite/Dockerfile.lite -t vexa-lite:$TAG .
                   docker rm -f vexa && docker run -d --name vexa --shm-size=2g --network host vexa-lite:$TAG
               emit FIX "rebuilt and restarted lite container"
           > Re-detect after rebuild
           call: docker.detect_mode()
           => DEPLOY_MODE = MODE
       if response == "skip":
           emit SKIP "running with potentially stale image"
   else:
       emit PASS "image tag contains today's date"

3. transcription
   call: http.check_url(URL="http://localhost:8085/health")
   expect: OK == true
   emit PASS "transcription service healthy"
   on_fail: stop

4. urls
   if DEPLOY_MODE == "compose":
       => GATEWAY_URL = "http://localhost:8056"
       => ADMIN_URL = "http://localhost:8057"
       => DASHBOARD_URL = "http://localhost:3001"
   if DEPLOY_MODE == "lite":
       => GATEWAY_URL = "http://localhost:8056"
       => ADMIN_URL = "http://localhost:8057"
       => DASHBOARD_URL = "http://localhost:3000"
   if DEPLOY_MODE == "helm":
       do: kubectl get ingress -o jsonpath='{.items[0].spec.rules[0].host}'
       => GATEWAY_URL = "http://{stdout}"
       => ADMIN_URL = GATEWAY_URL
       => DASHBOARD_URL = GATEWAY_URL
   on_fail: stop

5. token
   call: docker.container_for(MODE={DEPLOY_MODE}, SERVICE="admin-api")
   => CONTAINER
   call: docker.env(CONTAINER={CONTAINER}, VAR="ADMIN_API_TOKEN")
   => ADMIN_TOKEN = VALUE
   on_fail: stop

6. health
   for EP in [
       {name: "gateway",       url: "{GATEWAY_URL}/"},
       {name: "admin-api",     url: "{ADMIN_URL}/admin/users", header: "X-Admin-API-Key: {ADMIN_TOKEN}"},
       {name: "dashboard",     url: "{DASHBOARD_URL}/"},
       {name: "transcription", url: "http://localhost:8085/health"}
   ]:
       SERVICES_TOTAL += 1
       call: http.check_url(URL={EP.url}, HEADER={EP.header})
       if OK: SERVICES_UP += 1; emit PASS "{EP.name}: healthy"
       else:  emit FAIL "{EP.name}: unreachable"
       on_fail: continue

   if DEPLOY_MODE == "lite":
       for EP in [
           {name: "meeting-api", url: "http://localhost:8080/health"},
           {name: "runtime-api", url: "http://localhost:8090/health"},
           {name: "agent-api",   url: "http://localhost:8100/health"},
           {name: "mcp",         url: "http://localhost:18888/docs"},
           {name: "tts",         url: "http://localhost:8059/health"}
       ]:
           SERVICES_TOTAL += 1
           call: http.check_url(URL={EP.url})
           if OK: SERVICES_UP += 1; emit PASS "{EP.name}: healthy"
           else:  emit FAIL "{EP.name}: unreachable"
           on_fail: continue

   if DEPLOY_MODE == "compose":
       for EP in [
           {name: "runtime-api", url: "http://localhost:8090/health"},
           {name: "agent-api",   url: "http://localhost:8100/health"}
       ]:
           SERVICES_TOTAL += 1
           call: http.check_url(URL={EP.url})
           if OK: SERVICES_UP += 1; emit PASS "{EP.name}: healthy"
           else:  emit FAIL "{EP.name}: unreachable"
           on_fail: continue

7. inter_container
   call: docker.container_for(MODE={DEPLOY_MODE}, SERVICE="api-gateway")
   => GW_CONTAINER
   if DEPLOY_MODE == "compose":
       call: docker.exec(CONTAINER={GW_CONTAINER}, CMD="curl -sf http://meeting-api:8080/health")
   if DEPLOY_MODE == "lite":
       call: docker.exec(CONTAINER={GW_CONTAINER}, CMD="curl -sf http://localhost:8080/health")
   expect: exits 0
   emit PASS "inter-container connectivity"
   on_fail: continue

8. transcription_inside
   call: docker.container_for(MODE={DEPLOY_MODE}, SERVICE="meeting-api")
   => CONTAINER
   if DEPLOY_MODE == "compose":
       call: docker.exec(CONTAINER={CONTAINER}, CMD="curl -sf http://transcription-lb/health || curl -sf http://host.docker.internal:8085/health")
   if DEPLOY_MODE == "lite":
       call: docker.exec(CONTAINER={CONTAINER}, CMD="curl -sf http://localhost:8085/health")
   expect: response contains gpu_available
   emit PASS "transcription reachable from inside"
   on_fail: stop

9. redis
   call: docker.container_for(MODE={DEPLOY_MODE}, SERVICE="redis")
   => REDIS
   call: docker.exec(CONTAINER={REDIS}, CMD="redis-cli ping")
   expect: PONG
   on_fail: continue

10. minio [optional]
   if DEPLOY_MODE == "compose":
       call: docker.exec(CONTAINER="vexa-minio-1", CMD="mc ls local/vexa/ 2>/dev/null | head -3")
   if DEPLOY_MODE == "lite":
       call: http.check_url(URL="http://localhost:9000/minio/health/live")
   on_fail: continue

11. dashboard_credentials
    > Bug found 2026-04-07: VEXA_ADMIN_API_KEY defaulted to wrong value in compose,
    > VEXA_API_KEY was stale. Dashboard silently returned empty data for all pages.
    > This step validates the dashboard's own credentials are live.

    call: docker.container_for(MODE={DEPLOY_MODE}, SERVICE="dashboard")
    => DASH_CONTAINER

    11a. admin_key
        call: docker.env(CONTAINER={DASH_CONTAINER}, VAR="VEXA_ADMIN_API_KEY")
        => DASH_ADMIN_KEY
        call: docker.env(CONTAINER={DASH_CONTAINER}, VAR="VEXA_API_URL")
        => DASH_API_URL
        do: docker exec {DASH_CONTAINER} wget -q -O - --header="X-Admin-API-Key: {DASH_ADMIN_KEY}" {DASH_API_URL}/admin/users?limit=1
        expect: exits 0 and returns JSON
        emit PASS "dashboard admin key valid"
        on_fail: emit FAIL "BUG: dashboard VEXA_ADMIN_API_KEY is wrong — magic link login will fail"

    11b. api_key
        call: docker.env(CONTAINER={DASH_CONTAINER}, VAR="VEXA_API_KEY")
        => DASH_API_KEY
        if DASH_API_KEY is empty:
            emit FAIL "VEXA_API_KEY not set — run 'make setup-api-key'"
            on_fail: continue
        do: docker exec {DASH_CONTAINER} wget -q -O - --header="X-API-Key: {DASH_API_KEY}" {DASH_API_URL}/bots/status
        expect: exits 0 and returns JSON (not 401)
        emit PASS "dashboard API key valid"
        on_fail: emit FAIL "BUG: dashboard VEXA_API_KEY is stale (401) — all API calls will fail"

12. summary
    emit FINDING "mode={DEPLOY_MODE} services={SERVICES_UP}/{SERVICES_TOTAL}"
    if SERVICES_UP < 4:
        emit FAIL "infra not ready: {SERVICES_UP}/{SERVICES_TOTAL}"
        on_fail: stop
    else:
        emit PASS "infra healthy"
```

## Failure modes

| Symptom | Cause | Fix | Learned |
|---|---|---|---|
| Image tag is yesterday's date | make build reuses cached layers + same tag if code didn't change in Dockerfile | Always check image creation time, not just tag | Tags can be misleading — a tag like 260405-1517 might be rebuilt today with new code but look old |
| curl not found in gateway container | Python slim images don't include curl | Use python3 urllib as fallback for inter-container checks | Never assume curl exists — check first or use language-native HTTP |
| agent-api unreachable (000) | Container commented out in compose or crashed | Check docker compose logs agent-api | 000 = connection refused, not HTTP error — service is not running |
| Health 200 but code is stale | Container rebuilt from cache, image tag unchanged | verify_code_current step compares image creation timestamp to today | Health checks prove service responds, not that code is current |
| Dashboard shows empty pages / login fails | VEXA_ADMIN_API_KEY defaults to wrong value in compose (`vexa-admin-token` vs `changeme`) | Fixed compose to use `${ADMIN_API_TOKEN:-changeme}` matching admin-api | 2026-04-07: different defaults for same variable across services = silent auth failure |
| Dashboard transcript/status empty after restart | VEXA_API_KEY in .env goes stale when tokens are regenerated | Run `make setup-api-key` after restart, or step 11b catches it | Tokens have no expiry but are invalidated when the user table changes |
