---
needs: [GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN, DASHBOARD_URL, DEPLOY_MODE, API_TOKEN]
gives: [DASHBOARD_OK]
---

use: lib/http
use: lib/docker

# Dashboard

> **Why:** Next.js returns 200 even when all backends are broken. The user sees `[object Object]`. This catches it before they do.
> **What:** Run every backend call the dashboard makes — from inside the dashboard container, using its own env vars.
> **How:** Read VEXA_API_URL from the container, then wget each GET and POST endpoint the dashboard calls. If any fail, that page is broken.

## state

    CONTAINER = ""
    API_URL   = ""
    ADMIN_KEY = ""
    TESTED    = 0
    PASSED    = 0

## steps

```
1. find_container
   call: docker.container_for(MODE={DEPLOY_MODE}, SERVICE="dashboard")
   => CONTAINER = NAME
   on_fail: stop

2. read_env
   call: docker.env(CONTAINER={CONTAINER}, VAR="VEXA_API_URL")
   => API_URL = VALUE
   call: docker.env(CONTAINER={CONTAINER}, VAR="VEXA_ADMIN_API_KEY")
   => ADMIN_KEY = VALUE
   on_fail: stop

3. get_calls
   for CALL in [
       {name: "gateway",    cmd: "wget -q -O /dev/null {API_URL}/"},
       {name: "meetings",   cmd: "wget -q -O - --header='X-API-Key: {API_TOKEN}' {API_URL}/meetings"},
       {name: "bots",       cmd: "wget -q -O - --header='X-API-Key: {API_TOKEN}' {API_URL}/bots/status"},
       {name: "admin",      cmd: "wget -q -O - --header='X-Admin-API-Key: {ADMIN_KEY}' {API_URL}/admin/users?limit=1"},
       {name: "next-auth",  cmd: "wget -q -O - http://localhost:3000/api/auth/session"}
   ]:
       TESTED += 1
       call: docker.exec(CONTAINER={CONTAINER}, CMD="{CALL.cmd}")
       if exits 0: PASSED += 1; emit PASS "dashboard: {CALL.name}"
       else: emit FAIL "dashboard: {CALL.name}"
       on_fail: continue

4. post_calls
   TESTED += 1
   call: docker.exec(CONTAINER={CONTAINER}, CMD="wget -q -O - --post-data='{\"mode\":\"browser_session\",\"bot_name\":\"dash-test\"}' --header='Content-Type: application/json' --header='X-API-Key: {API_TOKEN}' {API_URL}/bots")
   if exits 0: PASSED += 1; emit PASS "dashboard: POST browser_session"
   else: emit FAIL "dashboard: POST browser_session — causes [object Object]"
   on_fail: continue

5. summary
   => DASHBOARD_OK = (PASSED == TESTED)
   emit FINDING "dashboard: {PASSED}/{TESTED}"
```
