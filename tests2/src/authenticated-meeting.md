---
needs: [GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN, API_TOKEN, USER_ID, SESSION_TOKEN]
gives: [AUTH_MEETING_OK]
---

use: lib/http
use: env

# Authenticated Meeting

> **Why:** Authenticated bot joins are the premium path — skip name prompts, bypass waiting rooms, appear as the Google account. If cookies don't load or Chrome args are wrong, the bot silently degrades to anonymous. This proc catches that.
> **What:** Verify the full chain: S3 config injected → cookies downloaded → Chrome persistent context → "Join now" seen → bot joins as account identity.
> **How:** Create bot with `authenticated: true`, inspect BOT_CONFIG for S3 fields, check container logs for download + join type, verify meeting status.

## state

    MEETING_URL       = ""
    NATIVE_MEETING_ID = ""
    BOT_CONTAINER     = ""
    AUTH_MEETING_OK   = false

## steps

```
═══════════════════════════════════════════════════════════════
 TIER 1 — API contract (no meeting needed)
═══════════════════════════════════════════════════════════════

1. s3_config_injected
   > DoD #1: authenticated=true adds S3 config to BOT_CONFIG.
   do: |
       curl -sf -X POST "{GATEWAY_URL}/bots" \
         -H "X-API-Key: {API_TOKEN}" -H "Content-Type: application/json" \
         -d '{"platform":"google_meet","native_meeting_id":"auth-config-test","bot_name":"Auth Config","authenticated":true,"automatic_leave":{"no_one_joined_timeout":30000}}'
   expect: STATUS_CODE in [200, 201]
   => BOT_ID, CONTAINER_NAME from response

   do: docker exec {CONTAINER_NAME} printenv BOT_CONFIG | python3 -c "import sys,json; c=json.load(sys.stdin); print('S3PATH='+str(c.get('userdataS3Path','MISSING'))); print('ENDPOINT='+str(c.get('s3Endpoint','MISSING'))); print('BUCKET='+str(c.get('s3Bucket','MISSING'))); print('AUTH='+str(c.get('authenticated','MISSING')))"
   expect: S3PATH contains "users/{USER_ID}/browser-userdata"
   expect: ENDPOINT is not MISSING
   expect: BUCKET is not MISSING
   expect: AUTH == True
   emit PASS "auth-meeting#1: S3 config in BOT_CONFIG"

   > Wait for timeout to kill it (30s)
   do: sleep 35
   on_fail: stop

2. cookies_downloaded
   > DoD #2: Bot downloads cookies from S3 before launching Chrome.
   > Use the same bot from step 1 — check its logs before it dies.
   do: docker logs {CONTAINER_NAME} 2>&1 | grep -E "S3 sync down|downloading userdata|syncBrowserData"
   expect: output contains "S3 sync down" or "downloading userdata"
   emit PASS "auth-meeting#2: cookies downloaded from S3"
   on_fail: continue

3. chrome_args
   > DoD #6: --password-store=basic in Chrome args.
   do: docker logs {CONTAINER_NAME} 2>&1 | grep -E "password-store|persistent.*context|authenticated.*context"
   expect: output indicates persistent context launched (not incognito)
   emit PASS "auth-meeting#6: persistent context with password-store=basic"
   on_fail: continue

4. diagnostic_screenshot
   > DoD #8: Bot takes screenshot at auth lobby.
   do: docker exec {CONTAINER_NAME} ls /app/storage/screenshots/ 2>/dev/null | grep -E "auth|lobby"
   expect: screenshot file exists
   emit PASS "auth-meeting#8: diagnostic screenshot taken"
   on_fail: continue

5. shared_s3_path
   > DoD #7: Same S3 path works for browser sessions and authenticated bots.
   > Check that the S3 path from step 1 matches what browser sessions use.
   do: |
       BROWSER_PATH=$(curl -sf -X POST "{GATEWAY_URL}/bots" \
         -H "X-API-Key: {API_TOKEN}" -H "Content-Type: application/json" \
         -d '{"mode":"browser_session","bot_name":"Path Check"}' | \
         python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('session_token',''))")
       # Get the browser session's S3 path from its container
       BROWSER_CONTAINER=$(curl -sf "{GATEWAY_URL}/bots/status" -H "X-API-Key: {API_TOKEN}" | \
         python3 -c "import sys,json; [print(b['container_name']) for b in json.load(sys.stdin).get('running_bots',[]) if b.get('data',{}).get('mode')=='browser_session']" | tail -1)
       docker exec $BROWSER_CONTAINER printenv BOT_CONFIG | python3 -c "import sys,json; c=json.load(sys.stdin); print('BROWSER_S3='+str(c.get('userdataS3Path','MISSING')))"
   expect: BROWSER_S3 path matches auth bot's S3PATH (both users/{USER_ID}/browser-userdata)
   > Clean up browser session
   do: curl -sf -X DELETE "{GATEWAY_URL}/bots/browser_session/$(curl -sf "{GATEWAY_URL}/bots/status" -H "X-API-Key: {API_TOKEN}" | python3 -c "import sys,json; [print(b['native_meeting_id']) for b in json.load(sys.stdin).get('running_bots',[]) if b.get('data',{}).get('mode')=='browser_session']" | tail -1)" -H "X-API-Key: {API_TOKEN}"
   emit PASS "auth-meeting#7: shared S3 path"
   on_fail: continue

6. use_saved_userdata_bug
   > DoD #9: use_saved_userdata field silently dropped.
   do: |
       curl -s -X POST "{GATEWAY_URL}/bots" \
         -H "X-API-Key: {API_TOKEN}" -H "Content-Type: application/json" \
         -d '{"platform":"google_meet","native_meeting_id":"field-test","bot_name":"Field Test","use_saved_userdata":true}' \
         -w "\nHTTP:%{http_code}"
   > If use_saved_userdata worked, it would add S3 config. Check:
   do: sleep 5
   do: |
       CONTAINER=$(docker ps --filter "name=meeting-{USER_ID}" --format '{{.Names}}' | head -1)
       docker exec $CONTAINER printenv BOT_CONFIG 2>/dev/null | python3 -c "import sys,json; c=json.load(sys.stdin); print('HAS_S3='+str(bool(c.get('userdataS3Path'))))"
   if HAS_S3 == False:
       emit PASS "auth-meeting#9: confirmed — use_saved_userdata silently ignored (field is 'authenticated')"
   else:
       emit FAIL "use_saved_userdata unexpectedly works — schema may have changed"
   > Clean up
   do: curl -sf -X DELETE "{GATEWAY_URL}/bots/google_meet/field-test" -H "X-API-Key: {API_TOKEN}"
   on_fail: continue

═══════════════════════════════════════════════════════════════
 TIER 2 — Live meeting (needs browser session with Google login)
═══════════════════════════════════════════════════════════════

7. create_meeting
   > Need a live Google Meet to test actual join behavior.
   > Uses existing browser session with saved Google login.
   do: |
       node -e "
       const {chromium}=require('playwright');
       (async()=>{
           const b=await chromium.connectOverCDP('{GATEWAY_URL}/b/{SESSION_TOKEN}/cdp',{timeout:15000});
           const p=b.contexts()[0].pages()[0]||await b.contexts()[0].newPage();
           await p.goto('https://meet.new',{timeout:45000,waitUntil:'domcontentloaded'});
           await p.waitForURL('**/meet.google.com/**',{timeout:45000});
           const m=p.url().match(/meet\.google\.com\/([a-z]+-[a-z]+-[a-z]+)/);
           if(m) console.log(m[1]);
           await b.close();
       })().catch(e=>{console.error(e.message);process.exit(1)});
       "
   => NATIVE_MEETING_ID
   => MEETING_URL = "https://meet.google.com/{NATIVE_MEETING_ID}"
   emit PASS "meeting created: {NATIVE_MEETING_ID}"
   on_fail: stop

8. authenticated_join
   > DoD #3: Authenticated bot sees "Join now" (not "Ask to join").
   > DoD #4: Bot joins as Google account identity.
   do: |
       curl -sf -X POST "{GATEWAY_URL}/bots" \
         -H "X-API-Key: {API_TOKEN}" -H "Content-Type: application/json" \
         -d '{"platform":"google_meet","native_meeting_id":"{NATIVE_MEETING_ID}","bot_name":"Auth Bot","authenticated":true,"transcribe_enabled":true,"automatic_leave":{"no_one_joined_timeout":300000}}'
   expect: STATUS_CODE in [200, 201]
   => BOT_CONTAINER from bots/status

   > Wait for bot to reach lobby or active
   do: |
       for i in $(seq 1 20); do
           STATUS=$(curl -sf -H "X-API-Key: {API_TOKEN}" "{GATEWAY_URL}/bots/status" | \
               python3 -c "import sys,json; [print(b.get('meeting_status','?')) for b in json.load(sys.stdin).get('running_bots',[]) if b.get('native_meeting_id')=='{NATIVE_MEETING_ID}']" 2>/dev/null | head -1)
           [ "$STATUS" = "awaiting_admission" ] || [ "$STATUS" = "active" ] && break
           sleep 5
       done
       echo "STATUS=$STATUS"

   > Check bot logs for join type
   do: docker logs {BOT_CONTAINER} 2>&1 | grep -E "Join now|Switch here|Ask to join|Authenticated mode|anonymous"
   if output contains "Join now" or "Switch here":
       emit PASS "auth-meeting#3: authenticated join — saw 'Join now' or 'Switch here'"
   if output contains "Ask to join" or "anonymous":
       emit FAIL "auth-meeting#3: cookies didn't load — fell back to anonymous join"
   on_fail: continue

9. admit_and_verify
   > Auto-admit the bot if in waiting room.
   if STATUS == "awaiting_admission":
       call: src/admit(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, SESSION_TOKEN={SESSION_TOKEN}, MEETING_PLATFORM="google_meet", NATIVE_MEETING_ID={NATIVE_MEETING_ID})

   > Verify bot reached active
   do: |
       for i in $(seq 1 12); do
           STATUS=$(curl -sf -H "X-API-Key: {API_TOKEN}" "{GATEWAY_URL}/bots/status" | \
               python3 -c "import sys,json; [print(b.get('meeting_status','?')) for b in json.load(sys.stdin).get('running_bots',[]) if b.get('native_meeting_id')=='{NATIVE_MEETING_ID}']" 2>/dev/null | head -1)
           [ "$STATUS" = "active" ] && break
           sleep 5
       done
       echo "STATUS=$STATUS"
   expect: STATUS == "active"
   emit PASS "auth-meeting#4: bot active in meeting"
   on_fail: continue

10. fallback_test
    > DoD #5: expired/missing cookies → bot falls back to anonymous with warning.
    > Create a bot for a DIFFERENT user (no saved cookies) with authenticated=true.
    > The bot should see "Ask to join" and log the warning.

    > Create a throwaway user with no browser session history
    do: |
        USER=$(curl -sf -X POST "{ADMIN_URL}/admin/users" -H "X-Admin-API-Key: {ADMIN_TOKEN}" -H "Content-Type: application/json" -d '{"email":"no-cookies@vexa.ai","name":"No Cookies"}')
        USER_ID_NEW=$(echo "$USER" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
        TOKEN_NEW=$(curl -sf -X POST "{ADMIN_URL}/admin/users/$USER_ID_NEW/tokens?scopes=bot,browser,tx&name=fallback" -H "X-Admin-API-Key: {ADMIN_TOKEN}" -H "Content-Type: application/json" -d '{}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
        echo "USER_ID_NEW=$USER_ID_NEW TOKEN_NEW=$TOKEN_NEW"

    do: |
        curl -sf -X POST "{GATEWAY_URL}/bots" \
          -H "X-API-Key: $TOKEN_NEW" -H "Content-Type: application/json" \
          -d '{"platform":"google_meet","native_meeting_id":"{NATIVE_MEETING_ID}","bot_name":"Fallback Bot","authenticated":true,"automatic_leave":{"no_one_joined_timeout":60000}}'
    > Wait for it to join or timeout
    do: sleep 30

    > Check logs for fallback warning
    do: |
        FALLBACK_CONTAINER=$(docker ps --filter "name=meeting-$USER_ID_NEW" --format '{{.Names}}' | head -1)
        docker logs $FALLBACK_CONTAINER 2>&1 | grep -E "Ask to join|anonymous|WARNING.*Authenticated|cookies.*not"
    if output contains "Ask to join" or "WARNING" or "anonymous":
        emit PASS "auth-meeting#5: fallback to anonymous with warning when no cookies"
    else:
        emit FAIL "auth-meeting#5: no fallback warning logged"
    on_fail: continue

11. cleanup
    > Stop all test bots
    do: curl -sf -X DELETE "{GATEWAY_URL}/bots/google_meet/{NATIVE_MEETING_ID}" -H "X-API-Key: {API_TOKEN}"
    do: curl -sf -X DELETE "{GATEWAY_URL}/bots/google_meet/{NATIVE_MEETING_ID}" -H "X-API-Key: $TOKEN_NEW" 2>/dev/null
    do: sleep 10
    > Verify containers removed
    do: docker ps -a --filter "status=exited" --filter "name=meeting-" --format '{{.Names}}' | wc -l
    expect: 0
    on_fail: continue

12. summary
    => AUTH_MEETING_OK = (steps 1-5 pass for tier 1, steps 8-9 pass for tier 2)
    emit FINDING "authenticated-meeting: tier1={tier1_pass_count}/6 tier2={tier2_pass_count}/4"
```

## Failure modes

| Symptom | Cause | Fix | Learned |
|---|---|---|---|
| BOT_CONFIG missing userdataS3Path | MINIO_ENDPOINT not set in meeting-api env | Check compose env vars | S3 config is conditional on MINIO_ENDPOINT |
| S3 sync down fails silently | aws CLI not in bot image, or wrong credentials | Check bot logs for S3 errors | aws CLI must be installed in the bot image |
| "Ask to join" despite authenticated=true | Cookies expired, or --password-store=basic missing | Check Chrome args and cookie age | Cookies are encrypted — same password-store flag needed in all contexts |
| Bot stuck forever on pre-join | No join button found — Google Meet UI changed | Diagnostic screenshot shows what the page looks like | Always screenshot before throwing |
