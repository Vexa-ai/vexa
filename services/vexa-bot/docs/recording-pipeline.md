# Recording & Post-Meeting Transcription Pipeline

## WHY

The bot has two transcription modes: **live** (per-speaker audio → transcription-service during meeting) and **deferred** (record audio → transcribe post-meeting). The per-speaker refactoring changed how audio is processed and speakers are identified. We need to verify the recording pipeline, speaker metadata persistence, and deferred transcription speaker mapping all still work.

## WHAT

### Three execution paths

| Path | When | Flow |
|------|------|------|
| **Live** | `transcribeEnabled=true` | Per-speaker audio → transcription-service → Redis → collector → Postgres |
| **Record-only** | `transcribeEnabled=false` | Audio recording → MinIO → dashboard "Transcribe" button |
| **Deferred** | User clicks "Transcribe" | Download from MinIO → transcription-service → speaker mapping from `meeting.data.speaker_events` |

### Audio recording — VERIFIED INTACT (confidence: 95%)

Recording and per-speaker pipelines **coexist independently**:
- **Recording**: MediaRecorder on combined stream → `__vexaSaveRecordingBlob` → `RecordingService.writeBlob()` → WAV on disk → upload on exit
- **Per-speaker**: ScriptProcessor per media element → `__vexaPerSpeakerAudioData` → Node.js callback → transcription-service

Different audio sources, different delivery mechanisms. No conflict.

**Flush triggers** (3 paths, all verified in code):
1. Graceful leave → `leaveGoogleMeet()` calls `__vexaFlushRecordingBlob("manual_leave")`
2. Timeout (alone/startup) → `stopWithFlush()` in monitoring loop
3. Browser events → `beforeunload` / `visibilitychange` listeners

**Upload**: `performGracefulLeave()` calls `activeRecordingService.upload(recordingUploadUrl, token)` with multipart form + metadata (meeting_id, session_uid, format, duration, file_size).

**Remaining risk (5%)**: If `botConfig.recordingUploadUrl` is not configured, upload silently skips. Verify bot-manager passes this field.

### Speaker event collection — VERIFIED INTACT (confidence: 95%)

**Two parallel paths, both working:**

1. **Real-time (Redis):** `segment-publisher.ts:publishSpeakerEvent()` → XADD to `speaker_events_relative` stream → collector consumes via consumer group → stores in Redis Sorted Set `speaker_events:{session_uid}`

2. **In-memory accumulation:** Browser accumulates `window.__vexaSpeakerEvents[]` throughout meeting → bot reads at exit via `page.evaluate(() => window.__vexaSpeakerEvents || [])` → included in unified callback payload → bot-manager persists to `meeting.data.speaker_events` JSONB

**Exit flow** (`performGracefulLeave()` in `index.ts:619-631`):
```
1. Platform leave (flush recording)
2. Cleanup per-speaker pipeline
3. Upload recording
4. Read __vexaSpeakerEvents from page  ← verified
5. Send unified callback with speaker_events  ← verified
6. Close connections & exit
```

**Bot-manager persistence** (`main.py:1759-1767`): Receives `speaker_events` in `BotStatusChangePayload`, writes to `meeting.data['speaker_events']` with `flag_modified`.

**All three platforms**: Google Meet, Teams (both via `window.__vexaSpeakerEvents`), Zoom (via `getZoomSpeakerEvents()` module function).

### Collector segment parsing — VERIFIED INTACT (confidence: 98%)

| Field | Bot publishes | Collector expects | Match? |
|-------|--------------|-------------------|--------|
| start | ✓ | ✓ | YES |
| end | ✓ | ✓ | YES |
| text | ✓ | ✓ | YES |
| language | ✓ | ✓ | YES |
| completed | ✓ | ✓ | YES |
| speaker | ✓ | ✓ | YES |
| absolute_start_time | ✓ | ✓ | YES |
| absolute_end_time | ✓ | ✓ | YES |

All snake_case, no mismatches. Collector handles producer-labeled `speaker` field directly:
```python
segment_speaker = segment.get('speaker')
if segment_speaker:
    mapped_speaker_name = segment_speaker
    mapping_status = "PRODUCER_LABELED"
```

### Post-meeting speaker mapping

`bot-manager/app/main.py:_map_speakers_to_segments()`:
1. Read `meeting.data.speaker_events` array
2. Build time ranges per speaker: `{name: [[start_ms, end_ms], ...]}`
3. For each deferred segment, find speaker with **maximum overlap**
4. Assign `segment.speaker = best_match_speaker`
5. Write to `Transcription` table with speaker attribution

### Per-speaker live pipeline (`index.ts:initPerSpeakerPipeline`)

```
Media elements → AudioContext → ScriptProcessor → __vexaPerSpeakerAudioData(index, samples)
    → handlePerSpeakerAudioData() → resolveSpeakerName() → VAD check
    → SpeakerStreamManager buffer (2s submit interval, 2 consecutive match confirmation)
    → TranscriptionClient.transcribe() (HTTP POST, WAV multipart)
    → onSegmentConfirmed → SegmentPublisher → Redis XADD + PUBLISH
```

## HOW — Remaining risks

### 1. Pipeline init silently fails (confidence: 70%)

