---
id: test/rt-collection
type: validation
requires: [test/infra-up, test/bot-lifecycle]
produces: [DATASET_PATH, GROUND_TRUTH_COUNT, RAW_AUDIO_COUNT]
mode: hybrid
---

# RT Collection — Live Meeting Ground Truth Capture

> Follows [RULES.md](RULES.md). This procedure owns its scripts.

Host a live meeting, send TTS bots with scripted ground truth, capture raw data for offline replay and scoring.

## DoD items this test owns

| Feature | # | Check |
|---------|---|-------|
| gmeet | 1 | Bot joins and captures per-speaker audio |
| gmeet | 7 | DOM selectors current |
| msteams | 1 | Bot joins with passcode and captures mixed audio |
| zoom | 1 | Bot joins and captures per-speaker audio |
| zoom | 5 | Automated TTS collection |

## Docs this test owns

This test validates the API examples in the How sections of:
- [features/realtime-transcription/README.md](../features/realtime-transcription/README.md) — Send bot, subscribe WS, get transcript
- [features/realtime-transcription/gmeet/README.md](../features/realtime-transcription/gmeet/README.md) — GMeet host + auto-admit commands
- [features/realtime-transcription/msteams/README.md](../features/realtime-transcription/msteams/README.md) — Teams bot with passcode

If a curl command in those docs doesn't work, this test fixes it and logs FIX.

## Inputs

| Name | From | Description |
|------|------|-------------|
| GATEWAY_URL | W1 | API gateway |
| API_TOKEN | W1 | Valid token |
| PLATFORM | param | gmeet or teams |
| GROUND_TRUTH | param | JSON: [{speaker, text, delay_ms}] |

## Steps

```
1  if PLATFORM=gmeet:
     host meeting: CDP_URL=$CDP node scripts/gmeet-host-auto.js
     → MEETING_URL, NATIVE_MEETING_ID
     start auto-admit: CDP_URL=$CDP node scripts/auto-admit.js $MEETING_URL
   if PLATFORM=teams:
     human provides MEETING_URL + passcode

2  send recorder bot:
     POST /bots {meeting_url, bot_name: "Recorder", transcribe_enabled: true}
     wait for status=active

3  send TTS speaker bots (one per speaker in GROUND_TRUTH):
     for speaker in GROUND_TRUTH.speakers:
       POST /bots {meeting_url, bot_name: speaker.name, tts_text: speaker.text}
     wait for all active

4  wait for all utterances (sum of delays + buffer)

5  measure latency: time from TTS send to first segment confirmed

6  capture raw data:
     mkdir -p tests/testdata/$DATASET/
     save ground-truth.json
     GET /meetings/$ID/transcripts → pipeline/rest-segments.json
     query postgres → pipeline/db-segments.csv
     bot logs → pipeline/bot-segments.json

7  stop all bots:
     DELETE /bots/$RECORDER_ID
     DELETE /bots/$SPEAKER_IDS

8  score:
     python3 tests/rt-score.py tests/testdata/$DATASET/
     → DATASET_PATH, GROUND_TRUTH_COUNT, RAW_AUDIO_COUNT
```

## Outputs

| Name | Description |
|------|-------------|
| DATASET_PATH | Path to tests/testdata/{dataset}/ |
| GROUND_TRUTH_COUNT | Number of utterances in ground truth |
| RAW_AUDIO_COUNT | Number of audio files captured |

## Failure modes

| Symptom | Cause | Fix | Learned |
|---------|-------|-----|---------|
