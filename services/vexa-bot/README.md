# Vexa Bot

## Why

The bot joins meetings and captures per-speaker audio. Unlike traditional
approaches that mix all participants into one stream and then guess who said
what, the bot keeps each speaker's WebRTC audio track separate. This gives:

- **Perfect speaker attribution** -- no diarization needed, segments arrive
  pre-labeled with the speaker's name.
- **Better transcription quality** -- clean single-voice audio instead of a
  mix with cross-talk.
- **Natural speaker events** -- derived from track activity (VAD on real
  audio), not DOM polling for CSS class changes.

## What

- Joins Google Meet, Teams, and Zoom via browser automation (Playwright).
- Per-speaker audio capture: each participant = separate audio stream.
- Silero VAD filters silence before transcription (saves compute).
- Direct HTTP POST to transcription-service (no WhisperLive dependency).
- Confirmation-based buffer: resubmits full buffer on each cycle, only
  publishes a segment when the text stabilizes across consecutive responses.
- Hallucination filter: known junk phrases + repetition detection.
- Redis output: XADD to streams (persistence) + PUBLISH to channels
  (real-time dashboard).
- Speaker identity: one-time DOM name resolution per participant, cached.
- Recording: audio file capture + upload to storage via bot-manager.

## How

### Architecture

```
Browser WebRTC: Track A (Alice), Track B (Bob), Track C (Carol)
  -> Per-speaker ScriptProcessor (browser)
  -> VAD filter (Node.js, Silero)
  -> Speaker buffer (confirmation-based, 3s interval, 15s cap)
  -> Transcription-service (HTTP POST)
  -> Redis XADD + PUBLISH (with speaker label)
```

### Key modules

All under `core/src/services/`:

| Module                   | Role                                              |
|--------------------------|---------------------------------------------------|
| `audio.ts`               | Per-speaker stream discovery from DOM media elements |
| `speaker-identity.ts`    | DOM lookup: media element -> participant name      |
| `speaker-streams.ts`     | Confirmation-based buffer per speaker              |
| `transcription-client.ts`| HTTP POST to transcription-service                 |
| `segment-publisher.ts`   | Redis XADD (persistence) + PUBLISH (real-time)     |
| `vad.ts`                 | Silero VAD -- silence filtering per stream         |
| `recording.ts`           | Audio file recording and upload                    |
| `unified-callback.ts`    | HTTP status callbacks to bot-manager               |

### Run

The bot is normally launched by bot-manager, which passes a `BOT_CONFIG` JSON
environment variable. For standalone testing:

```bash
docker build -t vexa-bot .
docker run --rm --platform linux/amd64 --network vexa_dev_vexa_default \
  -e BOT_CONFIG='{"platform":"google_meet","meetingUrl":"https://meet.google.com/abc-defg-hij","botName":"Vexa","token":"jwt","connectionId":"id","nativeMeetingId":"abc","meeting_id":1,"redisUrl":"redis://redis:6379/0","automaticLeave":{"waitingRoomTimeout":300000,"noOneJoinedTimeout":300000,"everyoneLeftTimeout":300000}}' \
  -e TRANSCRIPTION_SERVICE_URL=http://transcription-service:8083/v1/audio/transcriptions \
  vexa-bot
```

Dev workflow (bind-mounts dist/ for fast iteration):

```bash
make build                   # one-time: Docker image + local dist/
make test MEETING_URL='https://meet.google.com/abc-defg-hij'
make rebuild                 # after editing TS (~10s, no image rebuild)
```

### Configure

| Variable                    | Description                                     |
|-----------------------------|-------------------------------------------------|
| `BOT_CONFIG`                | JSON with full bot config (platform, meetingUrl, botName, meeting_id, redisUrl, automaticLeave) |
| `TRANSCRIPTION_SERVICE_URL` | HTTP URL of transcription-service endpoint       |
| `REDIS_URL`                 | Redis connection URL                             |
| `ZOOM_CLIENT_ID`            | Zoom SDK client ID (Zoom only)                   |
| `ZOOM_CLIENT_SECRET`        | Zoom SDK client secret (Zoom only)               |

### Test

A mock meeting page simulates multiple participants without a real meeting:

```bash
# Start backend
docker compose up -d redis
cd services/transcription-service && docker compose up -d

# Serve mock meeting (3 speakers: Alice, Bob, Carol)
cd services/vexa-bot/tests/mock-meeting && bash serve.sh

# Run tests
node tests/test_mock_meeting.js          # unit-level
node tests/test_mock_meeting_e2e.js      # end-to-end: bot -> transcription -> Redis

# Verify Redis output
redis-cli XRANGE transcription_segments - +
# Segments should have speaker: "Alice Johnson", "Bob Smith", etc.
```

### Dev

```bash
# Hot-debug: attach to a running bot, edit, rebuild, restart
core/src/platforms/hot-debug.sh

# Makefile loop: edit TS -> make rebuild (~10s) -> make test -> check Redis
```

## Supported Platforms

| Platform         | Browser            | Audio Capture                                   |
|------------------|--------------------|------------------------------------------------|
| Google Meet      | Chrome + Stealth   | DOM `<audio>`/`<video>` elements                |
| Microsoft Teams  | MS Edge (required) | RTCPeerConnection hook intercepts WebRTC tracks |
| Zoom             | None (native SDK)  | SDK raw audio callback or PulseAudio fallback   |

All platforms feed into the same per-speaker pipeline: tracks discovered,
tagged with participant identity, filtered through VAD, buffered, transcribed,
and published to Redis.

## Redis Output

Two Redis calls per segment:

```
XADD  transcription_segments   -- collector persists to Postgres
PUBLISH meeting:{id}:segments  -- gateway forwards to dashboard WebSocket
```

Speaker lifecycle events (track-based: joined, started, stopped, left):

```
XADD  speaker_events
```

## Runtime Control

Redis subscriber on `bot_commands:meeting:<meeting_id>`:

```bash
redis-cli PUBLISH bot_commands:meeting:123 '{"action":"leave"}'
redis-cli PUBLISH bot_commands:meeting:123 '{"action":"reconfigure","language":"es"}'
```

Status callbacks via HTTP POST to bot-manager: `joining`, `awaiting_admission`,
`active`, `completed`, `failed`.

## Project Structure

```
vexa-bot/
  Dockerfile          -- production build
  Makefile            -- hot dev kit (build, rebuild, test)

  core/src/
    index.ts          -- runBot() orchestrator
    docker.ts         -- container entry point
    platforms/
      shared/meetingFlow.ts   -- strategy-pattern flow controller
      googlemeet/             -- Google Meet strategies
      msteams/                -- Microsoft Teams strategies
      zoom/                   -- Zoom SDK + native addon
    services/
      audio.ts                -- per-speaker stream discovery
      speaker-identity.ts     -- DOM -> participant name
      speaker-streams.ts      -- confirmation buffer per speaker
      transcription-client.ts -- HTTP to transcription-service
      segment-publisher.ts    -- Redis XADD + PUBLISH
      vad.ts                  -- Silero VAD
      recording.ts            -- audio file capture + upload
      unified-callback.ts     -- HTTP callbacks to bot-manager

  tests/
    mock-meeting/             -- local multi-speaker test page
    test_mock_meeting.js      -- unit-level mock test
    test_mock_meeting_e2e.js  -- end-to-end mock test
```

## Zoom SDK

Zoom Meeting SDK binaries are proprietary and not included. Download from Zoom
and place under `core/src/platforms/zoom/native/zoom_meeting_sdk/`.

```bash
ls core/src/platforms/zoom/native/zoom_meeting_sdk/libmeetingsdk.so
```
