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

    SPEAKER_TOKEN_A = ""
    SPEAKER_TOKEN_B = ""
    SPEAKER_BOT_A   = ""
    SPEAKER_BOT_B   = ""
    SEGMENTS        = 0
    WER             = 0
    SPEAKER_ACCURACY = 0
    CHAT_OK         = false

## steps

```
1. speaker_tokens
   > Two separate speaker users so the recorder hears two distinct participants.
   > A bot can't hear itself — only audio from OTHER participants is captured.
   > Speaker A and Speaker B alternate utterances; the recorder captures both.
   do: cat secrets/staging.env 2>/dev/null | grep SPEAKER_TOKEN_A | cut -d= -f2
   => SPEAKER_TOKEN_A
   do: cat secrets/staging.env 2>/dev/null | grep SPEAKER_TOKEN_B | cut -d= -f2
   => SPEAKER_TOKEN_B
   if SPEAKER_TOKEN_A is empty:
       > Create speaker user A via admin API
       call: http.post_json(URL="{ADMIN_URL}/admin/users", DATA='{"email":"speaker-a@vexa.ai","name":"Alice"}', ADMIN_HEADER="X-Admin-API-Key: {ADMIN_TOKEN}")
       => SPEAKER_A_ID = BODY.id
       call: http.post_json(URL="{ADMIN_URL}/admin/users/{SPEAKER_A_ID}/tokens?scopes=bot,browser,tx&name=speaker-a", DATA="{}", ADMIN_HEADER="X-Admin-API-Key: {ADMIN_TOKEN}")
       => SPEAKER_TOKEN_A = BODY.token
   if SPEAKER_TOKEN_B is empty:
       > Create speaker user B via admin API
       call: http.post_json(URL="{ADMIN_URL}/admin/users", DATA='{"email":"speaker-b@vexa.ai","name":"Bob"}', ADMIN_HEADER="X-Admin-API-Key: {ADMIN_TOKEN}")
       => SPEAKER_B_ID = BODY.id
       call: http.post_json(URL="{ADMIN_URL}/admin/users/{SPEAKER_B_ID}/tokens?scopes=bot,browser,tx&name=speaker-b", DATA="{}", ADMIN_HEADER="X-Admin-API-Key: {ADMIN_TOKEN}")
       => SPEAKER_TOKEN_B = BODY.token
   on_fail: stop

2. launch_speakers
   > Launch both speaker bots into the meeting.
   call: http.post_json(
       URL="{GATEWAY_URL}/bots",
       DATA='{"platform":"{MEETING_PLATFORM}","native_meeting_id":"{NATIVE_MEETING_ID}","bot_name":"Alice","voice_agent_enabled":true,"automatic_leave":{"no_one_joined_timeout":300000}}',
       TOKEN={SPEAKER_TOKEN_A}
   )
   => SPEAKER_BOT_A = BODY.id

   call: http.post_json(
       URL="{GATEWAY_URL}/bots",
       DATA='{"platform":"{MEETING_PLATFORM}","native_meeting_id":"{NATIVE_MEETING_ID}","bot_name":"Bob","voice_agent_enabled":true,"automatic_leave":{"no_one_joined_timeout":300000}}',
       TOKEN={SPEAKER_TOKEN_B}
   )
   => SPEAKER_BOT_B = BODY.id
   on_fail: stop

3. admit_speakers
   > Admit both speaker bots via auto-admit proc (or human fallback).
   call: src/admit(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, SESSION_TOKEN={SESSION_TOKEN}, MEETING_PLATFORM={MEETING_PLATFORM}, NATIVE_MEETING_ID={NATIVE_MEETING_ID})
   > May need to run twice — once per bot.
   call: src/admit(GATEWAY_URL={GATEWAY_URL}, API_TOKEN={API_TOKEN}, SESSION_TOKEN={SESSION_TOKEN}, MEETING_PLATFORM={MEETING_PLATFORM}, NATIVE_MEETING_ID={NATIVE_MEETING_ID})
   on_fail: stop

4. wait_speakers
   call: http.poll_until(URL="{GATEWAY_URL}/bots/status", TOKEN={SPEAKER_TOKEN_A}, FIELD="status", VALUE="active", MAX=12, INTERVAL=10)
   call: http.poll_until(URL="{GATEWAY_URL}/bots/status", TOKEN={SPEAKER_TOKEN_B}, FIELD="status", VALUE="active", MAX=12, INTERVAL=10)
   on_fail: stop

5. send_tts
   > Alternate TTS between Speaker A and Speaker B.
   > Recorder hears both as distinct participants → multi-speaker attribution.
   > Use different voices so audio characteristics differ.
   for i, UTTERANCE in enumerate(GROUND_TRUTH):
       if i % 2 == 0:
           > Speaker A (Alice)
           call: http.post_json(
               URL="{GATEWAY_URL}/bots/{MEETING_PLATFORM}/{NATIVE_MEETING_ID}/speak",
               DATA='{"text":"{UTTERANCE.text}","voice":"alloy"}',
               TOKEN={SPEAKER_TOKEN_A}
           )
       else:
           > Speaker B (Bob)
           call: http.post_json(
               URL="{GATEWAY_URL}/bots/{MEETING_PLATFORM}/{NATIVE_MEETING_ID}/speak",
               DATA='{"text":"{UTTERANCE.text}","voice":"nova"}',
               TOKEN={SPEAKER_TOKEN_B}
           )
       expect: STATUS_CODE == 202
       do: sleep 8
       on_fail: continue
   emit PASS "sent {len(GROUND_TRUTH)} utterances (alternating 2 speaker bots)"

6. wait
   do: sleep 30

7. fetch_rest
   call: http.get_json(URL="{GATEWAY_URL}/transcripts/{MEETING_PLATFORM}/{NATIVE_MEETING_ID}", TOKEN={API_TOKEN})
   expect: segments exist
   => SEGMENTS = len(BODY.segments)
   => TRANSCRIPT_SEGMENTS = SEGMENTS
   emit FINDING "REST: {SEGMENTS} segments"
   on_fail: stop

7b. verify_ws_delivery
    > Subscribe to WS and check that segments arrive with non-empty text.
    > This catches the bug where REST works but WS delivers empty text.
    call: websocket.subscribe_live(MEETING_PLATFORM={MEETING_PLATFORM}, NATIVE_MEETING_ID={NATIVE_MEETING_ID})
    if WS segments have non-empty text:
        emit PASS "WS: live segments with text"
    else:
        emit FAIL "BUG: WS delivers empty text — REST works, WS broken"
        > Run websocket.debug_redis_channel to isolate publisher vs relay
    on_fail: continue

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

10. rapid_alternation
    > Bug: rapid speaker changes render incorrectly.
    > Send short utterances with <1s gaps, verify speaker attribution.

    RAPID_CORRECT = 0
    RAPID_TOTAL = len(GROUND_TRUTH_RAPID)

    for UTTERANCE in GROUND_TRUTH_RAPID:
        call: http.post_json(
            URL="{GATEWAY_URL}/bots/{MEETING_PLATFORM}/{NATIVE_MEETING_ID}/speak",
            DATA='{"text":"{UTTERANCE.text}","voice":"amy"}',
            TOKEN={SPEAKER_TOKEN}
        )
        > Wait for the utterance to finish. Duration varies: 3s to 60s.
        > Add 2s buffer for TTS synthesis + playback start.
        do: sleep {UTTERANCE.duration + 2}
        on_fail: continue

    > Wait for pipeline to process the last segments.
    do: sleep 30

    call: http.get_json(URL="{GATEWAY_URL}/transcripts/{MEETING_PLATFORM}/{NATIVE_MEETING_ID}", TOKEN={API_TOKEN})
    > Find segments matching rapid utterances.
    > For each, check speaker matches GROUND_TRUTH_RAPID.
    for SEGMENT matching GROUND_TRUTH_RAPID utterances:
        if SEGMENT.speaker matches expected speaker:
            RAPID_CORRECT += 1
        else:
            emit FINDING "rapid: expected {expected_speaker}, got {SEGMENT.speaker} for '{SEGMENT.text}'"

    => RAPID_ACCURACY = RAPID_CORRECT / RAPID_TOTAL
    emit FINDING "rapid speaker accuracy: {RAPID_CORRECT}/{RAPID_TOTAL} ({RAPID_ACCURACY})"
    if RAPID_ACCURACY >= 0.75:
        emit PASS "rapid alternation: speaker attribution acceptable"
    else:
        emit FAIL "rapid alternation: speaker attribution broken ({RAPID_ACCURACY})"
    on_fail: continue

11. summary
    emit FINDING "segments={SEGMENTS} wer={WER} speaker={SPEAKER_ACCURACY} rapid={RAPID_ACCURACY} chat={CHAT_OK}"
```

