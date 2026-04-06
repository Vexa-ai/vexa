---
needs: []
gives: [RECORDER_ID, TRANSCRIPT_SEGMENTS, FINALIZATION_OK, WEBSOCKET_OK, LIFECYCLE_OK, DASHBOARD_OK]
---

use: env
use: lib/log

# Lifecycle Debug

> **Why:** Bot lifecycle is broken — bots fail to stop, WS not delivering, transcription not working. Need to validate every link in the chain.
> **What:** Full chain: infra → api → browser → meeting → bot → transcription → finalize → websocket → containers.
> **How:** Uses all existing nodes. Hosts meeting via saved browser session on 2280905@gmail.com.

## steps

```
1. init
   call: log.init(COOKBOOK="lifecycle-debug")

ensure: GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN, DEPLOY_MODE, DASHBOARD_URL
    from: src/infra
    check: curl -sf {GATEWAY_URL}/

ensure: API_TOKEN
    from: src/api
    check: curl -sf {GATEWAY_URL}/bots/status -H "X-API-Key: {API_TOKEN}"

ensure: TEST_USER
    default: "2280905@gmail.com"

2. browser
   > Create authenticated browser session with saved Google login.
   call: src/browser(
       GATEWAY_URL={GATEWAY_URL},
       API_TOKEN={API_TOKEN},
       DASHBOARD_URL={DASHBOARD_URL}
   )
   => SESSION_TOKEN, CDP_URL, SAVED_STATE
   on_fail: stop

3. meeting
   > Navigate to meet.new, extract meeting URL.
   call: src/meeting(
       GATEWAY_URL={GATEWAY_URL},
       API_TOKEN={API_TOKEN},
       MEETING_PLATFORM="google_meet"
   )
   => MEETING_URL, NATIVE_MEETING_ID
   on_fail: stop

4. bot
   call: src/bot(
       GATEWAY_URL={GATEWAY_URL},
       API_TOKEN={API_TOKEN},
       MEETING_URL={MEETING_URL},
       MEETING_PLATFORM="google_meet",
       NATIVE_MEETING_ID={NATIVE_MEETING_ID}
   )
   => RECORDER_ID, BOT_STATUS
   on_fail: stop

5. transcription
   call: src/transcription(
       GATEWAY_URL={GATEWAY_URL},
       API_TOKEN={API_TOKEN},
       MEETING_PLATFORM="google_meet",
       NATIVE_MEETING_ID={NATIVE_MEETING_ID}
   )
   => TRANSCRIPT_SEGMENTS, WER, SPEAKER_ACCURACY, CHAT_OK
   on_fail: continue

6. finalize
   call: src/finalize(
       GATEWAY_URL={GATEWAY_URL},
       API_TOKEN={API_TOKEN},
       MEETING_PLATFORM="google_meet",
       NATIVE_MEETING_ID={NATIVE_MEETING_ID}
   )
   => FINALIZATION_OK
   on_fail: continue

7. websocket
   call: src/websocket(
       GATEWAY_URL={GATEWAY_URL},
       API_TOKEN={API_TOKEN}
   )
   => WEBSOCKET_OK
   on_fail: continue

8. containers
   call: src/containers(
       GATEWAY_URL={GATEWAY_URL},
       API_TOKEN={API_TOKEN},
       DEPLOY_MODE={DEPLOY_MODE}
   )
   => LIFECYCLE_OK, ORPHAN_COUNT, ZOMBIE_COUNT
   on_fail: continue

9. dashboard
   call: src/dashboard(
       GATEWAY_URL={GATEWAY_URL},
       ADMIN_URL={ADMIN_URL},
       ADMIN_TOKEN={ADMIN_TOKEN},
       DASHBOARD_URL={DASHBOARD_URL},
       DEPLOY_MODE={DEPLOY_MODE},
       API_TOKEN={API_TOKEN},
       TEST_USER={TEST_USER}
   )
   => DASHBOARD_OK
   on_fail: continue

10. summary
   call: log.summary(MODULE="lifecycle-debug", TOTAL_STEPS=8, PASSED={pass_count}, FAILED={fail_count}, FIXED=0, SKIPPED={skip_count})
   call: log.close()
```
