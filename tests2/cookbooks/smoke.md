---
needs: []
gives: [SMOKE_OK]
---

use: env
use: lib/log

# Smoke Test

> **Why:** After a deploy or restart, you want a fast "is it alive?" check before investing time in full tests.
> **What:** 5-minute sanity: infra health, API endpoints, dashboard backend calls, URL parsing. No meetings, no humans.
> **How:** Calls infra, api, dashboard, and urls modules. All automated, all fast.

## steps

```
1. init_log
   call: log.init(COOKBOOK="smoke")
   => LOG_FILE, JSONL_FILE
   call: log.emit(EVENT="START", MODULE="smoke", STEP="init", MSG="starting smoke test")

2. infra
   call: src/infra
   => GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN, DEPLOY_MODE, DASHBOARD_URL

3. api
   call: src/api(GATEWAY_URL={GATEWAY_URL}, ADMIN_URL={ADMIN_URL}, ADMIN_TOKEN={ADMIN_TOKEN})
   => USER_ID, API_TOKEN

4. dashboard
   call: src/dashboard(GATEWAY_URL={GATEWAY_URL}, ADMIN_URL={ADMIN_URL}, ADMIN_TOKEN={ADMIN_TOKEN}, DASHBOARD_URL={DASHBOARD_URL}, DEPLOY_MODE={DEPLOY_MODE}, API_TOKEN={API_TOKEN})
   => DASHBOARD_OK

5. urls
   call: src/urls(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN})
   => TEAMS_URLS_OK

6. summary
   => SMOKE_OK = (DASHBOARD_OK and TEAMS_URLS_OK)
   if SMOKE_OK: emit PASS "smoke: all clear"
   else: emit FAIL "smoke: issues found"

7. finish
   call: log.summary(MODULE="smoke", TOTAL_STEPS={step_count}, PASSED={pass_count}, FAILED={fail_count}, FIXED={fixed_count}, SKIPPED={skip_count})
   call: log.close()
   call: log.emit(EVENT="FINDING", MODULE="smoke", STEP="finish", MSG="logs at {LOG_FILE}")
```
