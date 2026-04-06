---
needs: [GATEWAY_URL, API_TOKEN, MEETING_PLATFORM, NATIVE_MEETING_ID]
gives: [TRANSCRIPT_SEGMENTS, WER, SPEAKER_ACCURACY, CHAT_OK]
---

use: lib/http
use: env

# Transcription

> **Why:** Transcription is the product. If audio goes in and text doesn't come out, nothing else matters.
> **What:** Send known TTS text via speaker bot, verify listener bot produces transcript segments, evaluate word-by-word against ground truth, test meeting chat.
> **How:** Two bots in same meeting (speaker + listener). Send TTS via POST /speak, fetch segments via GET /transcripts, compute WER and speaker accuracy, POST/GET chat messages.

## state

    SPEAKER_TOKEN  = ""
    SPEAKER_BOT_ID = ""
    SEGMENTS       = 0
    WER            = 0
    SPEAKER_ACCURACY = 0
    CHAT_OK        = false

## steps

```
1. speaker_token
   do: cat secrets/staging.env 2>/dev/null | grep SPEAKER_TOKEN | cut -d= -f2
   => SPEAKER_TOKEN
   if SPEAKER_TOKEN is empty:
       ask: "Need API token for {SPEAKER_EMAIL}. Provide it." [human]
       => SPEAKER_TOKEN
   on_fail: stop

2. launch_speaker
   call: http.post_json(
       URL="{GATEWAY_URL}/bots",
       DATA='{"platform":"{MEETING_PLATFORM}","native_meeting_id":"{NATIVE_MEETING_ID}","bot_name":"Speaker","voice_agent_enabled":true,"automatic_leave":{"no_one_joined_timeout":300000}}',
       TOKEN={SPEAKER_TOKEN}
   )
   => SPEAKER_BOT_ID = BODY.id
   on_fail: stop

3. admit_speaker [human]
   ask: "Speaker bot joining meeting. Admit it, then type 'done'."
   on_fail: stop

4. wait_speaker
   call: http.poll_until(URL="{GATEWAY_URL}/bots/status", TOKEN={SPEAKER_TOKEN}, FIELD="status", VALUE="active", MAX=12, INTERVAL=10)
   on_fail: stop

5. send_tts
   for UTTERANCE in GROUND_TRUTH:
       call: http.post_json(
           URL="{GATEWAY_URL}/bots/{MEETING_PLATFORM}/{NATIVE_MEETING_ID}/speak",
           DATA='{"text":"{UTTERANCE.text}","voice":"amy"}',
           TOKEN={SPEAKER_TOKEN}
       )
       expect: STATUS_CODE == 202
       do: sleep 8
       on_fail: continue
   emit PASS "sent {len(GROUND_TRUTH)} utterances"

6. wait
   do: sleep 30

7. fetch
   call: http.get_json(URL="{GATEWAY_URL}/transcripts/{MEETING_PLATFORM}/{NATIVE_MEETING_ID}", TOKEN={API_TOKEN})
   expect: segments exist
   => SEGMENTS = len(BODY.segments)
   => TRANSCRIPT_SEGMENTS = SEGMENTS
   emit FINDING "{SEGMENTS} segments"
   on_fail: stop

8. evaluate
   > Compare each segment to GROUND_TRUTH word-by-word.
   > Compute WER, speaker accuracy, check for duplicates.
   > This is analytical — read and compare.

   expect: WER < 0.15
   expect: SPEAKER_ACCURACY == 1.0
   expect: 0 duplicates
   => WER, SPEAKER_ACCURACY
   on_fail: continue

9. chat
   call: http.post_json(URL="{GATEWAY_URL}/bots/{MEETING_PLATFORM}/{NATIVE_MEETING_ID}/chat", DATA='{"text":"hello from test"}', TOKEN={API_TOKEN})
   if STATUS_CODE == 202:
       call: http.get_json(URL="{GATEWAY_URL}/bots/{MEETING_PLATFORM}/{NATIVE_MEETING_ID}/chat", TOKEN={API_TOKEN})
       if BODY contains "hello from test":
           => CHAT_OK = true
           emit PASS "chat works"
       else:
           emit FAIL "chat: message not found"
   on_fail: continue

10. summary
    emit FINDING "segments={SEGMENTS} wer={WER} speaker={SPEAKER_ACCURACY} chat={CHAT_OK}"
```
