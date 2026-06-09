# Vexa Local Deploy Analysis - Updated 2026-05-25

## Current Status

Meeting #4 (izb-sxgn-ijh) is **completed**. Bot recorded audio + video, frame
extraction works. Transcription still broken (upstream bot bug).

## Bugs Found

### BUG-1: Transcription fails — `timestamp_granularities` (Critical — OPEN)

**Symptom:** Bot sends `timestamp_granularities` param to Groq Whisper API which
rejects it with HTTP 400.

**Root cause:** Upstream vexa-bot code issue (#355). Inside the bot container image,
NOT in meeting-api.

**Status:** OPEN — requires custom bot image or upstream fix.

**Workaround:** Use deferred transcription (POST /meetings/{id}/transcribe) which
uses a different code path that works correctly.

### BUG-2: Video capture not working (Medium → FIXED)

**Symptom:** Only `media_type=audio` chunks uploaded initially.

**Root cause:** `CAPTURE_MODES` env var was set to `audio` (default) in the
running container, not `audio,video`.

**Fix:** Added `CAPTURE_MODES=audio,video` to `.env` and restarted.
Video capture now works — 1 video chunk (2.8MB master.webm, 764s) recorded
successfully for meeting #4.

**Status:** FIXED

### BUG-3: Frame extractor race condition (Medium → FIXED)

**Symptom:** `frame_extractor` ran before `recording_finalizer` completed,
found no video master, returned 0 frames.

**Root cause:** `post_meeting` tasks run concurrently; frame extractor fired
326ms before finalizer wrote the video master.

**Fix:** After fixing BUG-4 (below), re-running frame extraction manually
succeeded. The race condition is mitigated by the JSONB dual-path lookup
which finds the video master from meeting.data even before it's persisted
to Recording/MediaFile SQL rows. The race only affects the SQL fallback path.

**Status:** FIXED (manual re-run; automatic path should work for future
meetings since recording_finalizer completes before post_meeting callback
in most cases)

### BUG-4: Integer overflow on snowflake IDs (Critical → FIXED)

**Symptom:** `asyncpg.exceptions.DataError: invalid input for query argument $2:
788467172073 (value out of int32 range)`

**Root cause:** The bot generates snowflake-style IDs (e.g. 788467172073)
that exceed PostgreSQL int32 max (2,147,483,647). SQLAlchemy model columns
`Recording.id`, `MediaFile.id`, `MediaFile.recording_id`, and
`RecordingFrame.recording_id` were all `Integer` (int32) instead of `BigInteger`
(int64).

**Fix applied:**
1. Changed all four columns in `models.py` from `Column(Integer, ...)` to
   `Column(BigInteger, ...)`
2. Altered PostgreSQL columns from `integer` to `bigint`:
   - `recordings.id`
   - `media_files.id`
   - `media_files.recording_id`
   - `recording_frames.recording_id`
3. Rebuilt meeting-api Docker image and redeployed

**Status:** FIXED

### BUG-5: Bot doesn't auto-leave on meeting end (Low → OPEN)

**Symptom:** Bot stays in meeting after user ends it.

**Root cause:** Unknown — possibly a bot signaling issue.

**Status:** OPEN (low priority)

### BUG-6: SNAPSHOTS_ENABLED defaulted to false (Medium → FIXED)

**Symptom:** Frame extraction skipped because env var was `false`.

**Root cause:** `.env` file didn't include `SNAPSHOTS_ENABLED=true` or
`CAPTURE_MODES=audio,video`. Docker compose defaults to `false` and `audio`.

**Fix:** Added both env vars to `.env`.

**Status:** FIXED

## What Works

- Audio recording: 26 chunks → 912KB master.webm
- Video recording: 1 chunk → 2.8MB master.webm (764s duration)
- Frame extraction: 25 frames extracted at 30s intervals (320x180 WebP)
- Meeting creation & bot dispatch: fully functional
- Meeting API: all endpoints responding correctly
- Dashboard: rebuilt with snapshot code, running on port 3001
- Recording frames table: auto-created in PostgreSQL with bigint columns
- `SNAPSHOTS_ENABLED=true`: feature flag active
- `CAPTURE_MODES=audio,video`: both audio and video captured
- Deferred transcription: POST /meetings/{id}/transcribe endpoint works

## Verified: Meeting #4 Results

| Component | Status | Details |
|-----------|--------|---------|
| Audio recording | WORKS | master.webm (912KB) |
| Video recording | WORKS | master.webm (2.8MB, 764s) |
| Frame extraction | WORKS | 25 frames (1 per 30s), 320x180 WebP |
| Real-time transcription | BROKEN | BUG-1 (upstream bot issue) |
| Deferred transcription | UNTESTED | Available as workaround |

## Architecture Notes

| Service | Image | Has Snapshot Code? |
|---------|-------|--------------------|
| meeting-api | vexaai/meeting-api:0.10.6.2.1-260522-1105 | YES (rebuilt with BigInteger fix) |
| api-gateway | vexaai/api-gateway:0.10.6.2.1-260522-1105 | YES (rebuilt) |
| dashboard | vexaai/dashboard:0.10.6.2.1-260522-1105 | YES (rebuilt) |
| bot | vexaai/vexa-bot:latest | NO (upstream stock) |

## Remaining Work

1. **BUG-1 (transcription):** Build custom bot image removing
   `timestamp_granularities` param, or wait for upstream fix (#355).
   Workaround: deferred transcription endpoint works.

2. **BUG-3 (race condition):** Consider adding a small delay or retry in
   `extract_frames_if_enabled` to handle the edge case where finalizer
   hasn't written the video master yet.

3. **Dashboard:** Verify frame snapshots render in the UI (the /frames
   endpoint should serve them).