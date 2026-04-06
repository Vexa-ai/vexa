---
needs: [GATEWAY_URL, API_TOKEN, MEETING_PLATFORM]
gives: [MEETING_URL, NATIVE_MEETING_ID]
---

use: lib/http

# Create Meeting

> **Why:** Bot tests need a real meeting to join. This creates one.
> **What:** Open a live Google Meet (automated via CDP) or get a Teams URL from the human.
> **How:** GMeet: create authenticated browser session, navigate to meet.new, extract URL. Teams: human pastes URL.

## state

    MEETING_URL       = ""
    NATIVE_MEETING_ID = ""
    SESSION_TOKEN     = ""

## steps

```
if MEETING_PLATFORM == "google_meet":

    1. check_saved_session
       do: docker exec vexa-minio-1 mc ls local/vexa/ --recursive 2>/dev/null | grep -c "Cookies\|Login"
       if count == 0:
           ask: "No saved browser session. Log in via src/browser first, then type 'done'." [human]
       on_fail: stop

    2. create_session
       call: http.post_json(URL="{GATEWAY_URL}/bots", DATA='{"mode":"browser_session","bot_name":"Meeting Creator","authenticated":true,"use_saved_userdata":true}', TOKEN={API_TOKEN})
       => SESSION_TOKEN = BODY.session_token
       on_fail: stop

    3. wait_active
       call: http.poll_until(URL="{GATEWAY_URL}/bots/status", TOKEN={API_TOKEN}, FIELD="status", VALUE="active", MAX=12, INTERVAL=5)
       on_fail: stop

    4. navigate_meet_new
       do: |
           node -e "
           const {chromium}=require('playwright');
           (async()=>{
               const b=await chromium.connectOverCDP('{GATEWAY_URL}/b/{SESSION_TOKEN}/cdp');
               const p=b.contexts()[0].pages()[0]||await b.contexts()[0].newPage();
               await p.goto('https://meet.new');
               await p.waitForURL('**/meet.google.com/**',{timeout:30000});
               const url=p.url();
               console.log('MEETING_URL='+url);
               const m=url.match(/meet\.google\.com\/([a-z]+-[a-z]+-[a-z]+)/);
               if(m)console.log('NATIVE_MEETING_ID='+m[1]);
               await b.close();
           })().catch(e=>{console.error(e.message);process.exit(1)});
           "
       expect: stdout contains MEETING_URL=
       => MEETING_URL, NATIVE_MEETING_ID
       on_fail: ask

       if MEETING_URL is empty:
           ask: "meet.new went to login — cookies expired. Re-login, then type 'done'." [human]
           retry 4

if MEETING_PLATFORM == "teams":

    1. get_url [human]
       ask: "Create a Teams meeting and paste the full URL (including passcode if any)."
       => MEETING_URL
       > Extract NATIVE_MEETING_ID from URL.
       on_fail: stop

emit PASS "meeting ready: {MEETING_URL}"
```