## Failure modes

| Symptom | Cause | Fix | Learned |
|---|---|---|---|
| Rapid speaker changes render as noise | Overlapping segment timestamps (two people talking simultaneously) interleave when sorted by start_time | Dashboard needs segment grouping: don't break a speaker's block for a short interleave from another speaker | 2026-04-06: 53-min meeting showed Slawa/Dmitriy segments overlapping by 5-10s. Root cause is data ordering, not attribution |
| Speaker accuracy looks bad but pipeline is correct | Same-speaker segments split by interleaved other-speaker segment — each gets a new speaker header | Rendering issue, not transcription issue — evaluate accuracy at segment level, not visual level | Distinguish pipeline accuracy (correct) from display accuracy (broken) |
| WER good on standard TTS but bad on rapid | Short utterances ("Yes.", "No.") may be below Whisper's minimum audio duration | Check minAudioDuration config in bot; short utterances may be dropped or merged | TTS utterance duration must exceed the bot's audio buffer threshold |
| 0 segments from speaker bot | Bot can't hear itself — TTS plays into the meeting but the same bot's capture doesn't pick it up | Use two bots: speaker (tts@vexa.ai) and listener (test@vexa.ai) | A bot cannot hear itself — always use separate speaker and listener |
| Recorder sees 0 audio streams from other bots | Google Meet doesn't create `<audio>` elements for bot-to-bot audio. TTS plays via PulseAudio→virtual mic but the receiving bot's browser doesn't produce per-participant `<audio>` elements for anonymous/bot participants. | Multi-speaker attribution requires at least one human speaker. Pure bot-to-bot TTS is not captured by the recorder. | 2026-04-07: 3 bots in meeting (Recorder + Alice + Bob). Alice/Bob sent TTS. Recorder: "No active media elements with audio found", 0 streams. The [human] gate on multi-speaker testing cannot be removed. |
