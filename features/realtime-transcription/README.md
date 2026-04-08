---
services: [meeting-api, vexa-bot, tts-service]
tests3:
  targets: [meeting-tts, contracts, smoke]
  checks: [GMEET_URL_PARSED, TRANSCRIPTION_UP, WS_PING_PONG]
---

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
| 1 | Google Meet confidence ≥ 70 | 40 | ceiling | 0 | FAIL | GMeet on lite: bots joined, TTS 4/4 sent, human heard audio. 0 transcript segments returned. **Root cause**: lite shared PulseAudio — speaker bot TTS plays through the same audio sink the recorder bot captures from, but audio routing between processes doesn't work (loopback). Needs: separate PulseAudio sinks per bot, or test on compose/helm where bots are separate containers. | 2026-04-08 | Phase 5b |
| 2 | MS Teams confidence ≥ 70 | 40 | ceiling | 0 | PASS | Teams on lite: single bot + human speaker. Segments stored in Redis (`Stored/Updated 1 segments`). Latency ~10-20s. Teams on helm: 4 segments seen inline during test, but 0 persisted after stopping — K8s stopping hang blocks flush (see F2, F6). **Needs**: latency measurement make target, K8s stopping fix. | 2026-04-08 | Phase 5a, lite retest |
| 3 | WS delivery matches REST | 10 | ceiling | 0 | FAIL | REST returns 0 segments for both platforms after meetings end. Segments may exist in Redis during meeting (script saw 4 inline) but not persisted to Postgres. **Needs**: test target that queries both WS and REST during active meeting + after meeting ends, with timestamps for latency. | 2026-04-08 | Phase 5a, 5b |
| 4 | Zoom confidence ≥ 50 | 10 | — | 0 | SKIP | Not implemented | 2026-04-08 | — |
| 5 | Rapid speaker alternation: ≥75% attribution | 10 | — | 0 | FAIL | Cannot evaluate — 0 persisted segments. | 2026-04-08 | Phase 5a, 5b |
| 6 | Live WS transcript text is non-empty during active meeting | 10 | — | 0 | FAIL | Not verified — need WS subscription during active meeting with latency timestamps. **Needs**: make target that subscribes to WS, logs segments with receive timestamps, compares to TTS send timestamps. | 2026-04-08 | Phase 5a, 5b |
| 7 | Dashboard renders REST-loaded transcript on page load | 10 | — | 0 | FAIL | No persisted transcript data to render. Dashboard itself works (Phase 4 dashboard tests pass). | 2026-04-08 | Phase 5a, 5b |

Confidence: 30 (Teams PASS on lite, GMeet FAIL on lite loopback, K8s transcription broken — segments not persisted after bot stopping hang. Core pipeline works, K8s deployment issue to investigate.)

## Findings from 2026-04-08 full validation

### F1: Segments exist during meeting but lost after cleanup
The meeting-tts-teams script saw 4 segments inline during the test, but REST query returned 0 after bots stopped. Segments are likely in Redis during the meeting but never flushed to Postgres. The bot "stopping" state hang may prevent the flush.

### F2: Bot stopping state hang (Teams/helm)
After DELETE /bots, Teams bots get stuck in "stopping" and never reach "completed". This blocks: (a) segment flush to Postgres, (b) recording upload to MinIO, (c) webhook delivery, (d) concurrency slot release.

### F3: Latency not measured
Human reported high latency during Teams test. No timestamps captured. Need a test target that records: TTS send time, WS segment receive time, REST query time — to measure pipeline latency.

### F4: Lite PulseAudio loopback (multi-bot only)
In lite mode, all bots share one PulseAudio server. Speaker bot TTS output doesn't route to recorder bot audio input. Multi-bot transcription tests must run on compose or helm where bots are separate containers.

### F5: Transcription works on lite/Teams — segments arrive with latency
Bot 9841 on lite joined Teams meeting 343190844094660. Initially 0 segments — queried too early. After human spoke, segments started arriving: `Stored/Updated 1 segments in Redis from message ... for meeting 9841`. Transcription pipeline works on lite for Teams with a single bot + human speaker. **Latency is noticeable** — segments appear ~10-20s after speech.

### F6: K8s stopping hang is separate from transcription
Transcription pipeline itself works (confirmed on lite). The K8s issue is specifically the bot stopping state hang — bots don't reach "completed", which blocks segment flush to Postgres and makes REST queries return 0 after cleanup. Need to investigate the stopping flow on K8s separately.

### F7: All bots hit 90s Delayed Stop — not Redis-specific
Every bot stop triggers `[Delayed Stop] Waiting 90s for container`. This is by design in the code but blocks: recording upload, status transition to completed, dashboard showing completed. Meetings 9835 (GMeet), 9841 (Teams), 9842 (GMeet) all hit this. Meeting 9835 also had Redis connection refused during restart (compounding issue). GMeet 9842 had Redis up and still waited 90s. Recording upload and post-meeting tasks only run after the 90s delay.

### F8: MinIO upload retry blocks meeting-api — cascading failure
When recording upload fails (MinIO DNS), botocore retries 4 times with exponential backoff. During retries, the meeting-api async worker is blocked — ALL API requests return "Service unavailable". New bot creation, status queries, everything fails. This turns a config bug (wrong MinIO endpoint) into a full service outage. Found when transcription-replay failed to create recorder bot.

