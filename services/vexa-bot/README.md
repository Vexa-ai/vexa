# Vexa Bot

## Why

The bot joins meetings across platforms, captures audio, and produces
speaker-attributed transcription segments in real-time. Each platform
provides different signals, and the bot adapts:

- **Google Meet** -- separate audio stream per participant. Clean single-voice
  audio, speaker identity via DOM voting/locking.
- **Microsoft Teams** -- single mixed audio stream. Speaker attribution via
  live captions (Teams ASR provides speaker name with each caption event).
- **Zoom** -- SDK raw audio callbacks with participant metadata.

All platforms feed into the same shared transcription pipeline.

## What

### Documentation
- [Bot Overview](../../docs/bot-overview.mdx)
- [Meeting IDs](../../docs/meeting-ids.mdx)

- Joins Google Meet and Teams via browser automation (Playwright). Zoom uses native SDK (not browser).
- Per-speaker audio capture: each participant = separate audio stream.
- Screen share tracks (unmapped to a participant tile) labeled "Presentation".
- Silero VAD filters silence before transcription (saves compute).
- Direct HTTP POST to [transcription-service](../../services/transcription-service/README.md).
- Per-speaker language detection: auto-detect on first chunk, lock language
  after high-confidence detection so subsequent chunks skip detection overhead.
- Confirmation-based buffer: resubmits full buffer every 2s, publishes
  drafts immediately (`completed: false`) for ~2s dashboard latency.
  Confirmed segments (`completed: true`) replace drafts after 2 consecutive
  matching transcriptions. Wall-clock hard cap at 10s forces flush.
- Hallucination filter: known junk phrases + repetition detection.
  Phrase lists at `core/src/services/hallucinations/`.
- Redis output: XADD to streams (persistence) + PUBLISH to channels
  (real-time dashboard).
- Speaker identity: one-time DOM name resolution per participant, cached.
  Google Meet uses participant tile DOM selectors. Teams uses
  RTCPeerConnection track metadata.
- Recording: audio file capture + upload to storage via [meeting-api](../meeting-api/README.md).

### Known limitations

