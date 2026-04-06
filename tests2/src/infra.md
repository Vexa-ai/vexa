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

2. transcription
   call: http.check_url(URL="http://localhost:8085/health")
   expect: OK == true
   emit PASS "transcription service healthy"
   on_fail: stop

3. urls
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

4. token
   call: docker.container_for(MODE={DEPLOY_MODE}, SERVICE="admin-api")
   => CONTAINER
   call: docker.env(CONTAINER={CONTAINER}, VAR="ADMIN_API_TOKEN")
   => ADMIN_TOKEN = VALUE
   on_fail: stop

5. health
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

6. inter_container
   call: docker.container_for(MODE={DEPLOY_MODE}, SERVICE="api-gateway")
   => GW_CONTAINER
   if DEPLOY_MODE == "compose":
       call: docker.exec(CONTAINER={GW_CONTAINER}, CMD="curl -sf http://meeting-api:8080/health")
   if DEPLOY_MODE == "lite":
       call: docker.exec(CONTAINER={GW_CONTAINER}, CMD="curl -sf http://localhost:8080/health")
   expect: exits 0
   emit PASS "inter-container connectivity"
   on_fail: continue

7. transcription_inside
   call: docker.container_for(MODE={DEPLOY_MODE}, SERVICE="meeting-api")
   => CONTAINER
   if DEPLOY_MODE == "compose":
       call: docker.exec(CONTAINER={CONTAINER}, CMD="curl -sf http://transcription-lb/health || curl -sf http://host.docker.internal:8085/health")
   if DEPLOY_MODE == "lite":
       call: docker.exec(CONTAINER={CONTAINER}, CMD="curl -sf http://localhost:8085/health")
   expect: response contains gpu_available
   emit PASS "transcription reachable from inside"
   on_fail: stop

8. redis
   call: docker.container_for(MODE={DEPLOY_MODE}, SERVICE="redis")
   => REDIS
   call: docker.exec(CONTAINER={REDIS}, CMD="redis-cli ping")
   expect: PONG
   on_fail: continue

9. minio [optional]
   if DEPLOY_MODE == "compose":
       call: docker.exec(CONTAINER="vexa-minio-1", CMD="mc ls local/vexa/ 2>/dev/null | head -3")
   if DEPLOY_MODE == "lite":
       call: http.check_url(URL="http://localhost:9000/minio/health/live")
   on_fail: continue

10. summary
    emit FINDING "mode={DEPLOY_MODE} services={SERVICES_UP}/{SERVICES_TOTAL}"
    if SERVICES_UP < 4:
        emit FAIL "infra not ready: {SERVICES_UP}/{SERVICES_TOTAL}"
        on_fail: stop
    else:
        emit PASS "infra healthy"
```
