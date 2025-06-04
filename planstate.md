# VEXA Speaker Identification Implementation Plan

## Current Status: 🚀 **PLAN UPDATED - Ready for Phased Implementation**

### 🚀 **Phase 1: Vexa Bot - Relative Speaker Timestamps & Event Transmission**
**Focus:** Modify Vexa Bot to generate speaker event timestamps relative to the start of audio transmission for each unique WebSocket session (`uid`). Send these relative speaker events to WhisperLive.
**Key Changes:**
1.  In browser context: on new WebSocket connection (`uid`) and *before first audio chunk is sent*, capture an internal `sessionAudioStartTimeMs = Date.now()`. This variable is internal to the browser script for the current session.
2.  For speaker events: calculate `relative_timestamp_ms = Date.now() - sessionAudioStartTimeMs`.
3.  Send `speaker_activity` messages with `uid` and this `relative_timestamp_ms`.
4.  Ensure `sessionAudioStartTimeMs` is correctly reset and re-captured upon WebSocket reconnection when a new `uid` is established and audio is about to start.
**Smoke Test:** Monitor WebSocket messages from Vexa Bot to WhisperLive. Verify `speaker_activity` payloads include `uid` and accurate `relative_timestamp_ms`. Test WebSocket reconnection scenarios to ensure timestamp relativity is maintained per session.

### 🚀 **Phase 2: WhisperLive - Dual Redis Stream Forwarding**
**Focus:** Configure WhisperLive to:
1.  Forward received relative speaker events (from Vexa Bot, containing `uid` and `relative_timestamp_ms`) to a *new* Redis stream (e.g., `speaker_events_relative`, configurable).
2.  Continue forwarding transcription segments (which already have `uid` and their own relative `start`/`end` timestamps) to the *existing* `transcription_segments` Redis stream.
**Smoke Test:** Use `redis-cli`. Monitor `speaker_events_relative` to confirm it receives speaker events with `uid` and `relative_timestamp_ms`. Monitor `transcription_segments` to confirm it receives transcription segments with `uid` and their own relative `start`/`end` times.

### 🚀 **Phase 3: Transcription Collector - Correlated Mapping & Unified Storage**
**Focus:** Enhance Transcription Collector to:
1.  Consume from both `speaker_events_relative` and `transcription_segments` Redis streams.
2.  Buffer speaker events and transcription segments, keyed by `uid`.
3.  Implement mapping logic: For each `uid`, correlate buffered speaker events with transcription segments using their respective relative timestamps (converting transcription segment times to milliseconds for direct comparison).
4.  Update the `transcriptions` PostgreSQL table schema to include `mapped_speaker_name`, `mapped_participant_id_meet`, and `speaker_mapping_status`.
5.  Store the mapped transcription segments (now enriched with speaker data) directly into the `transcriptions` table. **No separate `speaker_events_log` table will be created or used.**
6.  Continue to update the `MeetingSession` table's `session_end_utc` column based on `session_end` events from the `transcription_segments` stream.
**Smoke Test:** After Phases 1 & 2 populate data, trigger the mapping process in Transcription Collector. Query the `transcriptions` table to verify that speaker information (`mapped_speaker_name`, `speaker_mapping_status`, etc.) is correctly populated for various speaking scenarios. Verify `meeting_sessions.session_end_utc` is updated.

---

### 📋 **Next Actions (Reflect New Phased Approach):**

1.  **Immediate:** Implement Vexa Bot changes for relative speaker timestamps (Phase 1).
2.  **Short-term:** Modify WhisperLive to forward speaker events to `speaker_events_relative` and transcripts to `transcription_segments` (Phase 2).
3.  **Medium-term:** Develop Transcription Collector logic for dual-stream consumption, buffering, speaker-transcription mapping, and unified storage in the `transcriptions` table (Phase 3).

---
### 🎯 **Success Metrics (To be evaluated for each phase):**
*   **Phase 1:** Accurate relative timestamps for speaker events per session `uid`.
*   **Phase 2:** Correct routing of speaker and transcript data to their respective Redis streams with correct `uid` and relative timestamps.
*   **Phase 3:** Reliable mapping of speakers to transcription segments. Correct storage of enriched transcripts. Session end times correctly updated.

