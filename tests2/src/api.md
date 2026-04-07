---
needs: [GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN]
gives: [USER_ID, API_TOKEN]
---

use: lib/http
use: lib/docker
use: env

# API

> **Why:** The API is the system's interface. If endpoints are broken, nothing downstream works.
> **What:** Test all API endpoints that don't require live meetings — admin, meetings, bots, runtime, agent, transcription, MCP, WebSocket smoke.
> **How:** Create a test user and API token via admin API, then hit every endpoint and count passes.

## state

    USER_ID    = ""
    API_TOKEN  = ""
    TESTED     = 0
    PASSED     = 0

## steps

```
1. user
   call: http.get_json(URL="{ADMIN_URL}/admin/users/email/{USER_EMAIL}", ADMIN_HEADER="X-Admin-API-Key: {ADMIN_TOKEN}")
   if STATUS_CODE == 200:
       => USER_ID = BODY.id
       emit PASS "user exists: {USER_ID}"
   else:
       call: http.post_json(URL="{ADMIN_URL}/admin/users", DATA='{"email":"{USER_EMAIL}","name":"Test User"}', ADMIN_HEADER="X-Admin-API-Key: {ADMIN_TOKEN}")
       expect: STATUS_CODE == 201
       => USER_ID = BODY.id
       emit PASS "user created: {USER_ID}"
   on_fail: stop

2. token
   call: http.post_json(URL="{ADMIN_URL}/admin/users/{USER_ID}/tokens?scopes=bot,browser,tx&name=test", DATA="{}", ADMIN_HEADER="X-Admin-API-Key: {ADMIN_TOKEN}")
   expect: STATUS_CODE in [200, 201]
   => API_TOKEN = BODY.token or BODY.api_key
   on_fail: stop

3. admin_list
   TESTED += 1
   call: http.get_json(URL="{ADMIN_URL}/admin/users", ADMIN_HEADER="X-Admin-API-Key: {ADMIN_TOKEN}")
   expect: STATUS_CODE == 200
   PASSED += 1; emit PASS "admin: list users"
   on_fail: continue

4. admin_get
   TESTED += 1
   call: http.get_json(URL="{ADMIN_URL}/admin/users/{USER_ID}", ADMIN_HEADER="X-Admin-API-Key: {ADMIN_TOKEN}")
   expect: BODY.id == USER_ID
   PASSED += 1; emit PASS "admin: get user"
   on_fail: continue

5. meetings
   TESTED += 1
   call: http.get_json(URL="{GATEWAY_URL}/meetings", TOKEN={API_TOKEN})
   expect: STATUS_CODE == 200
   PASSED += 1; emit PASS "meetings: list"
   on_fail: continue

6. bots
   TESTED += 1
   call: http.get_json(URL="{GATEWAY_URL}/bots/status", TOKEN={API_TOKEN})
   expect: STATUS_CODE == 200
   PASSED += 1; emit PASS "bots: status"
   on_fail: continue

7. runtime
   TESTED += 1
   call: http.check_url(URL="http://localhost:8090/health")
   if OK: PASSED += 1; emit PASS "runtime-api"
   on_fail: continue

8. agent [optional]
   > agent-api is NO-SHIP for 0.10 — commented out in docker-compose.yml.
   > Skip if not deployed. Not a failure.
   call: http.check_url(URL="http://localhost:8100/health")
   if OK: TESTED += 1; PASSED += 1; emit PASS "agent-api"
   else: emit SKIP "agent-api not deployed (NO-SHIP 0.10)"
   on_fail: continue

9. transcription
   TESTED += 1
   call: http.get_json(URL="http://localhost:8085/health")
   expect: BODY contains gpu_available
   PASSED += 1; emit PASS "transcription"
   on_fail: continue

10. mcp [optional]
    TESTED += 1
    call: http.check_url(URL="http://localhost:18888/docs")
    if OK: PASSED += 1; emit PASS "mcp"
    on_fail: continue

11. ws_smoke
    TESTED += 1
    call: http.check_ws(URL="ws://localhost:8056/ws?api_key={API_TOKEN}", SEND='{"action":"ping"}', EXPECT_CONTAINS="pong")
    PASSED += 1; emit PASS "websocket: ping/pong"
    on_fail: continue

12. summary
    emit FINDING "api: {PASSED}/{TESTED} endpoints"
    if PASSED == TESTED: emit PASS "all endpoints healthy"
    else: emit FAIL "{TESTED - PASSED} endpoints failed"
```
