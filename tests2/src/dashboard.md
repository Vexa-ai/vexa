---
needs: [GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN, DASHBOARD_URL, DEPLOY_MODE, API_TOKEN, TEST_USER]
gives: [DASHBOARD_OK]
---

use: lib/http
use: lib/docker

# Dashboard

> **Why:** Next.js returns 200 even when all backends are broken. API tests pass but the user sees nothing. This proc tests what the user actually sees.
> **What:** Three tiers. T1: backend connectivity (wget from container). T2: auth chain (login → cookie → proxy → data). T3: page-level (load meeting page in browser, verify transcript renders).
> **How:** T1 uses docker exec. T2 uses curl with cookies. T3 uses Playwright headless to load the actual page and check DOM.

## state

    CONTAINER    = ""
    API_URL      = ""
    ADMIN_KEY    = ""
    COOKIE_TOKEN = ""
    TESTED       = 0
    PASSED       = 0

## steps

```
═══════════════════════════════════════════════════════════════
 TIER 1 — Backend connectivity (from inside container)
 Tests: can the dashboard container reach all backends?
═══════════════════════════════════════════════════════════════

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

3. backend_calls
   for CALL in [
       {name: "gateway",    cmd: "wget -q -O /dev/null {API_URL}/"},
       {name: "meetings",   cmd: "wget -q -O - --header='X-API-Key: {API_TOKEN}' {API_URL}/meetings"},
       {name: "bots",       cmd: "wget -q -O - --header='X-API-Key: {API_TOKEN}' {API_URL}/bots/status"},
       {name: "admin",      cmd: "wget -q -O - --header='X-Admin-API-Key: {ADMIN_KEY}' {API_URL}/admin/users?limit=1"},
       {name: "next-auth",  cmd: "wget -q -O - http://localhost:3000/api/auth/session"}
   ]:
       TESTED += 1
       call: docker.exec(CONTAINER={CONTAINER}, CMD="{CALL.cmd}")
       if exits 0: PASSED += 1; emit PASS "t1: {CALL.name}"
       else: emit FAIL "t1: {CALL.name}"
       on_fail: continue

4. verify_no_false_failures
   TESTED += 1
   call: http.get_json(URL="{GATEWAY_URL}/meetings", TOKEN={API_TOKEN})
   for MEETING in BODY:
       if MEETING.status == "failed":
           call: http.get_json(URL="{GATEWAY_URL}/transcripts/{MEETING.platform}/{MEETING.native_meeting_id}", TOKEN={API_TOKEN})
           if BODY has segments and len(segments) > 0:
               emit FAIL "BUG: meeting {MEETING.native_meeting_id} shows 'failed' but has {len(segments)} transcript segments"
               on_fail: continue
   PASSED += 1
   emit PASS "t1: no false failures"
   on_fail: continue

═══════════════════════════════════════════════════════════════
 TIER 2 — Auth chain (login → cookie → proxy → data)
 Tests: can a user log in and fetch data through the dashboard proxy?
═══════════════════════════════════════════════════════════════

5. login
   TESTED += 1
   do: |
       curl -s -X POST "{DASHBOARD_URL}/api/auth/send-magic-link" \
         -H "Content-Type: application/json" \
         -d '{"email":"{TEST_USER}"}' \
         -c /tmp/dash-proc-cookies \
         -w "\nHTTP:%{http_code}"
   expect: response contains "success" and HTTP 200
   => COOKIE_TOKEN from cookie file (vexa-token value)
   PASSED += 1; emit PASS "t2: login"
   on_fail: stop

6. authenticated_meetings
   TESTED += 1
   do: |
       curl -sf -b /tmp/dash-proc-cookies "{DASHBOARD_URL}/api/vexa/meetings" | \
         python3 -c "import sys,json; d=json.load(sys.stdin); print(f'MEETINGS={len(d.get(\"meetings\",[]))}')"
   expect: MEETINGS > 0
   PASSED += 1; emit PASS "t2: meetings list"
   on_fail: continue

7. api_field_contract
   > Core bug: API returns `native_meeting_id`, dashboard uses `platform_specific_id`.
   > The browser's mapMeeting() does the rename. This step verifies the API contract:
   > GET /meetings/{id} must return non-null native_meeting_id so the mapping works.
   TESTED += 1
   > Find a meeting with transcripts to test against.
   do: |
       curl -sf -b /tmp/dash-proc-cookies "{DASHBOARD_URL}/api/vexa/meetings" | \
         python3 -c "
       import sys,json
       meetings=json.load(sys.stdin).get('meetings',[])
       # Pick first meeting with active or completed status
       for m in meetings:
           if m.get('status') in ('active','completed') and m.get('native_meeting_id'):
               print(f'ID={m[\"id\"]}')
               print(f'PLATFORM={m[\"platform\"]}')
               print(f'NATIVE={m[\"native_meeting_id\"]}')
               break
       else:
           print('NO_SUITABLE_MEETING')
       "
   => MEETING_DB_ID, MEETING_PLATFORM, MEETING_NATIVE_ID from output

   if MEETING_DB_ID exists:
       do: |
           curl -sf -b /tmp/dash-proc-cookies "{DASHBOARD_URL}/api/vexa/meetings/{MEETING_DB_ID}" | \
             python3 -c "
           import sys,json
           m=json.load(sys.stdin)
           nid=m.get('native_meeting_id')
           psi=m.get('platform_specific_id')
           print(f'NATIVE_MEETING_ID={nid}')
           print(f'PLATFORM_SPECIFIC_ID={psi}')
           # Contract: native_meeting_id must be present (browser mapMeeting uses it)
           if nid and nid!='None':
               print('CONTRACT=PASS')
           else:
               print('CONTRACT=FAIL')
           "
       if output contains "CONTRACT=PASS":
           PASSED += 1; emit PASS "t2: API field contract (native_meeting_id present)"
       else:
           emit FAIL "BUG: GET /meetings/{id} returns null native_meeting_id — transcript page will be empty"
   on_fail: continue

8. pagination
   > Bug: if GET /bots is slow (>5s timeout), proxy falls back to /bots/status
   > which returns only running bots — no completed meetings, no pagination.
   > This tests that the primary path works AND that pagination params pass through.
   TESTED += 1
   do: |
       python3 -c "
       import json, urllib.request
       # Test 1: small page
       req=urllib.request.Request('{DASHBOARD_URL}/api/vexa/meetings?limit=3&offset=0')
       req.add_header('Cookie','vexa-token={COOKIE_TOKEN}')
       d=json.load(urllib.request.urlopen(req))
       page1=d.get('meetings',[])
       has_more=d.get('has_more',False)
       print(f'PAGE1={len(page1)} has_more={has_more}')
       # Test 2: second page
       req2=urllib.request.Request('{DASHBOARD_URL}/api/vexa/meetings?limit=3&offset=3')
       req2.add_header('Cookie','vexa-token={COOKIE_TOKEN}')
       d2=json.load(urllib.request.urlopen(req2))
       page2=d2.get('meetings',[])
       print(f'PAGE2={len(page2)}')
       # Test 3: pages don't overlap
       ids1=set(m.get('id') for m in page1)
       ids2=set(m.get('id') for m in page2)
       overlap=ids1 & ids2
       print(f'OVERLAP={len(overlap)}')
       if len(page1)==3 and has_more and len(page2)>0 and len(overlap)==0:
           print('PAGINATION=PASS')
       else:
           print('PAGINATION=FAIL')
       "
   if output contains "PAGINATION=PASS":
       PASSED += 1; emit PASS "t2: pagination works (limit/offset/has_more, no overlap)"
   else:
       emit FAIL "t2: pagination broken — check proxy /bots timeout or backend response"
   on_fail: continue

9. transcript_via_proxy
   > Verify transcript data flows through dashboard proxy.
   TESTED += 1
   if MEETING_NATIVE_ID exists:
       do: |
           curl -sf -b /tmp/dash-proc-cookies \
             "{DASHBOARD_URL}/api/vexa/transcripts/{MEETING_PLATFORM}/{MEETING_NATIVE_ID}" | \
             python3 -c "
           import sys,json
           d=json.load(sys.stdin)
           segs=d.get('segments',[]) if isinstance(d,dict) else d
           print(f'SEGMENTS={len(segs)}')
           "
       if SEGMENTS > 0:
           PASSED += 1; emit PASS "t2: transcript via proxy ({SEGMENTS} segments)"
       else:
           emit FAIL "t2: transcript proxy returns 0 segments"
   on_fail: continue

═══════════════════════════════════════════════════════════════
 TIER 3 — Page-level (headless browser loads actual dashboard page)
 Tests: does the user actually see transcript and correct status?
═══════════════════════════════════════════════════════════════

9. page_renders_transcript
   > The definitive test. Load the meeting page in a real browser.
   > Check that transcript text appears in the DOM.
   > This catches: mapMeeting bugs, React rendering bugs, CSS hiding, JS errors.
   TESTED += 1
   if MEETING_DB_ID exists:
       do: |
           node -e "
           const {chromium}=require('playwright');
           (async()=>{
               const b=await chromium.launch({headless:true});
               const ctx=await b.newContext();
               // Set auth cookie
               await ctx.addCookies([{
                   name:'vexa-token',
                   value:'{COOKIE_TOKEN}',
                   domain:'localhost',
                   path:'/'
               }]);
               const p=await ctx.newPage();
               // Navigate to meeting page
               await p.goto('{DASHBOARD_URL}/meetings/{MEETING_DB_ID}',{timeout:15000});
               // Wait for data to load
               await p.waitForTimeout(5000);
               // Check for transcript content in DOM
               const body=await p.textContent('body');
               // Look for any text that matches our known segments
               const hasContent=body.length>200;
               // Check for transcript-specific elements
               const transcriptEls=await p.locator('[data-testid*=transcript],[class*=transcript],[class*=segment]').count();
               // Check for error states
               const hasError=body.includes('error') && body.includes('API');
               console.log('BODY_LEN='+body.length);
               console.log('TRANSCRIPT_ELEMENTS='+transcriptEls);
               console.log('HAS_ERROR='+hasError);
               // Check console for JS errors
               p.on('console',msg=>{if(msg.type()==='error')console.log('JS_ERROR:'+msg.text())});
               if(transcriptEls>0) console.log('PAGE_TRANSCRIPT=PASS');
               else if(hasContent && !hasError) console.log('PAGE_TRANSCRIPT=MAYBE');
               else console.log('PAGE_TRANSCRIPT=FAIL');
               await b.close();
           })().catch(e=>{console.error(e.message);process.exit(1)});
           "
       if output contains "PAGE_TRANSCRIPT=PASS":
           PASSED += 1; emit PASS "t3: transcript renders in browser"
       if output contains "PAGE_TRANSCRIPT=FAIL":
           emit FAIL "BUG: transcript not visible in browser — check mapMeeting, React rendering, JS console"
       if output contains "PAGE_TRANSCRIPT=MAYBE":
           emit FINDING "page has content but no transcript-specific elements — check CSS selectors"
   on_fail: continue

10. page_shows_correct_status
    > Verify the meeting status shown on the page matches the API.
    TESTED += 1
    if MEETING_DB_ID exists:
        do: |
            # Get API status
            API_STATUS=$(curl -sf -b /tmp/dash-proc-cookies \
              "{DASHBOARD_URL}/api/vexa/meetings/{MEETING_DB_ID}" | \
              python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))")
            echo "API_STATUS=$API_STATUS"

            # Get page-rendered status
            node -e "
            const {chromium}=require('playwright');
            (async()=>{
                const b=await chromium.launch({headless:true});
                const ctx=await b.newContext();
                await ctx.addCookies([{name:'vexa-token',value:'{COOKIE_TOKEN}',domain:'localhost',path:'/'}]);
                const p=await ctx.newPage();
                await p.goto('{DASHBOARD_URL}/meetings/{MEETING_DB_ID}',{timeout:15000});
                await p.waitForTimeout(5000);
                const body=(await p.textContent('body')).toLowerCase();
                for(const s of ['active','completed','failed','joining','requested','stopping']){
                    if(body.includes(s)) console.log('PAGE_STATUS='+s);
                }
                await b.close();
            })().catch(e=>{console.error(e.message);process.exit(1)});
            "
        > Compare API_STATUS with PAGE_STATUS. If they match: PASS.
        if API_STATUS in PAGE_STATUS output:
            PASSED += 1; emit PASS "t3: status matches API"
        else:
            emit FAIL "t3: status mismatch — API={API_STATUS} but page shows different"
    on_fail: continue

11. cache_busting
    > After a deploy, the browser may serve old JS bundles.
    > Verify the dashboard sets cache headers that prevent stale bundles.
    TESTED += 1
    do: |
        curl -sI "{DASHBOARD_URL}/" | grep -i "cache-control"
        curl -sI "{DASHBOARD_URL}/_next/static/" | grep -i "cache-control"
    expect: cache-control contains "no-store" or "no-cache" or "must-revalidate"
    > Next.js static chunks use content-hashed filenames — a new build = new URLs.
    > But the HTML page itself must not be cached, or old chunk references persist.
    PASSED += 1; emit PASS "t3: cache headers"
    on_fail: continue

═══════════════════════════════════════════════════════════════
 TIER 2b — Identity and navigation
 Tests: does the right user see the right page?
═══════════════════════════════════════════════════════════════

12. login_identity
    > Bugs found 2026-04-07:
    > 1. /api/auth/me fell back to VEXA_API_KEY env var → wrong user
    > 2. Cookie set with Secure flag on HTTP → browser rejects it → redirect loop
    >
    > Four checks:
    > 1. Magic link API returns correct user
    > 2. Cookie flags correct for deployment protocol (no Secure on HTTP)
    > 3. /api/auth/me with cookie returns correct user
    > 4. Meetings proxy works with the cookie
    TESTED += 1
    do: |
        # Step 1: Login + inspect cookie flags
        HEADERS=$(curl -sv -X POST "{DASHBOARD_URL}/api/auth/send-magic-link" \
          -H "Content-Type: application/json" \
          -d '{"email":"{TEST_USER}"}' \
          -c /tmp/dash-identity-cookies 2>&1)
        LOGIN_EMAIL=$(echo "$HEADERS" | grep -v "^[*<>]" | python3 -c "import sys,json; print(json.load(sys.stdin).get('user',{}).get('email','MISSING'))" 2>/dev/null || echo "PARSE_FAIL")
        echo "LOGIN_EMAIL=$LOGIN_EMAIL"

        # Step 2: Cookie flags — Secure must NOT be set on HTTP deployments
        COOKIE_LINE=$(echo "$HEADERS" | grep -i "set-cookie.*vexa-token" | head -1)
        echo "COOKIE_FLAGS=$COOKIE_LINE"
        PROTOCOL=$(echo "{DASHBOARD_URL}" | grep -o "^https\?")
        if [ "$PROTOCOL" = "http" ] && echo "$COOKIE_LINE" | grep -qi "Secure"; then
            echo "COOKIE_BUG=Secure flag on HTTP — browser will reject cookie"
            echo "IDENTITY=FAIL"
        else:
            # Step 3: /api/auth/me with cookie
            ME_RESP=$(curl -s -b /tmp/dash-identity-cookies "{DASHBOARD_URL}/api/auth/me")
            ME_EMAIL=$(echo "$ME_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('user',{}).get('email','MISSING'))")
            echo "ME_EMAIL=$ME_EMAIL"

            # Step 4: Meetings proxy
            MEETINGS_HTTP=$(curl -s -o /dev/null -w '%{http_code}' -b /tmp/dash-identity-cookies "{DASHBOARD_URL}/api/vexa/meetings")
            echo "MEETINGS_HTTP=$MEETINGS_HTTP"

            if [ "$LOGIN_EMAIL" = "{TEST_USER}" ] && [ "$ME_EMAIL" = "{TEST_USER}" ] && [ "$MEETINGS_HTTP" = "200" ]; then
                echo "IDENTITY=PASS"
            else
                echo "IDENTITY=FAIL"
                [ "$ME_EMAIL" != "{TEST_USER}" ] && echo "BUG: /api/auth/me returns $ME_EMAIL instead of {TEST_USER}"
            fi
        fi
    if output contains "IDENTITY=PASS":
        PASSED += 1; emit PASS "t2: login identity end-to-end ({TEST_USER})"
    else:
        emit FAIL "BUG: identity or cookie flags broken"
    on_fail: continue

13. login_redirect
    > Bug found 2026-04-07: after direct login, page.tsx:131 redirects to /agent
    > instead of /meetings. Agent feature is disabled/experimental.
    TESTED += 1
    do: |
        # Check what the login page hardcodes as redirect target
        grep -n 'router.push' services/dashboard/src/app/login/page.tsx | head -5
    expect: first router.push after direct login is "/" or "/meetings"
    > If it's "/agent", that's the bug. Root "/" redirects to /meetings (app/page.tsx).
    if output contains 'push("/agent")':
        emit FAIL "BUG: login redirects to /agent — should be / or /meetings"
    else:
        PASSED += 1; emit PASS "t2: login redirects to /meetings"
    on_fail: continue

14. bot_create_via_proxy
    > Bug found 2026-04-07: "Start bot" in dashboard fails with generic
    > "The server encountered an issue". POST /bots through proxy → 500.
    > Must test bot creation through the dashboard proxy, not just the gateway.
    TESTED += 1
    do: |
        curl -s -X POST "{DASHBOARD_URL}/api/vexa/bots" \
          -H "Content-Type: application/json" \
          -b /tmp/dash-proc-cookies \
          -d '{"platform":"google_meet","meeting_url":"https://meet.google.com/abc-defg-hij","bot_name":"dashboard-test"}' \
          -w "\nHTTP:%{http_code}"
    => BOT_RESP, HTTP_CODE
    if HTTP_CODE in [200, 201, 202]:
        PASSED += 1; emit PASS "t2: bot creation via dashboard proxy"
    elif HTTP_CODE == 403:
        > 403 = concurrent limit. API is reachable, proxy works. Acceptable.
        PASSED += 1; emit PASS "t2: bot creation via proxy — 403 (limit reached, proxy works)"
    elif HTTP_CODE == 500:
        > 500 = the bug. Check if it's a bot image issue or runtime-api issue.
        emit FAIL "BUG: POST /bots via dashboard proxy returns 500"
        emit FINDING "check: is bot image pullable? is runtime-api reachable from meeting-api?"
    else:
        emit FAIL "t2: bot creation via proxy returned {HTTP_CODE}"
    on_fail: continue

15. summary
    => DASHBOARD_OK = (PASSED == TESTED)
    emit FINDING "dashboard: {PASSED}/{TESTED}"
```