---

# Vexa Speaker Identification & Transcription Mapping Plan (Revised)

## I. Vexa Bot (Client-Side - `services/vexa-bot/core/src/platforms/google.ts`)

1.  **Integrate Speaker Detection Logic (Largely Existing):**
    *   Continue using adapted JavaScript code from `speakers_console_test.js` to monitor DOM mutations for speaking indicators.
    *   Capture: Event type (`SPEAKER_START` or `SPEAKER_END`), participant's display name, Google Meet's `data-participant-id`.

2.  **Implement Relative Timestamping for Speaker Events:**
    *   **Session Audio Start Time (`sessionAudioStartTimeMs`):**
        *   Within the browser's `page.evaluate` scope, for each new WebSocket connection (identified by a unique `currentSessionUid`):
            *   Just *before* the first audio data packet for this `currentSessionUid` is sent to WhisperLive, record an internal JavaScript variable: `let sessionAudioStartTimeMs = Date.now();`.
            *   This `sessionAudioStartTimeMs` is **not sent over WebSocket** but used as the local "time zero" for this specific audio session.
        *   **Reconnection Handling:** If the WebSocket connection drops and a new one is established (resulting in a new `currentSessionUid`), this `sessionAudioStartTimeMs` variable **must be reset and re-captured** when audio is about to start for the new session.
    *   **Calculating Relative Timestamps:**
        *   When a speaker event (START or END) is detected:
            *   `const eventAbsoluteTimeMs = Date.now();`
            *   `const relative_client_timestamp_ms = eventAbsoluteTimeMs - sessionAudioStartTimeMs;`

3.  **Send Speaker Events with Relative Timestamps via WebSocket to WhisperLive:**
    *   Construct a JSON message for speaker events.
    *   **WebSocket Message Format (Client to WhisperLive Server - Speaker Activity):**
        ```json
        {
          "type": "speaker_activity",
          "payload": {
            "event_type": "SPEAKER_START", // or "SPEAKER_END"
            "participant_name": "John Doe",
            "participant_id_meet": "meet-specific-id-string", // or "vexa-id-xxxx"
            "relative_client_timestamp_ms": 15234, // Calculated relative timestamp
            // Include existing contextual information already sent with audio config for this session:
            "uid": "websocket-connection-uid", // Critical: UID of the current WebSocket session
            "token": "bot-api-token",
            "platform": "google_meet",
            "meeting_id": "native-meeting-id",
            "meeting_url": "https://meet.google.com/xxx-yyy-zzz"
          }
        }
        ```
    *   These messages are sent in parallel with the audio stream for the same `uid`.

4.  **Signal Session End (Existing Logic - UID Critical):**
    *   When Vexa Bot prepares to leave, send the `session_control` message.
    *   **WebSocket Message Format (Client to WhisperLive Server - Session Control):**
        ```json
        {
          "type": "session_control",
          "payload": {
            "event": "LEAVING_MEETING",
            "uid": "websocket-connection-uid", // Critical for identifying the session
            "client_timestamp_ms": 1678886400000, // Absolute timestamp for this specific event
            "token": "bot-api-token",
            "platform": "google_meet",
            "meeting_id": "native-meeting-id"
          }
        }
        ```

## II. WhisperLive (Server-Side - `services/WhisperLive/whisper_live/server.py`)

1.  **Modify WebSocket Server to Handle Message Types (Largely Existing):**
    *   Recognize `type == "speaker_activity"` and `type == "session_control"`.
    *   Parse payloads.

