---
services: [meeting-api, vexa-bot]
tests3:
  targets: [post-meeting, smoke]
  checks: [MINIO_ENDPOINT_SET, MINIO_BUCKET_SET]
---

# Post-Meeting Transcription

## Why

After a meeting ends, the recording is in MinIO. Deferred transcription runs Whisper on the full recording with speaker mapping from live speaker events. Higher accuracy than realtime (full-file context), and produces segments even if realtime transcription was disabled.

## What

```
Meeting ends → bot uploads recording to MinIO → POST /meetings/{id}/transcribe
  → meeting-api downloads recording → ffmpeg webm→wav → Whisper (batch)
  → speaker mapping via speaker_events → segments stored in Postgres
  → GET /transcripts/{platform}/{id} returns deferred + realtime segments
```

### Components

| Component | File | Role |
|-----------|------|------|
| deferred transcription | `services/meeting-api/meeting_api/meetings.py` | Download recording, call Whisper, map speakers |
| recording upload | `services/vexa-bot/core/src/services/recording.ts` | Upload webm to MinIO on bot exit |
| speaker mapping | `services/meeting-api/meeting_api/meetings.py:_map_speakers_to_segments()` | Map Whisper timestamps to speaker events |

## How

### 1. Trigger deferred transcription after a meeting ends

Once the bot is `completed` and the recording has been uploaded to MinIO:

```bash
curl -s -X POST http://localhost:8056/meetings/137/transcribe \
  -H "X-API-Key: $VEXA_API_KEY"
# 200 {"segments": 9, "duration_s": 120.5}

# If realtime segments already exist:
# 409 {"detail": "This meeting is already transcribed (15 segments). Multiple transcripts per meeting not implemented."}
```

### 2. Retrieve transcription segments

```bash
curl -s -H "X-API-Key: $VEXA_API_KEY" \
  http://localhost:8056/transcripts/gmeet/137
# {
#   "meeting_id": 137,
#   "segments": [
#     {"start": 0.0, "end": 3.5, "text": "Hello everyone", "speaker": "Alice", "source": "realtime"},
#     {"start": 3.5, "end": 7.2, "text": "Hi Alice", "speaker": "Bob", "source": "realtime"},
#     ...
#   ]
# }
```

### 3. Check recording in MinIO

```bash
# Verify the recording exists (via mc CLI or S3 API)
mc ls minio/vexa-recordings/
# recording-137.webm
```

## DoD

| # | Check | Weight | Ceiling | Floor | Status | Evidence | Last checked | Test |
|---|-------|--------|---------|-------|--------|----------|--------------|------|
| 1 | Recording uploaded to MinIO after meeting ends | 20 | ceiling | 0 | FAIL | Lite: bot reaches completed but recording upload fails — `http://minio:9000` DNS unresolvable in host-network mode. K8s: bot stuck in stopping, upload never attempted. Config needs MINIO_ENDPOINT=localhost for lite. | 2026-04-08 | Phase 5a, 5b |
| 2 | POST /meetings/{id}/transcribe returns segments | 25 | ceiling | 0 | PARTIAL | Transcription engine works (GMeet: 9 segments from recording, Teams: 10 segments), but 0 deferred segments returned via API after transcription. 409 dedup prevention works when realtime segments exist. | 2026-04-05T21:50Z | 10-verify-post-meeting |
| 3 | Speaker names attributed (not all "Unknown") | 25 | ceiling | 0 | PARTIAL | GMeet: 3/3 correct (Alice, Bob, Charlie). Teams: names are UUIDs (`Teams Participant (uuid)`) not display names — see Known Issues | 2026-04-05T21:50Z | 10-verify-post-meeting |
| 4 | Deferred segments consistent with realtime | 15 | — | 0 | FAIL | 0 segments persisted on both platforms. Pipeline not delivering to storage. | 2026-04-08 | Phase 5a, 5b |
| 5 | Works for GMeet and Teams | 15 | — | 0 | FAIL | Both platforms: TTS heard, 0 segments persisted. Teams: bot stuck in stopping. GMeet lite: loopback prevents transcription. | 2026-04-08 | Phase 5a, 5b |

Confidence: 10 (ceiling item 1 FAIL, items 4-5 FAIL. Items 2-3 PARTIAL from previous test. Transcription pipeline broken end-to-end.)

## Known Issues

### 1. Duplicate segments — deferred transcription does not check for existing realtime segments

`POST /meetings/{id}/transcribe` (`meetings.py:1619`) inserts deferred segments into the `transcriptions` table without checking if realtime segments already exist for the same time range. `GET /transcripts` returns all rows, causing every utterance to appear twice in the dashboard.

**Root cause:** `meetings.py:1619` — `segment_id = f"deferred:{meeting_id}:{start:.3f}"` inserted alongside existing realtime segments.

**Fix applied:** `meetings.py` returns 409: "This meeting is already transcribed (N segments). Multiple transcripts per meeting not implemented." Prevents duplicate insertion at the source.

### 2. Teams deferred speaker names are UUIDs, not display names

Teams `speaker_events` use DOM element extraction (`recording.ts:extractName`). When name selectors fail (common for guests), the fallback at `recording.ts:428` is `Teams Participant ({uuid})`. This UUID is then mapped to deferred segments by `_map_speakers_to_segments()`.

**Root cause:** Teams speaker identity comes from DOM mutation observation, not from captions. The `extractName()` method (`recording.ts:387`) tries `nameSelectors` from the DOM but falls back to `extractId()` when selectors miss. Realtime gets correct names because it uses Teams live captions (`captions.ts`), which include display names directly in `data-tid="author"` elements. Deferred re-transcribes from the recording and relies on `speaker_events` persisted by the bot — which already have the wrong names.

**Evidence from DB (meeting 66):**
- Realtime: `Alice (Speaker) (Guest)`, `Bob (Speaker) (Guest)`, `Charlie (Speaker) (Guest)` — correct (from captions)
- Deferred: `Dmitry Grankin`, `Teams Participant (afd164dd-...)`, `Unknown` — wrong (from DOM mutation)
