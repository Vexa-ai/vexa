# Agent Test: Transcription Collector

## Prerequisites
- Services running: transcription-collector, redis, postgres (Docker)
- Environment: .env configured with REDIS_HOST, DATABASE_URL
- Setup: `docker compose up -d transcription-collector redis postgres`

## Tests

### Test 1: Redis Stream Consumer Lifecycle
**Goal:** Verify the collector consumes transcription segments from Redis streams.
**Setup:** Publish test segments to the `transcription_segments` Redis stream.
**Verify:** Segments appear in the database after processing.
**Pass criteria:** All published segments are persisted within 10 seconds.

### Test 2: Transcript API Response Format
**Goal:** Verify the REST API returns transcripts in the expected schema.
**Setup:** Insert test meeting + segments in the database. Call `GET /transcripts/{platform}/{meeting_id}`.
**Verify:** Response matches `TranscriptionResponse` schema with segments, speaker labels, timestamps.
**Pass criteria:** Response validates against the schema. Segments ordered by start_time.

### Test 3: Filter Application During Ingestion
**Goal:** Verify that non-informative segments are filtered before persistence.
**Setup:** Publish segments including `[BLANK_AUDIO]`, empty strings, and valid text to Redis.
**Verify:** Only valid segments appear in the database. Filtered segments are not persisted.
**Pass criteria:** Zero non-informative segments in the database after processing.

### Test 4: Meeting List API
**Goal:** Verify `GET /meetings` returns meetings for the authenticated user.
**Setup:** Create multiple meetings via the API or database. Call the endpoint with an API key.
**Verify:** Response includes all meetings owned by the API key, none from other users.
**Pass criteria:** Correct meeting count. No data leakage across API keys.

### Test 5: Speaker Events Stream Consumer
**Goal:** Verify speaker events are consumed and stored in Redis sorted sets.
**Setup:** Publish speaker events to the speaker_events_relative stream.
**Verify:** Events appear in the correct Redis sorted set with proper TTL.
**Pass criteria:** Events retrievable by meeting ID. TTL matches REDIS_SPEAKER_EVENT_TTL.
