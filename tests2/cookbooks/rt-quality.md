---
needs: [MEETING_PLATFORM]
gives: [WER, SPEAKER_ACCURACY, DELIVERY_OK]
---

use: env
use: lib/log

# RT Quality

> **Why:** Transcription quality degrades silently. You need numbers (WER, speaker accuracy) to catch regressions.
> **What:** Collect ground truth from a live meeting, replay offline, score against known input, validate delivery consistency.
> **How:** Calls rt-collect (live capture) -> rt-replay (offline scoring) -> rt-delivery (WS/REST consistency). One live meeting, then unlimited offline iterations.

## steps

```
1. init_log
   call: log.init(COOKBOOK="rt-quality")
   => LOG_FILE, JSONL_FILE
   call: log.emit(EVENT="START", MODULE="rt-quality", STEP="init", MSG="starting RT quality validation")

2. infra
   call: src/infra
   => GATEWAY_URL, ADMIN_URL, ADMIN_TOKEN, DEPLOY_MODE

3. api
   call: src/api(GATEWAY_URL={GATEWAY_URL}, ADMIN_URL={ADMIN_URL}, ADMIN_TOKEN={ADMIN_TOKEN})
   => USER_ID, API_TOKEN

4. collect
   call: src/rt-collect(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_PLATFORM={MEETING_PLATFORM})
   => DATASET_PATH, GROUND_TRUTH_COUNT

5. replay
   call: src/rt-replay(DATASET_PATH={DATASET_PATH})
   => WER, SPEAKER_ACCURACY, COMPLETENESS

6. delivery
   call: src/rt-delivery(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, DATASET_PATH={DATASET_PATH}, MEETING_PLATFORM={MEETING_PLATFORM}, NATIVE_MEETING_ID={NATIVE_MEETING_ID})
   => DELIVERY_OK, WS_REST_MATCH, PHANTOM_COUNT

7. summary
   emit FINDING "WER={WER} speaker={SPEAKER_ACCURACY} complete={COMPLETENESS} delivery={DELIVERY_OK} phantoms={PHANTOM_COUNT}"

8. finish
   call: log.summary(MODULE="rt-quality", TOTAL_STEPS={step_count}, PASSED={pass_count}, FAILED={fail_count}, FIXED={fixed_count}, SKIPPED={skip_count})
   call: log.close()
   call: log.emit(EVENT="FINDING", MODULE="rt-quality", STEP="finish", MSG="logs at {LOG_FILE}")
```
