# Realtime Transcription

## Why

Core feature. Bot joins a meeting, captures audio, transcribes with Whisper in real-time, delivers speaker-labeled segments via WebSocket and REST. Self-hosted alternative to Otter.ai/Fireflies/Read.ai at infrastructure cost.

## What

Both platforms feed into the same core: `SpeakerStreamManager` (`services/vexa-bot/core/src/services/speaker-streams.ts`).

```
Audio in → buffer (min 3s) → submit every 2s → Whisper (faster-whisper) → per-segment stability check
  → confirmed segments → Redis XADD + PUBLISH → collector persists to Postgres
  → api-gateway: WebSocket (live) + REST (historical)
```

### Platform architectures

| Platform | Audio | Speaker identity | Pipelines |
|----------|-------|-----------------|-----------|
| **Google Meet** | N separate `<audio>` elements, one per participant | DOM mutation voting + locking (2 votes, 70%) | N independent |
| **MS Teams** | 1 mixed stream, all participants | Live captions `[data-tid="author"]` timestamps | 1 shared |
| **Zoom** | Not implemented | — | — |

### Components

| Component | File | Role |
|-----------|------|------|
| speaker-streams | `services/vexa-bot/core/src/services/speaker-streams.ts` | Buffer, submit, confirm, emit |
| transcription-client | `services/vexa-bot/core/src/services/transcription-client.ts` | HTTP POST WAV to transcription-service |
| transcription-service | `services/transcription-service/main.py` | faster-whisper inference, word timestamps |
| segment-publisher | `services/vexa-bot/core/src/services/segment-publisher.ts` | Redis XADD + PUBLISH |
| transcription-collector | `services/meeting-api/` | Redis stream → Postgres (persistence only) |
| speaker-identity | `services/vexa-bot/core/src/services/speaker-identity.ts` | GMeet: DOM voting/locking |
| speaker-mapper | `services/vexa-bot/core/src/services/speaker-mapper.ts` | Teams: word timestamps × caption boundaries |

### Config (hardcoded in index.ts ~L1037)

| Param | Value | Why |
|-------|-------|-----|
| submitInterval | 2s | Latency vs Whisper efficiency |
| confirmThreshold | 2 | 2 consecutive matches per segment position |
| minAudioDuration | 3s | Don't submit tiny chunks |
| maxBufferDuration | 120s | Trim buffer front at 2 min |
| idleTimeoutSec | 15s | Browser silence filter makes pauses look idle |

### Platform docs

- [Google Meet](gmeet/) — multi-channel, voting/locking
- [MS Teams](msteams/) — single-channel, caption-driven
- [Zoom](zoom/) — research only, requires app approval

## How

### 1. Send a bot to a meeting

```bash
curl -X POST $GATEWAY/bots \
  -H "X-API-Key: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "meeting_url": "https://meet.google.com/abc-defg-hij",
    "bot_name": "Vexa Notetaker"
  }'
```

Response:
```json
{
  "id": "bot_abc123",
  "status": "requested",
  "meeting_url": "https://meet.google.com/abc-defg-hij",
  "platform": "google_meet",
  "native_meeting_id": "abc-defg-hij"
}
```

For Teams, add `passcode` (required for anonymous join):
```bash
curl -X POST $GATEWAY/bots \
  -H "X-API-Key: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "meeting_url": "https://teams.live.com/meet/9876543210",
    "bot_name": "Vexa Notetaker",
    "passcode": "abc123"
  }'
```

### 2. Check bot status

```bash
curl $GATEWAY/bots/status -H "X-API-Key: $TOKEN"
```

Response:
```json
{
  "bots": [
    {
      "id": "bot_abc123",
      "status": "active",
      "meeting_url": "https://meet.google.com/abc-defg-hij",
      "platform": "google_meet"
    }
  ]
}
```

Status transitions: `requested → joining → awaiting_admission → active → stopping → completed`

### 3. Subscribe to live transcription (WebSocket)

```bash
wscat -c "ws://$GATEWAY/ws?api_key=$TOKEN"
```

Subscribe to a meeting:
```json
{"action": "subscribe", "meetings": [{"meeting_id": "abc-defg-hij"}]}
```

Segments arrive as:
```json
{
  "segment_id": "sess123:speaker-0:1",
  "speaker": "Alice",
  "text": "The quarterly revenue exceeded expectations by fifteen percent",
  "start": 12.3,
  "end": 18.7,
  "language": "en",
  "completed": true,
  "absolute_start_time": "2026-04-05T14:30:12.300Z"
}
```

`completed: false` = draft (still being transcribed). `completed: true` = confirmed (final).