`initPerSpeakerPipeline()` returns `false` if `transcriptionServiceUrl` is missing, but the bot continues running. No transcription happens, no segments to Redis, but recording still works.

**Verify:** Is `TRANSCRIPTION_SERVICE_URL` set in Docker env? Is `botConfig.transcriptionServiceUrl` passed from bot-manager?

### 2. Speaker name resolution (confidence: 90% — fix implemented)

Fixed: speaking signal checked first, TTL cache, junk name filter. See `docs/speaker-name-resolution.md`.

**Remaining risk:** Speaking signal requires exactly one person talking. During simultaneous speech, falls back to positional mapping.

### 3. Recording upload URL not configured (confidence: unknown)

If `botConfig.recordingUploadUrl` is undefined, `RecordingService.upload()` silently skips. Recording is captured but never uploaded.

**Verify:** Does bot-manager include `recordingUploadUrl` in BOT_CONFIG?

### 4. Deferred transcription hallucination (confidence: 80% — fix identified)

Full recording sent to Whisper without VAD preprocessing → long silence gaps trigger repetition loops. See `docs/hallucination-pipeline.md`.

**Fix:** Add Silero VAD pre-segmentation in deferred transcription path.

### ~~5. Recording not captured~~ VERIFIED OK

Recording pipeline coexists with per-speaker pipeline. `__vexaSaveRecordingBlob` is called, MediaRecorder runs on combined stream independently.

### ~~6. Speaker events not persisted~~ VERIFIED OK

Full chain works: browser → bot exit → unified callback → bot-manager → `meeting.data.speaker_events`.

### ~~7. Collector format mismatch~~ VERIFIED OK

All field names match. Collector handles producer-labeled speaker field.

## Key files

| File | Role |
|------|------|
| `core/src/services/recording.ts` | WAV file creation + upload |
| `core/src/index.ts:525-702` | performGracefulLeave (exit flow) |
| `core/src/index.ts:981-1070` | Per-speaker pipeline init |
| `core/src/services/unified-callback.ts` | Exit callback payload (includes speaker_events) |
| `core/src/services/segment-publisher.ts` | Redis XADD + PUBLISH |
| `core/src/services/speaker-identity.ts` | Name resolution + cache |
| `platforms/googlemeet/recording.ts` | Browser-side audio + speaker detection + flush |
| `platforms/googlemeet/leave.ts:106-112` | Flush recording on manual leave |
| `bot-manager/app/main.py:1759-1767` | Persist speaker_events to meeting.data |
| `bot-manager/app/main.py:2994-3018` | _map_speakers_to_segments (deferred) |
| `transcription-collector/streaming/processors.py` | Segment consumption + Postgres persistence |

## Test results (2026-03-15)

### Messy meeting framework

Two test modes validate both transcription paths using generated messy audio (overlaps, noise, pauses, multilingual):

| Test | Path | What it validates |
|------|------|-------------------|
| `run_test.py` | Live (per-speaker) | POST each speaker's WAV independently → validate keywords, hallucinations, language detection |
| `test_deferred.py` | Deferred (combined) | Mix all speakers → POST combined WAV → map speakers by timestamp overlap → validate attribution |

**Location:** `services/vexa-bot/tests/messy-meeting/`

### Per-speaker path results (run_test.py)

`full-messy` scenario (46s, 3 speakers, overlaps, noise, Russian): **7/7 passed**
- Keyword attribution, no cross-contamination, no hallucinations, no duplicates, multilingual detection

`chaos-meeting` scenario (5min, 22 utterances, heavy overlaps, -15dB noise): **3/6 passed**
- Hallucination found: repetition loops in long silence gaps (`"so much so much so" x37`)
- Root cause: long per-speaker WAVs with silence gaps trigger Whisper hallucination. See `docs/hallucination-pipeline.md`

### Deferred path results (test_deferred.py)

`full-messy` scenario: **speaker mapping 6/6 correct (100%)**
- Combined recording correctly captures all speakers
- Timestamp-based speaker mapping works perfectly
- All segments attributed to correct speakers

### Key findings

1. **Per-speaker transcription is accurate** — keywords survive, no cross-contamination between speakers, Russian detected correctly
2. **Deferred speaker mapping works** — timestamp overlap algorithm correctly attributes segments to speakers
3. **Hallucination on long silence gaps** — real pipeline bug found in 5-min scenario, fix identified (VAD pre-segmentation + per-segment compression ratio filter)

## Verification checklist

- [x] Recording + per-speaker pipelines coexist (code verified)
- [x] `__vexaSaveRecordingBlob` called and flushed on exit (code verified)
- [x] `__vexaSpeakerEvents` read at exit and sent in callback (code verified)
- [x] Bot-manager persists speaker_events to meeting.data (code verified)
- [x] Collector parses new segment format correctly (field names verified)
- [x] Collector handles producer-labeled speaker field (code verified)
- [x] Per-speaker transcription accurate (messy meeting test, full-messy 7/7)
- [x] Deferred speaker mapping correct (test_deferred, 6/6 attribution)
- [ ] `botConfig.recordingUploadUrl` configured in production
- [ ] `botConfig.transcriptionServiceUrl` configured in production
- [ ] Bot logs show `[PerSpeaker] Pipeline initialized` in production
- [ ] Deferred transcription handles silence gaps (hallucination fix pending)
