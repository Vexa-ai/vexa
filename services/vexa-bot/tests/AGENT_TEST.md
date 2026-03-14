# Agent Test: Vexa Bot (Per-Speaker Pipeline)

## Prerequisites

- Services running: `redis`, `transcription-service`
- Mock meeting page served: `cd services/vexa-bot/tests/mock-meeting && bash serve.sh`
- Environment: `TRANSCRIPTION_SERVICE_URL`, `REDIS_URL` (or defaults via docker-compose)

## Tests

### Test 1: Mock meeting — 3 speakers, per-speaker separation

**Goal:** Verify the bot discovers 3 separate audio tracks from the mock meeting page and creates independent per-speaker buffers.

**Setup:** Start mock meeting page (`bash serve.sh`). Launch bot with `MEETING_URL=http://localhost:8080`.

**Verify:**
- Bot logs show 3 speakers discovered (Alice, Bob, Carol)
- Each speaker gets a separate `SpeakerStreamManager` buffer
- No audio cross-contamination (Alice's buffer doesn't contain Bob's audio)

**Evidence:** Bot stdout logs showing speaker discovery and buffer creation.

**Pass criteria:** 3 distinct speakers discovered, 3 buffers created, logs show per-speaker audio processing.

### Test 2: E2E — audio to transcription to Redis with speaker labels

**Goal:** Verify the full pipeline: audio capture -> transcription-service -> Redis XADD with correct speaker labels.

**Setup:** Same as Test 1, plus `redis` and `transcription-service` running.

**Verify:**
- `redis-cli XRANGE transcription_segments - +` shows segments
- Each segment has a `speaker` field with the correct name
- Segments contain coherent text (not hallucinations or empty strings)
- `redis-cli XRANGE speaker_events - +` shows lifecycle events

**Evidence:** Redis output from `bash tests/print_transcripts.sh`.

**Pass criteria:** At least 1 segment per speaker with correct speaker label and non-empty text.

### Test 3: VAD — silence filtered, speech passed

**Goal:** Verify Silero VAD filters silence and only submits speech audio to transcription-service.

**Setup:** Same as Test 1.

**Verify:**
- Bot logs show VAD filtering: silence chunks skipped, speech chunks passed
- Transcription-service receives fewer requests than total audio chunks (silence filtered)
- No transcription requests during periods of silence

**Evidence:** Bot logs with VAD filter counts.

**Pass criteria:** At least some audio chunks filtered as silence. Speech chunks produce transcription requests.

### Test 4: Confirmation buffer — DRAFT to CONFIRMED to PUBLISHED flow

**Goal:** Verify the confirmation-based buffer only publishes segments when text stabilizes.

**Setup:** Same as Test 2.

**Verify:**
- Bot logs show resubmission cycles (same buffer sent multiple times)
- `confirmCount` increments when consecutive transcriptions match (fuzzy)
- Segment is emitted only after reaching `confirmThreshold` (default 2)
- Hard cap flush fires at `maxBufferDuration` (default 15s) if confirmation doesn't converge

**Evidence:** Bot logs showing confirmation cycle. Unit test results from `speaker-streams.test.ts`.

**Pass criteria:** Segments are not emitted on first transcription response. At least one segment confirmed via fuzzy match.

### Test 5: Language detection — auto-detect, lock on confidence

**Goal:** Verify per-speaker language detection auto-detects on first chunk and locks after high confidence.

**Setup:** Same as Test 2. Use English audio for mock meeting speakers.

**Verify:**
- Bot logs show language detection per speaker: `[LANGUAGE] Alice -> en (prob=0.XX)`
- After high-confidence detection (prob > threshold), subsequent logs show "locked"
- Locked language is sent to transcription-service in subsequent requests

**Evidence:** Bot logs with language detection entries.

**Pass criteria:** Each speaker gets a detected language. At least one speaker's language is locked after high-confidence detection.

### Test 6: Screen share — unmapped tracks labeled "Presentation"

**Goal:** Verify that audio tracks not associated with a participant tile are labeled "Presentation".

**Setup:** A meeting with a screen share active, or a mock page with an extra `<audio>` element not inside a participant tile.

**Verify:**
- Bot logs show: `Element N -> "Presentation" (no participant tile -- screen share?)`
- Segments from that track have `speaker: "Presentation"`

**Evidence:** Bot logs and Redis output.

**Pass criteria:** Unmapped track is labeled "Presentation", not "Speaker 1" or "Unknown".

### Test 7: Speaker names — DOM resolution for Google Meet

**Goal:** Verify speaker-identity.ts resolves participant names from Google Meet DOM selectors.

**Setup:** Mock meeting page with DOM structure matching Google Meet participant tiles.

**Verify:**
- `speaker-identity.ts` finds participant name elements using platform-specific selectors
- Names are resolved on first track discovery (one-time lookup, cached)
- Names are correctly associated with audio tracks

**Evidence:** Bot logs showing `[SpeakerIdentity] Element N -> "Name" (platform: google_meet)`.

**Pass criteria:** All mock meeting speakers resolved by name from DOM. No "Unknown" speakers for tracks that have a participant tile.