### 4. Get historical transcript (REST)

After the meeting (or during — returns all confirmed segments):
```bash
curl $GATEWAY/meetings/$MEETING_ID/transcripts \
  -H "X-API-Key: $TOKEN"
```

Response:
```json
{
  "segments": [
    {
      "segment_id": "sess123:speaker-0:1",
      "speaker": "Alice",
      "text": "The quarterly revenue exceeded expectations by fifteen percent",
      "start": 12.3,
      "end": 18.7,
      "language": "en"
    }
  ]
}
```

### 5. Stop the bot

```bash
curl -X DELETE $GATEWAY/bots/$BOT_ID \
  -H "X-API-Key: $TOKEN"
```

Bot transitions: `active → stopping → completed`. Recording uploads to storage. Transcript persists in Postgres.

### 6. Make the bot speak (optional)

```bash
curl -X POST $GATEWAY/bots/$BOT_ID/speak \
  -H "X-API-Key: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello everyone, I am taking notes for this meeting."}'
```

Uses Piper TTS → PulseAudio → meeting audio. Other participants hear the bot speak.

## DoD

Synthetic — computed from children. No own items.

| # | Check | Weight | Ceiling | Floor | Status | Evidence | Last checked | Test |
|---|-------|--------|---------|-------|--------|----------|--------------|------|
| 1 | Google Meet confidence ≥ 70 | 40 | ceiling | 0 | PASS | GMeet child confidence 75. 9 segments, 0% WER on best stream. 2 speakers tested (3-speaker test not completed — container crashes). Production single-bot scenario works. Bug #30 audio loopback affects multi-bot lite mode only. | 2026-04-05T21:50Z | gmeet/README.md |
| 2 | MS Teams confidence ≥ 70 | 40 | ceiling | 0 | PASS | Teams child confidence 70. 19 segments, 3 speakers correctly attributed, WER <5%. Whisper hallucination finding (bug #24). Partial duplicate from caption re-rendering (bug #25). | 2026-04-05T21:50Z | msteams/README.md |
| 3 | WS delivery matches REST | 10 | ceiling | 0 | PASS | 8/8 WS steps: connect, auth reject, ping/pong, subscribe (Teams 9340658055333), unsubscribe, invalid JSON recovery, unknown action. 19 REST segments, 0 duplicates, all have text+speaker. | 2026-04-05T21:50Z | 12-websocket |
| 4 | Zoom confidence ≥ 50 | 10 | — | 0 | SKIP | Not implemented — requires Zoom app approval | 2026-04-05T21:50Z | zoom/README.md |

Confidence: 70 (items 1+2 pass — both children ≥70 (GMeet 75, Teams 70); item 3 pass; Zoom not implemented. Deductions for Whisper hallucination bugs and 3-speaker coverage gap.)

## Known Issues

### Duplicate segments when deferred transcription also runs

If `POST /meetings/{id}/transcribe` is called after a meeting that had realtime transcription, the `transcriptions` table ends up with both realtime and deferred rows for the same utterances. `GET /transcripts` returns all of them, causing the dashboard to show every line twice.

**Fix applied:** `POST /meetings/{id}/transcribe` returns 409 if segments already exist: "This meeting is already transcribed". See `features/post-meeting-transcription/README.md` for details.

### Realtime WER on specific words

Streaming Whisper occasionally misrecognizes words that batch Whisper gets right (e.g., "Three" → "Free"). This is a known limitation of streaming vs full-file context. Not a bug — documented as expected accuracy difference.

### Whisper hallucination on silence (bug #24)

When audio contains silence or very low-level noise, Whisper can hallucinate content — producing text that was never spoken. Observed: phantom "fema.gov" segment during a silence period. This is a known Whisper behavior, not a Vexa bug. Mitigation: hallucination filter in `core/src/services/hallucinations/` catches known junk phrases. New hallucination patterns should be added to the filter list.

### Partial duplicate from Teams caption re-rendering (bug #25)

Teams occasionally re-renders the same caption text, causing the caption observer to flush the audio buffer twice for the same utterance. This produces near-duplicate segments with slightly different timestamps. The second segment typically contains a subset of the first segment's text.

**Root cause:** Teams caption DOM updates are not atomic — the `[data-tid="closed-caption-text"]` element can fire multiple mutation events for the same caption line, especially during speaker transitions.

### GMeet audio loopback duplicates (bug #30)

In multi-bot scenarios, Google Meet's per-speaker audio elements can capture TTS output from other bots, creating duplicate segments with wrong speaker attribution. See `features/realtime-transcription/gmeet/README.md` for details.