## Failure modes

| Symptom | Cause | Fix | Learned |
|---|---|---|---|
| /bots/status returns 422 | Starlette routes /bots/{meeting_id} before /bots/status | Renamed to /bots/id/{meeting_id} | Static and parameterized routes on same prefix are ambiguous |
| Meeting shows "failed" but has transcripts | exit_code=1 on graceful leave treated as failure | self_initiated_leave during stopping → completed | Exit codes need semantic interpretation |
| POST /bots returns 403 | Concurrent bot limit reached | Not a bug — accept 403 as "API reachable" | Test reachability, not success |
| Magic link returns 503 "Access denied" | VEXA_ADMIN_API_KEY wrong default in compose | Fixed compose: `${ADMIN_API_TOKEN:-changeme}` | Different defaults across services = silent auth failure |
| Transcript page empty after reload | VEXA_API_KEY stale (401) | Fresh token in .env, infra step 11 validates | Token lifecycle not managed = "works then breaks" |
| Transcript page empty despite REST data | `getMeeting()` skipped `mapMeeting()`. native_meeting_id not mapped to platform_specific_id. Transcript URL = `/transcripts/{platform}/undefined`. | Added `mapMeeting(raw)` to `getMeeting()`. Step 7 tests the API contract. Step 9 tests the page. | API test PASS ≠ dashboard works. Must test the actual browser path. |
| Old JS bundle served after deploy | Browser caches Next.js chunks | Next.js uses content-hashed filenames. Step 11 checks HTML cache headers. Hard-refresh needed after deploy. | Cache busting depends on HTML not being cached — chunk filenames change but the page referencing them must too |
| Meetings list shows only running bots, no history | GET /bots times out (5s), proxy falls back to /bots/status which only returns running containers | Increase timeout or remove fallback (per "no fallbacks" rule). Step 8 tests pagination with limit/offset. | Fallback hides the failure — user sees 1 meeting instead of 47 with no error message |
| Login as test@vexa.ai shows admin@vexa.ai | `/api/auth/me` line 14: `token = cookieToken \|\| process.env.VEXA_API_KEY`. Falls back to VEXA_API_KEY (user 1) when cookie is missing or on server-side fetch. UI calls /me on every page load → shows admin. | Remove VEXA_API_KEY fallback from /api/auth/me. Cookie is the only identity source. Step 12 now checks /api/auth/me returns the logged-in user. | Fallback identity sources are a security bug. If cookie is missing, user is unauthenticated — don't guess. |
| Login redirect loop on HTTP | Cookie set with `Secure` flag. `secure: process.env.NODE_ENV === "production"` is always true in Next.js prod builds, even on HTTP. Browser rejects cookie → no session → redirect to /login. | Changed to `secure: isSecureRequest()` which checks NEXTAUTH_URL/DASHBOARD_URL protocol. Fixed in all 4 auth routes. Step 12 checks cookie flags. | curl ignores Secure flag — proc must check Set-Cookie headers explicitly. NODE_ENV ≠ protocol. |
| After login, redirects to /agent | login/page.tsx:131 hardcodes `router.push("/agent")` | Change to `router.push("/")` — root redirects to /meetings via app/page.tsx | Redirect targets must match the feature set. Agent is experimental/disabled. |
| "Start bot" generic 500 via dashboard | POST /bots → meeting-api → _spawn_via_runtime_api() → runtime-api /containers fails | Check: bot image pullable? runtime-api reachable? error-messages.ts hides detail. | Step 14 tests through proxy. 500 with no detail = user can't self-diagnose. |