**Root cause**: `recordings.py:194` calls `storage.upload_file()` synchronously in the request handler. botocore retries are blocking. Needs: async upload with timeout, or move upload to background task.

### F9: TTS intermittently broken — all deployments (lite, compose, helm)
Same observations on all three deployments: POST /speak returns 202, TTS synthesizes OK (200), audio reaches bot — but playback intermittent. Not deployment-specific. Core bot code bugs: unhandled Promise rejection (bug 3), no concurrency guard (bug 4), auto-mute race (bug 5). See `features/speaking-bot/README.md` for full analysis and fixes.

### F10: Teams captions-based transcription is fragile and non-English-broken
Teams bot relies on enabling closed captions via CDP DOM click. Two problems:
1. **Activation fails silently** — DOM selectors change, captions dialog doesn't appear, bot continues without captions → `whisper=0 vad=0/0` → 0 segments. Found on meeting 9854 (compose). Human manually enabled captions → transcription immediately started.
2. **Non-English quality** — Teams captions only support limited languages, caption text quality is poor for non-English speech. This is a platform limitation.
**Alternative**: Switch Teams to audio capture + whisper (same as GMeet approach). Capture raw audio stream, send to transcription service. No dependency on platform captions. Requires: PulseAudio audio capture in Teams bot, not caption scraping.

### F10: Lite recording upload fails — MinIO DNS (bot reaches completed but recording lost)
Bot 9841 on lite: stopping → completed works. But recording upload fails with `Could not connect to endpoint URL: http://minio:9000` — DNS resolution failure. Lite runs with `--network host`, so Docker service name `minio` doesn't resolve. Config needs `MINIO_ENDPOINT=http://localhost:9000` (or actual host IP) instead of `http://minio:9000`. Dashboard shows "Recording is processing..." because the upload 500'd. **Transcription data is fine** (in Redis/Postgres) — only the recording file is lost.

### Root cause analysis (from code research 2026-04-08)

**K8s stopping hang — three paths to completed, all fragile:**
1. **Bot self-callback** (`vexa-bot/core/src/index.ts:791`): bot calls `/bots/internal/callback/status_change` with status=completed. On K8s: fails if meeting-api unreachable from bot pod (DNS, network policy, pod IP change). Fails silently after 3 retries.
2. **Runtime-API exit callback** (`runtime-api/lifecycle.py:65-110`): pod watcher detects exit, fires callback. K8s issue: `DELETE /containers/{name}` (`api.py:308-318`) calls `stop()` then `remove()` — both call `delete_namespaced_pod`, so double-delete. Watcher may miss the event during reconnect. Also: DELETE endpoint calls `state.set_stopped()` but does NOT call `_fire_exit_callback()`.
3. **Delayed Stop safety net** (`meeting-api/meetings.py:494-544`): 90s asyncio.sleep, then force-complete. **No persistence** — if meeting-api pod restarts during the 90s window, task is lost. Meeting stays stuck forever.

**Segments lost after stop — redis_client=None after restart:**
- `meeting-api/main.py:101-108`: Redis client set once at startup. If Redis is down, `redis_client = None` forever. **No reconnect logic.**
- `collector/endpoints.py:169`: `if redis_c:` — if None, skips Redis, returns only Postgres segments.
- `collector/db_writer.py`: flushes Redis→Postgres every 10s. Segments need 30s immutability threshold. If meeting-api restarts before flush, Redis segments lost.
- **No explicit flush on meeting completion.** Post-meeting `run_all_tasks()` reads both Redis+Postgres via the internal endpoint, but if redis_client is None, it only sees Postgres.

**Key files:**
- Stop flow: `meetings.py:1282-1374`, `meetings.py:494-544`
- Callbacks: `callbacks.py:90-234` (exit), `callbacks.py:311-461` (status_change)
- Segment persistence: `collector/db_writer.py:31-142`, `collector/endpoints.py:145-265`
- K8s backend: `runtime-api/backends/kubernetes.py:169-193` (double-delete), `273-315` (watcher)
- Redis startup: `main.py:101-113` (no reconnect)
- Config: `config.py:28` `BOT_STOP_DELAY_SECONDS=90`

### What we need (new make targets)

1. **`make -C tests3 transcription-latency`** — during active meeting: send TTS, subscribe WS, log segment timestamps, report latency percentiles
2. **`make -C tests3 transcription-persistence`** — after meeting ends: verify segments in Postgres (not just Redis), compare count to WS count during meeting
3. **`make -C tests3 bot-stopping`** — stop bot, verify reaches "completed" within timeout, check recordings uploaded, webhook fired
4. Fix meeting-tts-teams to run on compose (not just helm) to avoid lite loopback issue

### What needs fixing (code)

1. **Redis reconnect**: `main.py` needs reconnect logic — if initial ping fails, retry periodically, not stay None forever
2. **Explicit segment flush on meeting completion**: `run_all_tasks()` should force `db_writer` to flush remaining segments for the meeting before post-meeting processing
3. **K8s exit callback gap**: `DELETE /containers` endpoint should call `_fire_exit_callback()`, not just `state.set_stopped()`
4. **Delayed Stop persistence**: 90s safety net should survive meeting-api restart (Redis-backed timer or cron instead of in-memory asyncio task)
5. **Double-delete**: K8s backend `remove()` delegates to `stop()` which both call `delete_namespaced_pod` — should be idempotent

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
