---
id: test/verify-post-meeting
type: validation
requires: [test/verify-transcription]
produces: [POST_MEETING_SEGMENTS]
validates: [post-meeting-transcription, meeting-chat]
docs: [features/post-meeting-transcription/README.md, features/meeting-chat/README.md, services/meeting-api/README.md]
mode: machine
---

# Verify Post-Meeting Transcription

> Follows [RULES.md](RULES.md).

After bots leave a meeting, verify that:
1. Recording is uploaded to MinIO/S3
2. `POST /meetings/{id}/transcribe` produces deferred segments
3. Speaker mapping works via `meeting.data.speaker_events` → `_map_speakers_to_segments()`
4. Deferred segments are consistent with realtime segments

## Inputs

| Name | From | Default | Description |
|------|------|---------|-------------|
| GATEWAY_URL | test/infra-up | — | API gateway URL |
| LISTENER_TOKEN | test/api-full | — | API token for listener user |
| MEETING_PLATFORM | test/create-live-meeting | — | Platform |
| NATIVE_MEETING_ID | test/create-live-meeting | — | Meeting ID |
| REALTIME_SEGMENTS | test/verify-transcription | — | Count from realtime test (for comparison) |

## Steps

1. Verify recording exists for the meeting via `GET /recordings`
2. Call `POST /meetings/{meeting_id}/transcribe` with optional language
3. Meeting-api: downloads recording from MinIO → ffmpeg webm→wav → Whisper → speaker mapping → stores segments
4. Verify via `GET /transcripts/{platform}/{native_id}` — deferred segments have `segment_id=deferred:*`
5. Assert: deferred segments exist, speakers attributed via `speaker_events`, text consistent with realtime
6. **Ground truth evaluation** — compare deferred segments to the known TTS input text (same analysis as 09 Phase 1b)
7. **Dedup assertion** — verify the transcript API does not return duplicate content:
   - Count realtime segments and deferred segments separately (by `segment_id` prefix)
   - For each deferred segment, check if a realtime segment covers the same content
   - If both exist for the same utterance → log as **DUPLICATE BUG** (backend should filter)
   - Report: `N realtime + M deferred, K duplicated utterances`

> assert: deferred segments exist with speaker attribution
> assert: deferred WER < 15% vs ground truth (deferred typically has better punctuation)
> assert: 0 duplicate utterances shown to user (if both realtime and deferred exist for same content, backend must filter one)
> on-fail for dedup: this is a backend bug in GET /transcripts — it should not return both realtime and deferred for the same utterance

## Script

```bash
eval $(./scripts/10-verify-post-meeting.sh GATEWAY_URL LISTENER_TOKEN MEETING_PLATFORM NATIVE_MEETING_ID)
```

## Outputs

| Name | Description |
|------|-------------|
| POST_MEETING_SEGMENTS | Number of deferred segments |
| RECORDING_UPLOADED | true if recording found in MinIO |

## Failure modes

| Symptom | Cause | Fix | Learned |
|---------|-------|-----|---------|
| No recording in MinIO | RECORDING_ENABLED=false or MinIO not configured | Check meeting data.recording_enabled, check MINIO_* env vars | |
| Recording exists but no deferred segments | POST_MEETING_HOOKS not configured or agent-api not processing | Check POST_MEETING_HOOKS env var, check agent-api logs | |
| Deferred segments differ wildly from realtime | Different model/settings for deferred vs realtime | Check transcription_tier config | Realtime uses streaming whisper, deferred uses batch — minor differences expected |
| speaker=None on deferred segments | No speaker_events in meeting.data | Check bot persisted speaker events at exit | Bots must persist SPEAKER_START/END events from Google Meet active speaker detection or Teams captions |
| 502 from transcription service | webm format not supported | Ensure ffmpeg installed in meeting-api container | meeting-api converts webm→wav via ffmpeg before sending to Whisper |
| Schema rejects dashboard fields | MeetingCreate extra="forbid" | Change to extra="ignore" in schemas.py:405 | Dashboard sends workspaceGitRepo/Token/Branch from localStorage |
| Duplicate utterances in transcript | GET /transcripts returns both realtime and deferred segments for same content | Fixed: POST /meetings/{id}/transcribe returns 409 if segments already exist | 2026-04-05: dashboard showed every utterance twice. Fix: meetings.py rejects deferred if transcription exists. |

## Dashboard API Patterns

The dashboard calls `POST /api/vexa/meetings/{id}/transcribe` with `{language?: string}`. This proxies to the gateway which forwards to meeting-api. The test script uses the same `POST /meetings/{id}/transcribe` path with the same payload shape to validate what the dashboard actually calls.

## Docs ownership

After this test runs, verify and update:

- **features/post-meeting-transcription/README.md**
  - DoD table: update Status, Evidence, Last checked for all items: #1 (recording uploaded to MinIO), #2 (POST /meetings/{id}/transcribe returns segments), #3 (speaker names attributed), #4 (deferred segments consistent with realtime), #5 (works for GMeet and Teams)
  - Components table: verify `services/meeting-api/meeting_api/meetings.py` (deferred transcription + `_map_speakers_to_segments()`), `services/vexa-bot/core/src/services/recording.ts` (recording upload) paths are correct
  - Architecture: verify the documented pipeline `meeting ends -> bot uploads recording -> POST /meetings/{id}/transcribe -> ffmpeg webm->wav -> Whisper -> speaker mapping -> Postgres` matches actual behavior — check meeting-api logs for ffmpeg conversion and Whisper call
  - Confidence score: recalculate after updating statuses

- **features/meeting-chat/README.md**
  - DoD table: update Status, Evidence, Last checked for item #4 (chat messages persisted after meeting ends) — verify chat messages are still retrievable via GET `/bots/{platform}/{id}/chat` after the meeting has ended and bots have left

- **services/meeting-api/README.md**
  - Recordings endpoints: verify GET `/bots/{platform}/{meeting_id}/recordings` returns recording data matching what was uploaded to MinIO
  - Internal Callbacks: verify POST `/bots/internal/callback/exited` was called by the bot on exit and that `data.recording_enabled` was set correctly
  - Environment variables: verify `TRANSCRIPTION_COLLECTOR_URL` and MinIO-related vars are set — if recording upload failed, check these
