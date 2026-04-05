---
id: test/verify-transcription
type: validation
requires: [test/admit-bot]
produces: [TRANSCRIPT_SEGMENTS]
validates: [realtime-transcription, speaking-bot, meeting-chat]
docs: [features/realtime-transcription/README.md, features/speaking-bot/README.md, features/meeting-chat/README.md, services/meeting-api/README.md, services/vexa-bot/README.md, services/tts-service/README.md, services/transcription-service/README.md]
mode: machine
skill: /test-verify-transcription
---

# Verify Transcription

> Follows [RULES.md](RULES.md). This procedure owns its scripts — fix them when they don't match reality.

After bot is admitted and active in a meeting, verify that audio is captured and transcription is returned via the API.

## Inputs

| Name | From | Default | Description |
|------|------|---------|-------------|
| GATEWAY_URL | test/infra-up | — | API gateway URL |
| LISTENER_TOKEN | test/api-full | — | API token for the listener/test user (`test@vexa.ai`) |
| SPEAKER_TOKEN | secrets/staging.env | — | API token for the speaker/TTS user (`tts@vexa.ai`) |
| MEETING_PLATFORM | test/create-live-meeting | google_meet | Platform |
| NATIVE_MEETING_ID | test/create-live-meeting | — | Platform meeting ID |

## Why two bots?

A bot **cannot hear itself**. The `/speak` API plays TTS through the bot's microphone into the meeting, but that same bot's audio capture doesn't pick up its own output. So:

- **Speaker bot** (`tts@vexa.ai`) — joins meeting, calls `/speak` to generate audio
- **Listener bot** (`test@vexa.ai`) — already in the meeting, transcribes what it hears

Different users because one user can only have one bot per meeting.

## Script

```bash
eval $(./scripts/09-verify-transcription.sh GATEWAY_URL LISTENER_TOKEN SPEAKER_TOKEN MEETING_PLATFORM NATIVE_MEETING_ID)
```

## Steps

Two-phase test to isolate breakpoints:

### Phase 1: TTS → listener (proven path)

1. Launch speaker bot from `tts@vexa.ai`, admit it
2. `POST /speak` with `{"text": "Hello, this is a test..."}` — TTS via Piper (local, no API key)
3. Poll listener's `/transcripts` for segments
4. If segments appear → full e2e pipeline works

### Phase 1b: Ground truth evaluation (analytical — no script)

The TTS input text is known ground truth. After segments appear, the agent MUST
evaluate each segment line-by-line against the exact text sent via `/speak`:

For each segment:
1. **Match to ground truth** — which `/speak` call produced this segment?
2. **Word-level diff** — compare transcribed text to sent text, word by word
3. **Classify errors**:
   - Substitution: wrong word ("Free" instead of "Three")
   - Insertion: extra words not in ground truth
   - Deletion: missing words from ground truth
   - Punctuation-only: acceptable variation ("Hello everyone" vs "Hello, everyone")
4. **Speaker attribution** — does the speaker name match the bot that spoke?
5. **Duplicates** — is this segment's content already covered by another segment?

Report as a table:

```
| Speaker | Sent (ground truth) | Transcribed | Errors | Speaker correct? |
|---------|---------------------|-------------|--------|-----------------|
```

> assert: WER (word error rate) < 15% per segment
> assert: 0 content duplicates within realtime segments
> assert: speaker attribution 100% correct
> on-fail: log each error with exact word positions. Do not round up to PASS.

This phase is analytical — the agent reads and compares, no script needed.
Ground truth is the exact text from the `/speak` calls earlier in the procedure.

### Phase 2: Audio file playback → listener

1. `POST /speak` with `audio_base64` (pre-rendered `testdata/test-speech-en.wav`)
2. Poll listener's `/transcripts` for NEW segments
3. If segments appear → both TTS and audio playback paths work

**Note:** Speaker bot needs admission by host browser (second admit cycle after listener).