2.  **Publish Speaker Events to a New Redis Stream (`speaker_events_relative`):**
    *   When a `speaker_activity` message is received:
        *   Extract `uid`, `relative_client_timestamp_ms`, and other speaker details.
        *   Publish to a new Redis stream (e.g., `REDIS_SPEAKER_EVENTS_RELATIVE_STREAM_KEY`, configurable).
        *   **Redis Message Payload (WhisperLive to Redis Stream `speaker_events_relative`):**
            ```json
            {
              // Fields directly from the "payload" of the client's message
              "uid": "websocket-connection-uid",
              "event_type": "SPEAKER_START",
              "participant_name": "John Doe",
              "participant_id_meet": "meet-specific-id-string",
              "relative_client_timestamp_ms": 15234, // Relative timestamp from client
              "token": "bot-api-token",
              "platform": "google_meet",
              "meeting_id": "native-meeting-id",
              "meeting_url": "https://meet.google.com/xxx-yyy-zzz",
              // Additional server-side metadata
              "server_received_timestamp_iso": "2023-03-15T12:00:00.123Z" // ISO 8601 when WhisperLive processed this
            }
            ```

3.  **Publish Transcription Segments to Existing Redis Stream (Existing Logic - UID Critical):**
    *   Continue generating transcription segments. These segments already contain relative timestamps (`start`, `end` in seconds) from the perspective of WhisperLive's audio processing start for that `uid`.
    *   Ensure the `uid` (identifying the WebSocket session) is included in the payload published to the `transcription_segments` Redis stream.
    *   **Example Redis Message Payload (WhisperLive to `transcription_segments` Stream - for one segment):**
        ```json
        {
          "type": "transcription", // or implied if this stream only carries transcripts
          "uid": "websocket-connection-uid",
          "token": "bot-api-token",
          "platform": "google_meet",
          "meeting_id": "native-meeting-id",
          "segments": [
            {
              "text": "Hello world",
              "start": 0.520, // Relative start time in seconds
              "end": 1.230,   // Relative end time in seconds
              "language": "en"
            }
            // ... more segments if batched
          ],
          "server_timestamp_iso": "2023-03-15T12:00:01.456Z"
        }
        ```

4.  **Handle Session End Signaling (Existing Logic - UID Critical):**
    *   If `LEAVING_MEETING` is received or connection drops, publish `session_end` to `transcription_segments` stream with the corresponding `uid`.
    *   **Redis `session_end` Message Payload (WhisperLive to `transcription_segments` Stream):**
        ```json
        {
          "type": "session_end",
          "uid": "websocket-connection-uid",
          "token": "bot-api-token",
          "platform": "google_meet",
          "meeting_id": "native-meeting-id",
          "session_end_timestamp_iso": "2023-03-15T13:00:00.000Z" // Authoritative ISO 8601 of session end
        }
        ```

## III. Transcription Collector (Service - `services/transcription-collector/main.py`)

1.  **Consume from Two Redis Streams:**
    *   **`transcription_segments` Stream (Existing):**
        *   Continue consuming transcription data and `session_end` / `session_start` events. This stream provides `uid`.
    *   **New `speaker_events_relative` Stream:**
        *   Define new configuration for this stream name (e.g., `REDIS_SPEAKER_EVENTS_RELATIVE_STREAM_NAME`) and consumer group.
        *   Implement a new asynchronous background task to consume messages. This stream also provides `uid`.

2.  **Buffer Data by `uid`:**
    *   Maintain in-memory buffers (e.g., Python dictionaries where keys are `uid`s).
    *   Store lists of incoming transcription segments and speaker events associated with each `uid`.
    *   Manage buffer size and eviction (e.g., after a session ends and mapping is complete, or based on time).

