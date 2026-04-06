---
needs: [GATEWAY_URL, API_TOKEN, DASHBOARD_URL]
gives: [SESSION_TOKEN, CDP_URL, SAVED_STATE]
---

use: lib/http

# Browser Session

> **Why:** Browser sessions let bots reuse human logins (Google, GitHub). If state doesn't persist, every session requires re-login.
> **What:** End-to-end: create session via API, navigate via CDP, human logs in, save state to MinIO, destroy session, recreate, verify login persists.
> **How:** POST /bots with mode=browser_session, connect Playwright via gateway CDP proxy, human login step, save/restore via MinIO, verify cookies survive.

## state

    BOT_ID        = ""
    SESSION_TOKEN = ""
    CDP_URL       = ""
    SAVED_STATE   = false

## steps

```
1. create
   call: http.post_json(URL="{GATEWAY_URL}/bots", DATA='{"mode":"browser_session","bot_name":"Test Browser"}', TOKEN={API_TOKEN})
   expect: STATUS_CODE in [200, 201]
   => BOT_ID = BODY.id
   => SESSION_TOKEN = BODY.session_token
   => CDP_URL = "{GATEWAY_URL}/b/{SESSION_TOKEN}/cdp"
   on_fail: stop

2. wait_active
   call: http.poll_until(URL="{GATEWAY_URL}/bots/status", TOKEN={API_TOKEN}, FIELD="status", VALUE="active", MAX=12, INTERVAL=5)
   on_fail: stop

3. navigate [idempotent]
   do: |
       node -e "
       const {chromium}=require('playwright');
       (async()=>{
           const b=await chromium.connectOverCDP('{CDP_URL}');
           const p=b.contexts()[0].pages()[0]||await b.contexts()[0].newPage();
           await p.goto('https://www.google.com');
           console.log('URL:'+p.url());
           await b.close();
       })().catch(e=>{console.error(e.message);process.exit(1)});
       "
   expect: stdout contains google.com
   emit PASS "CDP proxy works"
   on_fail: stop

4. login [human]
   ask: "Open {DASHBOARD_URL}, start a browser session, navigate to a service requiring login, log in. Type 'done'."
   on_fail: stop

5. save
   call: http.post_json(URL="{GATEWAY_URL}/b/{SESSION_TOKEN}/save", DATA="{}", TOKEN={API_TOKEN})
   expect: STATUS_CODE == 200
   emit PASS "state saved"
   on_fail: ask

6. verify_storage
   do: docker exec vexa-minio-1 mc ls local/vexa/ --recursive 2>/dev/null | grep -c "Cookies\|Login\|userdata"
   expect: count > 0
   => SAVED_STATE = true
   on_fail: continue

7. destroy
   call: http.delete(URL="{GATEWAY_URL}/bots/{BOT_ID}", TOKEN={API_TOKEN})
   do: sleep 5
   on_fail: continue

8. recreate
   call: http.post_json(URL="{GATEWAY_URL}/bots", DATA='{"mode":"browser_session","bot_name":"Test Browser 2","use_saved_userdata":true}', TOKEN={API_TOKEN})
   expect: STATUS_CODE in [200, 201]
   => SESSION_TOKEN = BODY.session_token
   => CDP_URL = "{GATEWAY_URL}/b/{SESSION_TOKEN}/cdp"
   on_fail: stop

9. verify_state [human]
   ask: "Open the new browser session. Navigate to the same service. Are you still logged in? (yes/no)"
   if response == "yes": emit PASS "state persisted across sessions"
   if response == "no":  emit FAIL "login state lost"
   on_fail: continue
```