> assert: at least 1 transcript segment with non-empty text
> on-fail: check transcription service connection, Redis streams, meeting-api logs

### Phase 3: Meeting chat read/write

1. `POST /bots/{platform}/{native_id}/chat` with `{"text": "hello from test"}` — bot sends chat message
2. `GET /bots/{platform}/{native_id}/chat` — read chat messages back
3. Assert: sent message appears in response
4. Assert: message has sender name and timestamp

> assert: POST returns 202, GET returns ≥1 message matching sent text
> on-fail: check if chat endpoints exist, check bot platform supports chat

## Outputs

| Name | Description |
|------|-------------|
| TRANSCRIPT_SEGMENTS | Number of segments retrieved |
| CHAT_OK | true if chat read/write works |

## Failure modes

| Symptom | Cause | Fix | Learned |
|---------|-------|-----|---------|
| 0 segments, bot is active | Audio not reaching transcription service | Check bot logs for audio capture, verify TRANSCRIBER_URL | |
| 0 segments, Web Speech API TTS used | Headless Chrome has no audio loopback — speechSynthesis doesn't route to Meet | Use PulseAudio virtual sink, or play audio file via `<audio>` element, or human speaks | Web Speech API is visual-only in headless Chrome |
| Segments exist but text is empty | Transcription service returned empty text (silence?) | Speak louder/clearer, check model size (tiny may miss speech) | |
| 401 on /transcripts | Token doesn't have `tx` scope | Recreate token with `bot,browser,tx` scopes | |
| Segments have wrong speaker | Speaker attribution failed | Known limitation — check SPLM pipeline | |
| Need multiple bots in same meeting | One user can only have one bot per meeting | Create separate user (tts@vexa.ai) for TTS bot | Multi-user tokens stored in secrets/staging.env |
| No audio in meeting (headless) | Tried Web Speech API / Chrome flags — wrong approach | Use `/speak` API: `POST /bots/{platform}/{id}/speak {"text":"..."}` | The Vexa API has TTS built in — don't hack the browser |
| /speak returns error | Bot not voice_agent_enabled, or bot not active | Create bot with `voice_agent_enabled: true` | /speak works on any active bot with voice agent |
| TTS fails: getaddrinfo tts-service | tts-service container not running on staging network | `docker run -d --name tts-service --network vexa-staging_staging vexaai/tts-service:260402-1711` | TTS uses Piper (local), no OpenAI key needed |
| audio_base64 playback completes but listener hears nothing | `playFile()` in tts-playback.ts missing `unmuteTtsAudio()` — PulseAudio stays muted | Add `unmuteTtsAudio()` before playback and `muteTtsAudio()` on exit in `playFile()` | `playPCM()` (TTS streaming) had mute/unmute, `playFile()` (audio_base64) didn't — fixed in 0.10.0-260404-2316 |
| Default provider "openai" | Stale default in bot index.ts and schemas.py | Changed to "piper" (local TTS, no API key) | TTS service uses Piper, not OpenAI |
| Word error in transcription | Realtime Whisper misrecognizes words (e.g. "Three" → "Free") | Log as WER finding; deferred (batch Whisper) often gets these right | 2026-04-05: realtime WER 1 word in 3 utterances. Known limitation of streaming vs batch. |
| Test passes despite quality issues | Script only checks segment count > 0, not text accuracy | Added Phase 1b: ground truth line-by-line evaluation (analytical, no script) | 2026-04-05: "Free speakers" passed because count > 0. Quality must be evaluated per-segment. |

## Docs ownership

After this test runs, verify and update:

