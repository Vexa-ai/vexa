---
needs: [GATEWAY_URL, API_TOKEN, MEETING_PLATFORM]
gives: [DATASET_PATH, GROUND_TRUTH_COUNT]
---

use: lib/http
use: env

# RT Collection

> **Why:** Quality measurement needs ground truth. TTS input text is the known-correct reference for scoring.
> **What:** Host a live meeting, send scripted TTS utterances from multiple speakers, capture the pipeline's output as a dataset.
> **How:** Create meeting, launch recorder + speaker bots, send each GROUND_TRUTH utterance via POST /speak, save REST segments to testdata/ for offline replay.

## state

    MEETING_URL       = ""
    NATIVE_MEETING_ID = ""
    RECORDER_ID       = ""
    SPEAKER_BOTS      = []
    DATASET_PATH      = ""

## steps

```
1. meeting
   if MEETING_PLATFORM == "google_meet":
       call: src/meeting(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, MEETING_PLATFORM="google_meet")
       => MEETING_URL, NATIVE_MEETING_ID
   if MEETING_PLATFORM == "teams":
       ask: "Paste Teams meeting URL." [human]
       => MEETING_URL, NATIVE_MEETING_ID
   on_fail: stop

2. recorder
   call: http.post_json(URL="{GATEWAY_URL}/bots", DATA='{"platform":"{MEETING_PLATFORM}","native_meeting_id":"{NATIVE_MEETING_ID}","bot_name":"Recorder","transcribe_enabled":true,"automatic_leave":{"no_one_joined_timeout":300000}}', TOKEN={API_TOKEN})
   => RECORDER_ID = BODY.id
   on_fail: stop

3. admit_recorder [human]
   ask: "Admit Recorder bot, type 'done'."
   on_fail: stop

4. wait_recorder
   call: http.poll_until(URL="{GATEWAY_URL}/bots/status", TOKEN={API_TOKEN}, FIELD="status", VALUE="active", MAX=12, INTERVAL=10)
   on_fail: stop

5. speakers
   for SPEAKER in unique speakers in GROUND_TRUTH:
       call: http.post_json(URL="{GATEWAY_URL}/bots", DATA='{"platform":"{MEETING_PLATFORM}","native_meeting_id":"{NATIVE_MEETING_ID}","bot_name":"{SPEAKER}","voice_agent_enabled":true,"automatic_leave":{"no_one_joined_timeout":300000}}', TOKEN={speaker_token})
       SPEAKER_BOTS.append({name: SPEAKER, id: BODY.id})
       on_fail: continue

   for BOT in SPEAKER_BOTS:
       ask: "Admit '{BOT.name}' bot, type 'done'." [human]

6. send_utterances
   for UTTERANCE in GROUND_TRUTH:
       call: http.post_json(URL="{GATEWAY_URL}/bots/{MEETING_PLATFORM}/{NATIVE_MEETING_ID}/speak", DATA='{"text":"{UTTERANCE.text}","voice":"amy"}', TOKEN={token for UTTERANCE.speaker})
       do: sleep 3
       on_fail: continue
   => GROUND_TRUTH_COUNT = len(GROUND_TRUTH)

7. capture
   do: |
       DATASET="rt-{MEETING_PLATFORM}-$(date +%y%m%d-%H%M)"
       mkdir -p tests/testdata/$DATASET
       curl -sf -H "X-API-Key: {API_TOKEN}" "{GATEWAY_URL}/transcripts/{MEETING_PLATFORM}/{NATIVE_MEETING_ID}" > tests/testdata/$DATASET/rest-segments.json
   => DATASET_PATH = tests/testdata/$DATASET
   emit PASS "dataset at {DATASET_PATH}"
   on_fail: continue

8. stop_all
   call: http.delete(URL="{GATEWAY_URL}/bots/{RECORDER_ID}", TOKEN={API_TOKEN})
   for BOT in SPEAKER_BOTS:
       call: http.delete(URL="{GATEWAY_URL}/bots/{BOT.id}", TOKEN={token})
   on_fail: continue
```
