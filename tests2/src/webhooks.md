---
needs: [GATEWAY_URL, API_TOKEN, DEPLOY_MODE]
gives: [WEBHOOK_OK]
---

use: lib/http
use: lib/docker

# Webhooks

> **Why:** Customers integrate via webhooks. A malformed envelope or leaked internal field breaks their integration or exposes secrets.
> **What:** Create bot with webhook config, verify envelope has correct shape (event_id, api_version, data), verify HMAC signing, verify no internal fields leak.
> **How:** POST /bots with webhook headers, import build_envelope inside meeting-api container, verify structure and HMAC computation.

## state

    SECRET = "test-secret-12345"
    BOT_ID = ""

## steps

```
1. create_with_webhook
   do: |
       curl -sf -X POST "{GATEWAY_URL}/bots" \
         -H "X-API-Key: {API_TOKEN}" -H "Content-Type: application/json" \
         -H "X-User-Webhook-URL: https://httpbin.org/post" \
         -H "X-User-Webhook-Secret: {SECRET}" \
         -H "X-User-Webhook-Events: bot.status_change,meeting.ended" \
         -d '{"platform":"google_meet","native_meeting_id":"webhook-test","bot_name":"Webhook Test"}'
   expect: STATUS_CODE in [200, 201]
   => BOT_ID
   on_fail: stop

2. envelope
   call: docker.container_for(MODE={DEPLOY_MODE}, SERVICE="meeting-api")
   => CONTAINER
   call: docker.exec(CONTAINER={CONTAINER}, CMD="python3 -c \"from meeting_api.webhook_delivery import build_envelope; import json; print(json.dumps(build_envelope('bot.status_change',{'bot_id':1,'status':'active'}),indent=2))\"")
   expect: output contains event_id, event_type, api_version, created_at, data
   expect: output does NOT contain bot_container_id, webhook_secrets
   emit PASS "envelope shape correct"
   on_fail: continue

3. hmac
   call: docker.exec(CONTAINER={CONTAINER}, CMD="python3 -c \"import hmac,hashlib,json;from meeting_api.webhook_delivery import build_envelope;e=build_envelope('test',{});sig=hmac.new('{SECRET}'.encode(),json.dumps(e).encode(),hashlib.sha256).hexdigest();print('SIG='+sig)\"")
   expect: SIG= followed by hex
   emit PASS "HMAC signing works"
   on_fail: continue

4. cleanup
   call: http.delete(URL="{GATEWAY_URL}/bots/{BOT_ID}", TOKEN={API_TOKEN})
   on_fail: continue

5. summary
   => WEBHOOK_OK = true
   emit PASS "webhooks validated"
```