| Area | Status | Detail |
|------|--------|--------|
| **Certainty** | HIGH | Per-speaker pipeline well documented |
| **Google Meet failures** | Known | 7.8% failure rate — join-stage "No active media elements" error. |
| **Teams regressions** | Active | Current prod works (0% fail), but new code has open issues (#171, #189, #190, #191). |
| **Zoom production** | Self-hosted only | Requires your own Zoom Marketplace app + SDK. Not available on hosted service. Web app mode (Playwright, no SDK) in development. |
| **Authenticated bot support** | Google Meet ✅, Teams 🔴 | Google Meet: bots join as authenticated Google user via stored browser credentials (`authenticated: true` in BOT_CONFIG). Uses persistent browser context with userdata synced from/to S3. Teams: blocked — consumer Microsoft accounts (Gmail-linked) get locked by anti-automation detection. Requires Microsoft 365 Business Basic tenant (~$6/mo) with MFA disabled. S3 sync infrastructure already covers Teams sessions; gap is account provisioning only. See `conductor/missions/research-msteams-auth.md`. Zoom: not started. |

## How

### Architecture

```
Browser WebRTC: Track A (Alice), Track B (Bob), Track C (Carol)
  -> Per-speaker ScriptProcessor (browser)
  -> VAD filter (Node.js, Silero)
  -> Speaker buffer (confirmation-based, 2s interval, 10s wall-clock cap)
  -> Transcription-service (HTTP POST)
  -> Redis XADD {payload} with JWT + absolute UTC timestamps
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
| `unified-callback.ts`    | HTTP status callbacks to meeting-api               |

### Run

The bot is normally launched by meeting-api (via Runtime API), which passes a `BOT_CONFIG` JSON
environment variable. For standalone testing:

```bash
# Build with immutable timestamp tag (convention: YYMMDD-HHMM)
docker build -t vexaai/vexa-bot:$(date +%y%m%d-%H%M) .

# Or use make build from deploy/compose/ which tags all services consistently
docker run --rm --platform linux/amd64 --network vexa_dev_vexa_default \
  -e BOT_CONFIG='{"platform":"google_meet","meetingUrl":"https://meet.google.com/abc-defg-hij","botName":"Vexa","token":"jwt","connectionId":"id","nativeMeetingId":"abc","meeting_id":1,"redisUrl":"redis://redis:6379/0","automaticLeave":{"waitingRoomTimeout":300000,"noOneJoinedTimeout":300000,"everyoneLeftTimeout":300000}}' \
  -e TRANSCRIPTION_SERVICE_URL=http://transcription-service:8083/v1/audio/transcriptions \
  vexaai/vexa-bot:dev  # For production, use immutable tags (e.g., 0.10.0-260405-0108)
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
| `BOT_CONFIG`                | JSON with full bot config (platform, meetingUrl, botName, meeting_id, redisUrl, automaticLeave, authenticated, userdataS3Path, s3Endpoint, s3Bucket, s3AccessKey, s3SecretKey) |
| `TRANSCRIPTION_SERVICE_URL` | HTTP URL of transcription-service endpoint       |
| `REDIS_URL`                 | [Redis](../redis.md) connection URL              |
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

# Pretty-print recent transcripts from Redis
bash tests/print_transcripts.sh
```

Unit tests for the confirmation buffer (no Docker needed):

```bash
cd core && npx tsx src/services/__tests__/speaker-streams.test.ts
```

### Dev

```bash
# Hot-debug: attach to a running bot, edit, rebuild, restart
core/src/platforms/hot-debug.sh

# Makefile loop: edit TS -> make rebuild (~10s) -> make test -> check Redis
```

## Bot Capabilities

Four independent capability flags control what the bot does in a meeting. Audio is always on; video defaults to off to save resources.

```
                     Default
Audio IN  (capture)  always on    Per-speaker ScriptProcessors (Google Meet)
                                  or caption-driven mixed stream routing (Teams)
Audio OUT (speak)    off          TTS playback via virtual mic (voiceAgentEnabled)
Video IN  (see)      off          Receive participant video (videoReceiveEnabled)
Video OUT (show)     off          Stream avatar via virtual camera (cameraEnabled)
```

| Flag | Type field | API field | Default | What it controls |
|------|-----------|-----------|---------|-----------------|
| `voiceAgentEnabled` | `voice_agent_enabled` | `voice_agent_enabled` | `false` | TTS playback, microphone service, Redis event publishing. Does NOT require camera. |
| `cameraEnabled` | `camera_enabled` | `camera_enabled` | `false` | Virtual camera init script, avatar streaming, `ScreenContentService`. Independent of voice agent. |
| `videoReceiveEnabled` | `video_receive_enabled` | `video_receive_enabled` | `false` | When off, incoming video tracks are disabled at the WebRTC level (~87% CPU savings per bot). |
| `transcribeEnabled` | `transcribe_enabled` | `transcribe_enabled` | `true` | Per-speaker transcription pipeline (SpeakerStreamManager, TranscriptionClient, SegmentPublisher). |

Typical configurations:

| Use case | `voiceAgentEnabled` | `cameraEnabled` | `videoReceiveEnabled` |
|----------|--------------------|-----------------|-----------------------|
| **Recorder bot** (transcription only) | off | off | off |
| **TTS speaker bot** (collection runs) | on | off | off |
| **Full voice agent** (avatar + TTS + screen reading) | on | on | on |
| **Authenticated recorder** (no lobby wait) | off | off | off |

Authenticated bots additionally set `authenticated: true` + S3 credentials in BOT_CONFIG. The bot downloads stored browser userdata from S3, launches a persistent Chromium context (no incognito), and joins as the signed-in user — skipping name entry and lobby wait. Browser data is synced back to S3 on exit to keep credentials fresh.

**Platform support:** Google Meet authenticated join is implemented and validated. MS Teams authenticated join requires a Microsoft 365 Business Basic account (consumer accounts get locked). The S3 sync and persistent context infrastructure is shared — Teams needs the same cookies/localStorage files that `s3-sync.ts` already handles.

## Supported Platforms

| Platform | Browser | Audio Capture | Speaker Identity |
|----------|---------|--------------|-----------------|
| Google Meet | Chrome + Stealth | N per-element `<audio>`/`<video>` streams (one per participant) | DOM speaking-indicator correlation + voting/locking (`speaker-identity.ts`): 3 votes at 70% ratio locks permanently |
| Microsoft Teams | MS Edge (fallback: Chrome) | 1 mixed RTCPeerConnection stream, routed by live caption speaker boundaries | Caption author `[data-tid="author"]` — name known at routing time, no voting needed |
| Zoom (self-hosted) | None (native SDK) | SDK raw audio callback or PulseAudio fallback | SDK participant metadata. Requires own Marketplace app. |

All platforms feed into the same shared pipeline: `SpeakerStreamManager` (buffering, Whisper submission, confirmation) -> `TranscriptionClient` (HTTP POST WAV) -> `SegmentPublisher` (Redis XADD + PUBLISH). Platform-specific code only handles how audio enters the manager and how speaker names are resolved.

## Redis Output

Per segment — XADD with `{payload: JSON}` wrapping JWT token, session UID, and segments array:

```
XADD transcription_segments * payload '{"type":"transcription","token":"<JWT>","uid":"<session>","segments":[{"start":19.0,"end":34.0,"text":"...","speaker":"Alice","completed":false,"absolute_start_time":"2026-03-15T08:10:01.194Z","absolute_end_time":"2026-03-15T08:10:16.193Z"}]}'
```

- `completed: false` — draft (published immediately on each transcription result, ~2s latency)
- `completed: true` — confirmed (after fuzzy match stabilization, replaces draft)
- `absolute_start_time/end_time` — bot publishes absolute UTC directly (no collector reconstruction needed)
- `start/end` — relative seconds, kept for backward compatibility (Redis hash key)

Both go through collector → Redis hash → `PUBLISH tc:meeting:{id}:mutable` → gateway WebSocket → dashboard.

Speaker lifecycle events (track-based: joined, started, stopped, left):

```
XADD speaker_events_relative * uid <session> relative_client_timestamp_ms 5000 event_type SPEAKER_START participant_name "Alice"
```

## Runtime Control

Redis subscriber on `bot_commands:meeting:<meeting_id>`:

```bash
redis-cli PUBLISH bot_commands:meeting:123 '{"action":"leave"}'
redis-cli PUBLISH bot_commands:meeting:123 '{"action":"reconfigure","language":"es"}'
```

Status callbacks via HTTP POST to [meeting-api](../meeting-api/README.md): `joining`, `awaiting_admission`,
`active`, `completed`, `failed`.

### Bot-Enforced Timeouts

The bot enforces two timeouts internally via `automaticLeave` in BOT_CONFIG:

| BOT_CONFIG field | API name | Default | What happens |
|-----------------|----------|---------|-------------|
| `automaticLeave.waitingRoomTimeout` | `max_wait_for_admission` | 900000 (15 min) | Bot sends `completed` callback with reason `awaiting_admission_timeout` |
| `automaticLeave.everyoneLeftTimeout` | `max_time_left_alone` | 900000 (15 min) | Bot sends `completed` callback with reason `left_alone` |
| `automaticLeave.noOneJoinedTimeout` | `no_one_joined_timeout` | 120000 (2 min) | Bot sends `completed` callback |

**Note:** `max_bot_time` (absolute max lifetime) is NOT enforced by the bot. It's enforced server-side by the scheduler, which fires `DELETE /bots` regardless of bot state. This is defense in depth — even if the bot hangs, the scheduler kills it. See `features/bot-lifecycle/README.md`.

**Finding (2026-04-05):** The default `noOneJoinedTimeout` of 120s (2 min) is too short for human-in-the-loop test flows where the human needs to context-switch between terminal and meeting UI to admit bots. Use `no_one_joined_timeout: 300000` (5 min) in bot creation payloads for test scenarios. See `tests/07-bot-lifecycle.md` for details.

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
      unified-callback.ts     -- HTTP callbacks to meeting-api

  tests/
    mock-meeting/             -- local multi-speaker test page
    test_mock_meeting.js      -- unit-level mock test
    test_mock_meeting_e2e.js  -- end-to-end mock test
```

## VNC Browser View (All Modes)

Every bot container runs a VNC stack so the dashboard can show the bot's browser in real time. The entrypoint starts this for all modes (meeting and browser_session):

- **Xvfb**: Virtual display on `:99` (1920x1080x24) — Playwright renders here
- **fluxbox**: Window manager that maximizes all windows to fill the display
- **x11vnc**: VNC server on port 5900, connected to display :99
- **websockify**: Bridges VNC to WebSocket on port 6080, serves noVNC web client

The dashboard accesses VNC via the gateway: `/b/{meeting_id}/vnc/websockify`. The gateway resolves the meeting ID to a container name via Redis.

## Browser Session Mode

Activated when `BOT_CONFIG` contains `mode: "browser_session"`. Instead of joining a meeting, the bot runs a persistent Chromium instance. Same VNC stack as meeting mode, plus:

- **CDP**: Chrome DevTools Protocol on port 9222 (socat exposes it on 0.0.0.0:9223 for Docker network access)
- **SSH**: OpenSSH on port 22 (mapped to a random host port). Password is the `session_token` from BOT_CONFIG.
- **Persistent browser profile**: Chromium user data stored at `/tmp/browser-data`, synced to/from MinIO (`users/{id}/browser-userdata/browser-data`) on startup and save.
- **Workspace**: `/workspace` directory synced via git (if `workspaceGitRepo` is configured in BOT_CONFIG) or via MinIO. Git workspace auto-commits and pushes on save.
- **Save triggers**: Redis pub/sub on channel `browser_session:{container_name}` -- `save_storage` message triggers sync, `stop` message saves and exits.
- **Graceful shutdown**: SIGTERM/SIGINT saves all data before exit.

Entry point: `core/src/browser-session.ts` (imported dynamically from `docker.ts` when mode is `browser_session`).

## Zoom SDK

Zoom Meeting SDK binaries are proprietary and not included. Download from Zoom
and place under `core/src/platforms/zoom/native/zoom_meeting_sdk/`.

```bash
ls core/src/platforms/zoom/native/zoom_meeting_sdk/libmeetingsdk.so
```

## Public Docs

- [Google Meet](https://docs.vexa.ai/platforms/google-meet)
- [Microsoft Teams](https://docs.vexa.ai/platforms/microsoft-teams)
- [Zoom](https://docs.vexa.ai/platforms/zoom)
