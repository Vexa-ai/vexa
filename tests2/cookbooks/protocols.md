---
needs: []
gives: [WEBSOCKET_OK, WEBHOOK_OK, LIFECYCLE_OK]
---

use: env
use: lib/log

# Protocols

> **Why:** WebSocket, webhooks, and container cleanup are integration points that break independently of meeting features.
> **What:** Validate WS protocol, webhook envelope/signing, and container removal. No live meetings needed.
> **How:** Calls websocket, webhooks, and containers modules in parallel after infra + api setup.

## steps

```
1. init_log
   call: log.init(COOKBOOK="protocols")
   => LOG_FILE, JSONL_FILE
   call: log.emit(EVENT="START", MODULE="protocols", STEP="init", MSG="starting protocols validation")

2. infra
   call: src/infra
   => GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN, DEPLOY_MODE

3. api
   call: src/api(GATEWAY_URL={GATEWAY_URL}, ADMIN_URL={ADMIN_URL}, ADMIN_TOKEN={ADMIN_TOKEN})
   => USER_ID, API_TOKEN

parallel:
    branch ws:
        4. websocket
           call: src/websocket(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN})
           => WEBSOCKET_OK
    branch hooks:
        5. webhooks
           call: src/webhooks(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, DEPLOY_MODE={DEPLOY_MODE})
           => WEBHOOK_OK
    branch containers:
        6. lifecycle
           call: src/containers(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, DEPLOY_MODE={DEPLOY_MODE})
           => LIFECYCLE_OK, ORPHAN_COUNT, ZOMBIE_COUNT
    join: wait for all

7. summary
   emit FINDING "ws={WEBSOCKET_OK} hooks={WEBHOOK_OK} lifecycle={LIFECYCLE_OK}"

8. finish
   call: log.summary(MODULE="protocols", TOTAL_STEPS={step_count}, PASSED={pass_count}, FAILED={fail_count}, FIXED={fixed_count}, SKIPPED={skip_count})
   call: log.close()
   call: log.emit(EVENT="FINDING", MODULE="protocols", STEP="finish", MSG="logs at {LOG_FILE}")
```
