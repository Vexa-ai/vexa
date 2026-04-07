---
needs: [GATEWAY_URL, API_TOKEN, DASHBOARD_URL, USER_ID]
gives: [SESSION_TOKEN, CDP_URL, SAVED_STATE]
---

use: lib/http

# Browser Session

> **Why:** Browser sessions let bots reuse human logins (Google, GitHub). If state doesn't persist, every session requires re-login.
> **What:** Two-tier validation. Tier 1 (auto): verify the S3 save/restore pipeline works without any login. Tier 2 (human): verify Google login persists across sessions.
> **How:** Tier 1 writes a marker via CDP localStorage, saves, destroys, recreates, reads marker back. Tier 2 requires human Google login, then verifies meet.new works after restart.

## state

    BOT_ID          = ""
    SESSION_TOKEN   = ""
    CDP_URL         = ""
    CONTAINER_NAME  = ""
    SAVED_STATE     = false
    ROUNDTRIP_OK    = false
    LOGIN_PERSISTED = false

## steps

```
═══════════════════════════════════════════════════════════════
 TIER 1 — Automated (no human, no Google login)
 Tests: session create, CDP, S3 save, S3 restore, data roundtrip
═══════════════════════════════════════════════════════════════

1. create
   call: http.post_json(URL="{GATEWAY_URL}/bots", DATA='{"mode":"browser_session","bot_name":"Browser T1"}', TOKEN={API_TOKEN})
   expect: STATUS_CODE in [200, 201]
   => BOT_ID = BODY.id
   => SESSION_TOKEN = BODY.data.session_token
   => CDP_URL = "{GATEWAY_URL}/b/{SESSION_TOKEN}/cdp"
   on_fail: stop

2. wait_active
   do: sleep 15
   call: http.get_json(URL="{GATEWAY_URL}/bots/status", TOKEN={API_TOKEN})
   expect: running_bots contains browser_session with status running
   => CONTAINER_NAME from running_bots entry
   on_fail: stop

3. verify_cdp
   do: |
       node -e "
       const {chromium}=require('playwright');
       (async()=>{
           const b=await chromium.connectOverCDP('{CDP_URL}');
           const p=b.contexts()[0].pages()[0]||await b.contexts()[0].newPage();
           await p.goto('https://example.com');
           console.log('URL:'+p.url());
           await b.close();
       })().catch(e=>{console.error(e.message);process.exit(1)});
       "
   expect: stdout contains example.com
   emit PASS "CDP proxy works"
   on_fail: stop

4. verify_s3_download
   > Check bot logs confirm S3 download happened on startup.
   do: docker logs {CONTAINER_NAME} 2>&1 | grep "S3 sync down"
   expect: output contains "S3 sync down"
   emit PASS "S3 download on startup"
   on_fail: continue

5. write_marker
   > Write a unique marker into localStorage via CDP. This is the data we'll
   > check survives the destroy→recreate cycle. No Google login needed.
   do: |
       MARKER=$(date +%s)-roundtrip-test
       node -e "
       const {chromium}=require('playwright');
       (async()=>{
           const b=await chromium.connectOverCDP('{CDP_URL}');
           const p=b.contexts()[0].pages()[0]||await b.contexts()[0].newPage();
           await p.goto('https://example.com');
           await p.evaluate(()=>localStorage.setItem('vexa_test_marker','${MARKER}'));
           const v=await p.evaluate(()=>localStorage.getItem('vexa_test_marker'));
           console.log('MARKER_SET='+v);
           await b.close();
       })().catch(e=>{console.error(e.message);process.exit(1)});
       "
   expect: stdout contains MARKER_SET={MARKER}
   => MARKER
   emit PASS "marker written: {MARKER}"
   on_fail: stop

6. explicit_save
   call: http.post_json(URL="{GATEWAY_URL}/b/{SESSION_TOKEN}/save", DATA="{}")
   expect: STATUS_CODE == 200
   emit PASS "explicit save returned 200"
   on_fail: stop

7. verify_minio
   > Confirm cookies landed in the correct MinIO bucket and path.
   do: |
       BUCKET=$(docker exec vexa-meeting-api-1 printenv MINIO_BUCKET)
       AK=$(docker exec vexa-meeting-api-1 printenv MINIO_ACCESS_KEY)
       SK=$(docker exec vexa-meeting-api-1 printenv MINIO_SECRET_KEY)
       docker exec vexa-minio-1 mc alias set local http://localhost:9000 "$AK" "$SK" --quiet 2>/dev/null
       docker exec vexa-minio-1 mc ls "local/$BUCKET/users/{USER_ID}/browser-userdata/browser-data/Default/" 2>/dev/null
   expect: output contains "Cookies"
   expect: output contains "Local Storage"
   emit PASS "cookies + localStorage in MinIO"
   on_fail: stop

8. verify_auto_save
   > Wait 70s for auto-save, verify timestamp refreshed.
   do: |
       BEFORE=$(docker exec vexa-minio-1 mc stat "local/$BUCKET/users/{USER_ID}/browser-userdata/browser-data/Default/Cookies" 2>/dev/null | grep "Last Modified")
       sleep 70
       AFTER=$(docker exec vexa-minio-1 mc stat "local/$BUCKET/users/{USER_ID}/browser-userdata/browser-data/Default/Cookies" 2>/dev/null | grep "Last Modified")
       [ "$BEFORE" != "$AFTER" ] && echo "AUTO_SAVE_OK" || echo "AUTO_SAVE_STALE"
   expect: AUTO_SAVE_OK
   emit PASS "auto-save cycle working"
   on_fail: continue

9. destroy
   call: http.delete(URL="{GATEWAY_URL}/bots/browser_session/{NATIVE_MEETING_ID}", TOKEN={API_TOKEN})
   do: sleep 15
   > Verify container is fully removed (not just stopped).
   do: docker ps -a --filter "name={CONTAINER_NAME}" --format '{{.Names}}' | wc -l
   expect: count == 0
   emit PASS "session destroyed and container removed"
   on_fail: continue

10. recreate
    call: http.post_json(URL="{GATEWAY_URL}/bots", DATA='{"mode":"browser_session","bot_name":"Browser T1b"}', TOKEN={API_TOKEN})
    expect: STATUS_CODE in [200, 201]
    => SESSION_TOKEN = BODY.data.session_token
    => CDP_URL = "{GATEWAY_URL}/b/{SESSION_TOKEN}/cdp"
    => CONTAINER_NAME = (from bots/status after wait)
    do: sleep 15
    on_fail: stop

11. verify_restore_download
    do: docker logs {CONTAINER_NAME} 2>&1 | grep "S3 sync down"
    expect: output contains "S3 sync down"
    emit PASS "restored session downloaded from S3"
    on_fail: continue

12. read_marker
    > The critical automated test: can we read back the marker we wrote?
    do: |
        node -e "
        const {chromium}=require('playwright');
        (async()=>{
            const b=await chromium.connectOverCDP('{CDP_URL}');
            const p=b.contexts()[0].pages()[0]||await b.contexts()[0].newPage();
            await p.goto('https://example.com');
            const v=await p.evaluate(()=>localStorage.getItem('vexa_test_marker'));
            if(v==='{MARKER}') console.log('ROUNDTRIP_OK');
            else console.log('ROUNDTRIP_FAIL:got='+v);
            await b.close();
        })().catch(e=>{console.error(e.message);process.exit(1)});
        "
    if output contains "ROUNDTRIP_OK":
        => ROUNDTRIP_OK = true
        emit PASS "data survived destroy→recreate cycle"
    else:
        => ROUNDTRIP_OK = false
        emit FAIL "BUG: data lost after destroy→recreate — S3 restore pipeline broken"
    on_fail: stop

13. verify_no_stale_locks
    > Chrome lock files should be cleaned after restore.
    do: docker exec {CONTAINER_NAME} ls /tmp/browser-data/SingletonLock 2>&1
    expect: "No such file" or exit code != 0
    emit PASS "no stale lock files"
    on_fail: continue

14. verify_auth_flag
    > Verify that `authenticated: true` produces s3 config in bot_config.
    > Create a meeting bot with authenticated=true, check BOT_CONFIG env contains userdataS3Path.
    do: |
        curl -sf -X POST "{GATEWAY_URL}/bots" \
          -H "X-API-Key: {API_TOKEN}" -H "Content-Type: application/json" \
          -d '{"platform":"google_meet","native_meeting_id":"auth-flag-test","bot_name":"Auth Flag","authenticated":true}' | \
          python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}))"
    > The bot was created — now check its BOT_CONFIG env via runtime API.
    do: sleep 5
    do: docker ps --filter "name=meeting-{USER_ID}" --format '{{.Names}}' | tail -1
    => AUTH_CONTAINER
    do: docker exec {AUTH_CONTAINER} printenv BOT_CONFIG 2>/dev/null | python3 -c "import sys,json; c=json.load(sys.stdin); print('HAS_S3PATH' if c.get('userdataS3Path') else 'NO_S3PATH')"
    if output contains "HAS_S3PATH":
        emit PASS "authenticated flag triggers S3 config"
    else:
        emit FAIL "BUG: authenticated=true but no userdataS3Path in BOT_CONFIG"
    > Cleanup
    do: curl -sf -X DELETE "{GATEWAY_URL}/bots/google_meet/auth-flag-test" -H "X-API-Key: {API_TOKEN}"
    on_fail: continue

15. verify_shutdown_save
    > Stop the browser session gracefully, check logs show save before exit.
    call: http.delete(URL="{GATEWAY_URL}/bots/browser_session/{NEW_NATIVE_MEETING_ID}", TOKEN={API_TOKEN})
    do: sleep 10
    > Container may be gone — check saved logs or MinIO timestamp.
    emit PASS "tier 1 complete"
    on_fail: continue

16. tier1_summary
    if ROUNDTRIP_OK:
        => SAVED_STATE = true
        emit PASS "TIER 1: S3 save/restore pipeline works — data roundtrip confirmed"
    else:
        => SAVED_STATE = false
        emit FAIL "TIER 1: S3 save/restore pipeline broken"
        stop

═══════════════════════════════════════════════════════════════
 TIER 2 — Human gate (Google login persistence)
 Requires: human logs into Google once. Then automated verify.
═══════════════════════════════════════════════════════════════

17. create_for_login
    call: http.post_json(URL="{GATEWAY_URL}/bots", DATA='{"mode":"browser_session","bot_name":"Browser T2"}', TOKEN={API_TOKEN})
    expect: STATUS_CODE in [200, 201]
    => SESSION_TOKEN = BODY.data.session_token
    => CDP_URL = "{GATEWAY_URL}/b/{SESSION_TOKEN}/cdp"
    do: sleep 15
    on_fail: stop

18. check_existing_login
    > Maybe cookies from a prior human login still work. Check before asking.
    do: |
        node -e "
        const {chromium}=require('playwright');
        (async()=>{
            const b=await chromium.connectOverCDP('{CDP_URL}');
            const p=b.contexts()[0].pages()[0]||await b.contexts()[0].newPage();
            await p.goto('https://myaccount.google.com/',{timeout:15000});
            await p.waitForTimeout(3000);
            const url=p.url();
            if(url.includes('accounts.google.com/signin')||url.includes('accounts.google.com/v3/signin'))
                console.log('LOGIN_REQUIRED');
            else if(url.includes('myaccount.google.com'))
                console.log('LOGGED_IN');
            else console.log('UNKNOWN:'+url);
            await b.close();
        })().catch(e=>{console.error(e.message);process.exit(1)});
        "
    if output == "LOGGED_IN":
        emit PASS "existing Google login valid — skipping human step"
        skip to step 20
    on_fail: continue

19. login [human]
    ask: "Open browser session at {DASHBOARD_URL} or VNC. Log into Google as {USER_EMAIL}. Type 'done'."
    > After human logs in, save.
    call: http.post_json(URL="{GATEWAY_URL}/b/{SESSION_TOKEN}/save", DATA="{}")
    expect: STATUS_CODE == 200
    on_fail: stop

20. destroy_and_verify_login
    > Destroy session, recreate, verify Google login survived.

    20a. destroy
         call: http.delete(URL="{GATEWAY_URL}/bots/browser_session/{T2_NATIVE_ID}", TOKEN={API_TOKEN})
         do: sleep 15

    20b. recreate
         call: http.post_json(URL="{GATEWAY_URL}/bots", DATA='{"mode":"browser_session","bot_name":"Browser T2b"}', TOKEN={API_TOKEN})
         expect: STATUS_CODE in [200, 201]
         => SESSION_TOKEN = BODY.data.session_token
         => CDP_URL = "{GATEWAY_URL}/b/{SESSION_TOKEN}/cdp"
         do: sleep 15
         on_fail: stop

    20c. verify_google_login
         do: |
             node -e "
             const {chromium}=require('playwright');
             (async()=>{
                 const b=await chromium.connectOverCDP('{CDP_URL}');
                 const p=b.contexts()[0].pages()[0]||await b.contexts()[0].newPage();
                 await p.goto('https://myaccount.google.com/',{timeout:15000});
                 await p.waitForTimeout(3000);
                 const url=p.url();
                 if(url.includes('myaccount.google.com')&&!url.includes('signin'))
                     console.log('LOGIN_PERSISTED');
                 else console.log('LOGIN_LOST:'+url);
                 await b.close();
             })().catch(e=>{console.error(e.message);process.exit(1)});
             "
         if output == "LOGIN_PERSISTED":
             => LOGIN_PERSISTED = true
             emit PASS "Google login survived session restart"
         else:
             => LOGIN_PERSISTED = false
             emit FAIL "BUG: Google login lost after restart — cookies saved but not restorable"
         on_fail: continue

    20d. verify_meet_new
         > The ultimate test: can we create a meeting?
         if LOGIN_PERSISTED:
             do: |
                 node -e "
                 const {chromium}=require('playwright');
                 (async()=>{
                     const b=await chromium.connectOverCDP('{CDP_URL}');
                     const p=b.contexts()[0].pages()[0]||await b.contexts()[0].newPage();
                     await p.goto('https://meet.new',{timeout:30000});
                     await p.waitForURL('**/meet.google.com/**',{timeout:30000});
                     console.log('MEET_URL='+p.url());
                     await b.close();
                 })().catch(e=>{console.error(e.message);process.exit(1)});
                 "
             if output contains "MEET_URL=":
                 emit PASS "meet.new works — can create meetings"
             else:
                 emit FAIL "meet.new failed despite login"
         on_fail: continue

21. tier2_summary
    emit FINDING "tier1_roundtrip={ROUNDTRIP_OK} tier2_login={LOGIN_PERSISTED}"
    if ROUNDTRIP_OK and LOGIN_PERSISTED:
        emit PASS "FULL PASS: browser session save/restore works end-to-end"
    if ROUNDTRIP_OK and not LOGIN_PERSISTED:
        emit FAIL "S3 pipeline works but Google cookies not restorable — likely Chrome encryption issue"
    if not ROUNDTRIP_OK:
        emit FAIL "S3 pipeline broken — nothing downstream can work"
```

## Failure modes

| Symptom | Cause | Fix | Learned |
|---|---|---|---|
| Old proc checked wrong MinIO bucket | Hardcoded `local/vexa/` but bucket is `vexa-recordings` | Read MINIO_BUCKET from meeting-api env | Never hardcode bucket names |
| `use_saved_userdata` field silently ignored | Schema field is `authenticated`; `MeetingCreate(extra="ignore")` drops unknown fields | Use `authenticated: true` in requests | Always check actual schema field names |
| Auto-save overwrites good cookies with empty ones | Session creates empty profile, auto-save at 60s uploads before login | Guard auto-save: skip if no explicit save has happened yet | Auto-save without guard can clobber good state |
| Cookies in MinIO but login not restored | Chrome cookie encryption uses per-profile key; raw file copy is undecryptable | Ensure `--password-store=basic` on both sessions, or same encryption seed | Chrome cookies are encrypted — transport working doesn't mean payload works |
| Marker roundtrip fails | S3 sync excludes localStorage dirs, or aws CLI not installed in bot | Check BROWSER_CACHE_EXCLUDES doesn't filter Local Storage; verify aws CLI in image | Roundtrip test isolates transport from payload |
