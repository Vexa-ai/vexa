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

## DoD

| # | Check | Weight | Ceiling | Floor | Status | Evidence | Last checked | Test |
|---|-------|--------|---------|-------|--------|----------|--------------|------|
| 1 | Recording uploaded to MinIO after meeting ends | 20 | ceiling | 0 | FAIL | GMeet: "[Google Recording] Audio capture disabled by config." — recording not uploaded. Teams: "No bot config for leave callback" — recording upload failed. Both platforms broken. | 2026-04-05T21:50Z | 10-verify-post-meeting |
| 2 | POST /meetings/{id}/transcribe returns segments | 25 | ceiling | 0 | PARTIAL | Transcription engine works (GMeet: 9 segments from recording, Teams: 10 segments), but 0 deferred segments returned via API after transcription. 409 dedup prevention works when realtime segments exist. | 2026-04-05T21:50Z | 10-verify-post-meeting |
| 3 | Speaker names attributed (not all "Unknown") | 25 | ceiling | 0 | PARTIAL | GMeet: 3/3 correct (Alice, Bob, Charlie). Teams: names are UUIDs (`Teams Participant (uuid)`) not display names — see Known Issues | 2026-04-05T21:50Z | 10-verify-post-meeting |
| 4 | Deferred segments consistent with realtime | 15 | — | 0 | FAIL | 409 dedup prevents re-transcription when realtime segments exist. When deferred does run, API returns 0 deferred segments — persistence broken. | 2026-04-05T21:50Z | 10-verify-post-meeting |
| 5 | Works for GMeet and Teams | 15 | — | 0 | FAIL | Recording upload broken on both platforms. Teams speaker mapping produces UUIDs. Pipeline code exists but never exercised end-to-end. | 2026-04-05T21:50Z | 10-verify-post-meeting |

Confidence: 20 (ceiling item 1 FAIL — recording upload broken both platforms. Item 2 PARTIAL — engine works but persistence broken. Item 3 PARTIAL — GMeet names correct, Teams UUIDs. Items 4-5 FAIL. Dedup prevention works correctly.)

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
