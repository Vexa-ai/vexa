---
needs: []
gives: [BUG1_OK, BUG2_OK]
---

use: env
use: lib/log

# Bug Reproduction

> **Why:** Two known bugs: (1) dashboard shows "failed" for working meetings, (2) rapid speaker interchange renders incorrectly. This cookbook reproduces both and verifies fixes.
> **What:** Run the minimal proc chain to hit each bug. Skip what's already there.
> **How:** Ensure infra+api, then run dashboard check for bug 1 and transcription rapid test for bug 2.

## steps

```
1. init
   call: log.init(COOKBOOK="bugs")

ensure: GATEWAY_URL, ADMIN_TOKEN, DEPLOY_MODE, DASHBOARD_URL
    from: src/infra
    check: curl -sf {GATEWAY_URL}/

ensure: API_TOKEN
    from: src/api
    check: curl -sf {GATEWAY_URL}/bots/status -H "X-API-Key: {API_TOKEN}"

2. bug1_false_failed
   > Meeting status shows "failed" in dashboard even when meeting worked.
   > Dashboard proc now checks: any "failed" meeting with transcript segments = bug.
   call: src/dashboard(GATEWAY_URL={GATEWAY_URL}, ADMIN_URL={ADMIN_URL}, ADMIN_TOKEN={ADMIN_TOKEN}, DASHBOARD_URL={DASHBOARD_URL}, DEPLOY_MODE={DEPLOY_MODE}, API_TOKEN={API_TOKEN})
   => DASHBOARD_OK
   => BUG1_OK = DASHBOARD_OK
   if not BUG1_OK:
       emit FAIL "bug1: false failures still present in dashboard"
   else:
       emit PASS "bug1: no false failures"

3. bug2_rapid_speakers
   > Rapid speaker alternation renders incorrectly.
   > Need an active meeting with bot. If we have one, test directly.
   > If not, build up the chain.

   ensure: SESSION_TOKEN
       from: src/browser
       check: curl -sf {GATEWAY_URL}/bots/status -H "X-API-Key: {API_TOKEN}"

   ensure: MEETING_URL, NATIVE_MEETING_ID
       from: src/meeting(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_PLATFORM="google_meet")

   ensure: BOT_ADMITTED
       from: src/bot(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_URL={MEETING_URL}, MEETING_PLATFORM="google_meet", NATIVE_MEETING_ID={NATIVE_MEETING_ID}) + src/admit

   call: src/transcription(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_PLATFORM="google_meet", NATIVE_MEETING_ID={NATIVE_MEETING_ID})
   => RAPID_ACCURACY
   => BUG2_OK = RAPID_ACCURACY >= 0.75

   if not BUG2_OK:
       emit FAIL "bug2: rapid speaker attribution broken ({RAPID_ACCURACY})"
   else:
       emit PASS "bug2: rapid speaker attribution acceptable"

4. cleanup
   if NATIVE_MEETING_ID exists:
       call: src/finalize(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_PLATFORM="google_meet", NATIVE_MEETING_ID={NATIVE_MEETING_ID})

5. summary
   call: log.summary(MODULE="bugs", TOTAL_STEPS=2, PASSED={pass_count}, FAILED={fail_count})
   call: log.close()
   emit FINDING "bug1={BUG1_OK} bug2={BUG2_OK}"
```
