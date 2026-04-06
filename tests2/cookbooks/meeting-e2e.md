---
needs: [MEETING_PLATFORM]
gives: [TRANSCRIPT_SEGMENTS, FINALIZATION_OK]
---

use: env
use: lib/log

# Meeting End-to-End

> **Why:** The core product loop is: create meeting -> launch bot -> transcribe -> finalize. This tests that loop on one platform.
> **What:** Single meeting lifecycle from infra to finalization. Pass MEETING_PLATFORM=google_meet or teams.
> **How:** Calls infra -> api -> browser -> meeting -> bot -> admit -> transcription -> post-meeting -> finalize, sequentially.

## steps

```
1. init_log
   call: log.init(COOKBOOK="meeting-e2e")
   => LOG_FILE, JSONL_FILE
   call: log.emit(EVENT="START", MODULE="meeting-e2e", STEP="init", MSG="starting meeting end-to-end")

2. infra
   call: src/infra
   => GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN, DEPLOY_MODE, DASHBOARD_URL

3. api
   call: src/api(GATEWAY_URL={GATEWAY_URL}, ADMIN_URL={ADMIN_URL}, ADMIN_TOKEN={ADMIN_TOKEN})
   => USER_ID, API_TOKEN

4. browser
   call: src/browser(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, DASHBOARD_URL={DASHBOARD_URL})
   => SESSION_TOKEN, CDP_URL, SAVED_STATE

5. meeting
   call: src/meeting(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_PLATFORM={MEETING_PLATFORM})
   => MEETING_URL, NATIVE_MEETING_ID

6. bot
   call: src/bot(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_URL={MEETING_URL}, MEETING_PLATFORM={MEETING_PLATFORM}, NATIVE_MEETING_ID={NATIVE_MEETING_ID})
   => RECORDER_ID

7. admit
   call: src/admit(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, SESSION_TOKEN={SESSION_TOKEN}, MEETING_PLATFORM={MEETING_PLATFORM}, NATIVE_MEETING_ID={NATIVE_MEETING_ID})

8. transcription
   call: src/transcription(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_PLATFORM={MEETING_PLATFORM}, NATIVE_MEETING_ID={NATIVE_MEETING_ID})
   => TRANSCRIPT_SEGMENTS, WER, SPEAKER_ACCURACY, CHAT_OK

9. post_meeting
   call: src/post-meeting(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_PLATFORM={MEETING_PLATFORM}, NATIVE_MEETING_ID={NATIVE_MEETING_ID})

10. finalize
    call: src/finalize(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_PLATFORM={MEETING_PLATFORM}, NATIVE_MEETING_ID={NATIVE_MEETING_ID})
    => FINALIZATION_OK

11. summary
    emit FINDING "segments={TRANSCRIPT_SEGMENTS} wer={WER} finalized={FINALIZATION_OK}"

12. finish
    call: log.summary(MODULE="meeting-e2e", TOTAL_STEPS={step_count}, PASSED={pass_count}, FAILED={fail_count}, FIXED={fixed_count}, SKIPPED={skip_count})
    call: log.close()
    call: log.emit(EVENT="FINDING", MODULE="meeting-e2e", STEP="finish", MSG="logs at {LOG_FILE}")
```
