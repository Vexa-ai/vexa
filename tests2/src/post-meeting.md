---
needs: [GATEWAY_URL, API_TOKEN, MEETING_PLATFORM, NATIVE_MEETING_ID]
gives: [POST_MEETING_SEGMENTS, RECORDING_UPLOADED]
---

use: lib/http

# Post-Meeting

> **Why:** After a meeting ends, recordings get re-transcribed with batch Whisper for better quality. If this pipeline breaks, users lose the polished transcript.
> **What:** Verify recording uploaded to MinIO, trigger deferred transcription, check speaker attribution and dedup (no duplicate utterances from realtime + deferred).
> **How:** GET /recordings, POST /meetings/{id}/transcribe, GET /transcripts and count deferred segments, diff against realtime for duplicates.

## state

    MEETING_ID            = ""
    POST_MEETING_SEGMENTS = 0
    RECORDING_UPLOADED    = false

## steps

```
1. find_meeting
   call: http.get_json(URL="{GATEWAY_URL}/meetings", TOKEN={API_TOKEN})
   > Match NATIVE_MEETING_ID.
   => MEETING_ID
   on_fail: stop

2. recording
   call: http.get_json(URL="{GATEWAY_URL}/bots/{MEETING_PLATFORM}/{NATIVE_MEETING_ID}/recordings", TOKEN={API_TOKEN})
   if BODY has recordings:
       => RECORDING_UPLOADED = true
       emit PASS "recording uploaded"
   else:
       emit FAIL "no recording"
   on_fail: continue

3. trigger_deferred
   call: http.post_json(URL="{GATEWAY_URL}/meetings/{MEETING_ID}/transcribe", DATA="{}", TOKEN={API_TOKEN})
   if STATUS_CODE == 200: emit PASS "deferred triggered"
   if STATUS_CODE == 409: emit FINDING "already exists"
   on_fail: continue

4. wait
   do: sleep 30

5. fetch
   call: http.get_json(URL="{GATEWAY_URL}/transcripts/{MEETING_PLATFORM}/{NATIVE_MEETING_ID}", TOKEN={API_TOKEN})
   > Count deferred segments (segment_id prefix "deferred:").
   => POST_MEETING_SEGMENTS = deferred count
   emit FINDING "deferred: {POST_MEETING_SEGMENTS} segments"
   on_fail: stop

6. dedup
   > Check no duplicate utterances (both realtime and deferred for same content).
   expect: 0 duplicates
   if duplicates: emit FAIL "duplicate utterances in transcript"
   else: emit PASS "no duplicates"
   on_fail: continue
```