3.  **Implement Transcription-to-Speaker Mapping Logic:**
    *   **Triggering:** Mapping can be triggered when new transcription segments arrive for a `uid`, or periodically for `uid`s with pending data.
    *   **Timestamp Correlation:**
        *   For a given `uid`:
            *   Transcription segments have `start` (float, seconds) and `end` (float, seconds) relative to WhisperLive's audio processing start for that `uid`. Convert these to milliseconds: `segment_start_ms = segment.start * 1000`.
            *   Speaker events have `relative_client_timestamp_ms` relative to Vexa Bot's audio transmission start for that same `uid`.
            *   Since both are relative to (approximately) the same "time zero" for the `uid`, they can be directly compared.
    *   **Mapping Algorithm (for each `uid`'s data):**
        *   Fetch all buffered `Transcription` segments and `SpeakerEvent` messages for the current `uid`. Sort speaker events by `relative_client_timestamp_ms`.
        *   For each transcription segment `[segment_start_ms, segment_end_ms]`:
            *   Iterate through the speaker events for that `uid`.
            *   Identify which speaker (`participant_name` or `participant_id_meet`) was "active" during the segment's interval. A speaker is active if their most recent `SPEAKER_START` (before or at `segment_start_ms`) does not have a corresponding `SPEAKER_END` with `relative_client_timestamp_ms` *before* `segment_end_ms`.
            *   **Handling Missing `SPEAKER_END`:** If a speaker has an open `SPEAKER_START`, their speaking period extends until the session's end (derived from `MeetingSession.session_end_utc`) or the latest known event for that session.
        *   **Populate Speaker Information:**
            *   Update the transcription data structure with `mapped_speaker_name`, `mapped_participant_id_meet`.
            *   Set `speaker_mapping_status` to 'MAPPED', 'MULTIPLE' (if multiple speakers overlap significantly within the segment), or 'UNKNOWN' (if no speaker data aligns).
    *   This logic should be robust to slight timing discrepancies between the two event streams.

4.  **Process and Store Mapped Transcriptions in PostgreSQL:**
    *   After mapping, process the (now speaker-enriched) transcription segments.
    *   Apply existing deduplication logic.
    *   Store the final data into the `transcriptions` PostgreSQL table.
    *   **No separate `speaker_events_log` table is needed.** Speaker information is part of the `transcriptions` record.

5.  **Modify `transcriptions` PostgreSQL Table Schema (in `libs/shared-models/shared_models/models.py`):**
    *   Add new columns (if not already present from previous plan, ensure alignment):
        *   `mapped_speaker_name`: VARCHAR(255), NULL
        *   `mapped_participant_id_meet`: VARCHAR(255), NULL
        *   `speaker_mapping_status`: VARCHAR(50), NULL, DEFAULT 'PENDING' (Values: "PENDING", "MAPPED", "UNKNOWN", "MULTIPLE", "ERROR")
        *   `mapped_at`: TIMESTAMPTZ, NULL (Timestamp of when mapping was last attempted/updated)
    *   Remove any planned columns related to `speaker_events_log_id`.

6.  **Process `session_end` and `session_start` Events (from `transcription_segments` stream - largely existing):**
    *   Continue to use these to manage `MeetingSession` records, including setting `session_start_utc` and `session_end_utc`. This data helps define the boundaries for mapping and buffer cleanup.
    *   **Modify `MeetingSession` Table Schema (ensure `session_end_utc` exists):** (in `libs/shared-models/shared_models/models.py`)
        *   Add `session_end_utc`: TIMESTAMPTZ, NULL (if not already present).

## IV. General Considerations

*   **Configuration Management:** All new Redis stream names, consumer group names, etc., should be configurable.
*   **Timestamp Precision and Timezones:**
    *   Relative timestamps (`relative_client_timestamp_ms` and segment `start`/`end` times) are floats or integers representing milliseconds or seconds.
    *   Absolute timestamps (like `server_received_timestamp_iso`, `session_end_timestamp_iso`, `mapped_at`) stored in DB should be `TIMESTAMPTZ` (UTC).
*   **Error Handling and Logging:** Implement robust logging across all services, especially around timestamp generation, stream publishing/consumption, and the mapping logic.
*   **Idempotency:** Design Redis consumers for idempotency.
*   **Message Ordering:** While Redis streams generally maintain order within a single producer, the mapping logic in Transcription Collector will need to handle data arriving from two separate streams. Buffering by `uid` and then sorting events/segments by their relative timestamps before mapping is crucial.
*   **Scalability:** Consider the load on Transcription Collector, as it now handles more state and logic.
*   **Testing:** Thoroughly test the relative timestamp generation in Vexa Bot, dual stream forwarding in WhisperLive, and the mapping logic in Transcription Collector with various scenarios (late speaker events, overlapping speech, reconnections).

This revised plan aligns with the strategy of centralizing mapping in the Transcription Collector using relative timestamps from both Vexa Bot and WhisperLive, identified by a common session `uid`, and avoids storing raw speaker events separately.