- **features/realtime-transcription/README.md**
  - DoD table: this test provides evidence for items #1 (Google Meet confidence) and #2 (MS Teams confidence) depending on which platform was used — update Status, Evidence, Last checked
  - Platform architectures table: verify the audio capture method (Google Meet N separate `<audio>` elements vs Teams 1 mixed stream) matches actual bot behavior — check bot logs for how many audio streams were created
  - Config table (submitInterval, confirmThreshold, minAudioDuration, etc.): verify these hardcoded values match what the bot actually uses — if segment timing or confirmation behavior differs, update the table
  - Components table: verify `speaker-streams.ts`, `transcription-client.ts`, `segment-publisher.ts` paths still exist and are the actual code path exercised
  - How section: verify the documented POST `/bots` request/response format matches what this test used, and verify GET `/transcripts/{platform}/{native_meeting_id}` response shape (segment_id, speaker, text, start, end, language, completed) matches actual segments retrieved

- **features/speaking-bot/README.md**
  - DoD table: update Status, Evidence, Last checked for items #2 (other participants hear speech — verified because listener bot transcribes what speaker bot says), #3 (multiple voices distinguishable — if test used different voices), #5 (works on GMeet and Teams)
  - Components table: verify `services/meeting-api/meeting_api/voice_agent.py` (speak endpoint), `services/vexa-bot/core/src/services/tts-playback.ts` (TTS playback), `services/tts-service/` (Piper), and `services/vexa-bot/core/entrypoint.sh` (PulseAudio setup) paths are correct
  - Architecture: verify the documented flow `POST /speak -> Redis PUBLISH -> bot -> TTS service (Piper) -> WAV -> PulseAudio tts_sink -> virtual_mic -> meeting audio` matches actual behavior — check bot logs for TTS synthesis and PulseAudio mute/unmute sequence

- **features/meeting-chat/README.md**
  - DoD table: update Status, Evidence, Last checked for items #1 (POST /chat sends message), #2 (GET /chat returns messages), #3 (works on GMeet and Teams)
  - Architecture: verify `POST /bots/{platform}/{id}/chat {text} -> Redis PUBLISH -> bot types in meeting chat` matches actual behavior observed — check that the chat message appeared in the meeting
  - Components table: verify `services/meeting-api/meeting_api/voice_agent.py` (chat endpoints) and `services/vexa-bot/core/src/browser-session.ts` (chat handler) paths are correct

- **services/meeting-api/README.md**
  - Voice Agent endpoints: verify POST `/bots/{platform}/{meeting_id}/speak` accepts `{text, voice}` and returns 202, and POST/GET `/bots/{platform}/{meeting_id}/chat` work as documented
  - Verify the speak endpoint correctly publishes to Redis and the bot receives the command

- **services/vexa-bot/README.md**
  - Key modules table: verify `audio.ts` (stream discovery), `speaker-identity.ts` (DOM lookup), `speaker-streams.ts` (confirmation buffer), `transcription-client.ts` (HTTP POST), `segment-publisher.ts` (Redis output) paths match actual code exercised during transcription
  - Redis Output section: verify the XADD payload format (`type`, `token`, `uid`, `segments` with `start`, `end`, `text`, `speaker`, `completed`, `absolute_start_time`) matches actual Redis entries produced during the test
  - Bot Capabilities table: verify `voiceAgentEnabled` and `transcribeEnabled` flags match the configuration used for speaker and listener bots

- **services/tts-service/README.md**
  - Endpoints table: verify POST `/v1/audio/speech` accepts `{model, input, voice, response_format}` and returns audio as documented
  - Voice mapping table: verify the OpenAI-to-Piper voice mappings (alloy->amy, echo->danny, etc.) match actual TTS service behavior — check which voice the test used and what audio was produced
  - Dependencies: verify Piper TTS is bundled and no external API calls are made (check tts-service logs for outbound requests)

- **services/transcription-service/README.md**
  - Response format: verify segments returned have `text`, `start`, `end` fields matching the documented shape — the listener bot's transcription pipeline uses this service
  - Word-level timestamps: if the bot requests `timestamp_granularities=word`, verify the `words` array is present in the response as documented
