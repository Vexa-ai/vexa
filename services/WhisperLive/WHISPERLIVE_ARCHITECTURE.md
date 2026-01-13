# WhisperLive (Vexa) — Architecture & Algorithm (Deep Dive)

This document explains the **current WhisperLive architecture and end-to-end algorithm** as implemented in this repo (CPU/GPU/Remote backends), with special focus on the **remote backend** (`whisperlive-remote`) and the **LIFO + Algorithm A** buffer management we implemented for stable “real-time” transcript updates.

Scope:
- Code lives primarily in `services/WhisperLive/whisper_live/server.py` and `services/WhisperLive/whisper_live/settings.py`.
- The process entrypoint is `services/WhisperLive/run_server.py`.
- Remote transcription is performed via `services/WhisperLive/whisper_live/remote_transcriber.py`.
- Real-time output is delivered to:
  - The **client WebSocket** (bot-manager / meeting client),
  - And (optionally) the **Transcription Collector** via a **Redis stream**.

---

## 1) High-level architecture

### Components

1) **WhisperLive WebSocket server**
- Implemented by `TranscriptionServer` in `whisper_live/server.py`.
- Accepts WebSocket connections and receives:
  - An initial JSON “options” message (connection parameters),
  - Then a stream of binary audio frames + JSON control messages.

2) **Per-connection “ServeClient” handler**
- One instance per WebSocket connection, created by `TranscriptionServer.initialize_client()`.
- There are 3 handler types:
  - `ServeClientTensorRT` (GPU TensorRT backend),
  - `ServeClientFasterWhisper` (local faster-whisper backend),
  - `ServeClientRemote` (remote HTTP backend; used by `whisperlive-remote`).

3) **Audio buffer + offsets**
- Each handler inherits `ServeClientBase`, which manages:
  - `frames_np`: the accumulated PCM samples (numpy array),
  - `frames_offset`: “how many seconds we dropped from the front” due to buffer trimming,
  - `timestamp_offset`: “how far we have *confirmed/cut* the audio”.

4) **Remote HTTP transcriber** (remote backend only)
- `ServeClientRemote` constructs a `RemoteTranscriber` (`whisper_live/remote_transcriber.py`).
- WhisperLive sends audio (WAV bytes) to `TRANSCRIBER_URL` / `REMOTE_TRANSCRIBER_URL`.
- Responses are converted into Whisper-like `Segment` objects.

5) **TranscriptionCollectorClient** (optional)
- Built into `TranscriptionServer.__init__()` if `REDIS_STREAM_URL` is set.
- Publishes transcription payloads to a Redis stream (default key: `transcription_segments`).
- This is how WhisperLive “feeds” downstream storage/aggregation services.

---

## 2) Process / threading model

WhisperLive uses **two concurrent loops per connection**:

### A) The WebSocket receive loop (server-side)
Location: `TranscriptionServer.recv_audio()`.

Responsibilities:
- Performs the connection handshake via `handle_new_connection()`.
- Repeatedly calls `process_audio_frames()`:
  - `get_audio_from_websocket()` reads the next message from the socket.
  - If it’s audio → convert to numpy frames → call `client.add_frames(frame_np)`.
  - If it’s a JSON control message → handle it and return `None`.
  - If it’s `END_OF_AUDIO` → return `False` and terminate the loop.

### B) The transcription loop (per-client background thread)
Each ServeClient starts a background thread:

- TensorRT: `ServeClientTensorRT.__init__()` starts `self.trans_thread = Thread(target=self.speech_to_text)`.
- FasterWhisper: `ServeClientFasterWhisper.__init__()` starts the same.
- Remote: `ServeClientRemote.__init__()` starts the same.

This background thread:
- Watches the shared audio buffer (`frames_np`).
- Decides when to transcribe.
- Calls `transcribe_audio(...)`.
- Converts results into “segments” and pushes them downstream (WS + Redis stream).

Important concurrency note:
- `frames_np`, `frames_offset`, `timestamp_offset` are protected by `self.lock` in `ServeClientBase`.
- Remote backend also has `self.transcription_lock` for “one request in-flight” and coalescing logic.

---

## 3) Connection handshake & metadata

### Required fields (remote backend, enforced server-side)
During `handle_new_connection()`, WhisperLive expects a JSON message with:
- `uid`
- `platform`
- `meeting_url`
- `token`
- `meeting_id`

If any are missing, WhisperLive sends an ERROR payload and closes the socket.

### Why these fields matter
They are used for:
- Tagging each segment stream with meeting/session identity.
- Publishing into Redis stream (`TranscriptionCollectorClient.send_transcription(...)`).
- Enforcing correctness (no “unknown” placeholder behavior).

---

## 4) Audio ingestion & buffer mechanics

### 4.1 Incoming message types
After handshake, the socket delivers:

1) **Binary audio frames**
- These become numpy arrays and are appended via `ServeClientBase.add_frames(frame_np)`.

2) **Control JSON messages**
- `get_audio_from_websocket()` detects JSON and routes:
  - speaker activity updates,
  - audio chunk metadata,
  - other control payloads.

3) **END_OF_AUDIO sentinel**
- Literal `b"END_OF_AUDIO"`, used as a stop signal.

### 4.2 Buffer growth, trimming, and offsets
Buffer variables:
- `frames_np`: the full retained buffer samples (post-trim).
- `frames_offset`: time offset (seconds) representing discarded audio from the front.
- `timestamp_offset`: time offset (seconds) representing how much audio is “confirmed / cut”.

When the buffer is too big (`frames_np.shape[0] > max_buffer_s * RATE`):
- We drop `discard_buffer_s` from the front:
  - `frames_offset += discard_buffer_s`
  - `frames_np = frames_np[discard_buffer_s * RATE:]`
- If trimming would place `timestamp_offset` behind `frames_offset`, we clamp:
  - `timestamp_offset = frames_offset`

### 4.3 “What audio do we send to the model?”
All backends ultimately call:
- `get_audio_chunk_for_processing()`

This returns:
- `input_bytes = frames_np[int((timestamp_offset - frames_offset)*RATE):]`
- `duration = len(input_bytes)/RATE`

Meaning:
- The system always transcribes **from the current “cut point”** (`timestamp_offset`) to the end of the retained buffer.
- When `timestamp_offset` advances, the next transcription excludes already-confirmed audio.

### 4.4 Forced clipping when “no valid segment for too long”
To prevent unbounded “stuck” buffers, `clip_audio_if_no_valid_segment()` forces:
- If the unconfirmed tail exceeds `clip_if_no_segment_s`, then:
  - `timestamp_offset = frames_offset + total_duration - clip_retain_s`

This is a safety valve against runaway buffers.

---

## 5) Output channels (WS + Redis stream)

### 5.1 WebSocket output format
The server sends to the connected client:

```json
{
  "uid": "<session uid>",
  "segments": [ ... ]
}
```

Where each segment is shaped like:
- `start` (string formatted to 3 decimals),
- `end` (string formatted to 3 decimals),
- `text`,
- `completed` (bool),
- optionally `language`.

### 5.2 Redis stream publishing (TranscriptionCollectorClient)
If `REDIS_STREAM_URL` is set:
- Every `send_transcription_to_client(segments)` also calls:
  - `collector_client.send_transcription(token, platform, meeting_id, segments, session_uid)`
- The collector client:
  - wraps a payload `{type:"transcription", token, platform, meeting_id, uid, segments}`
  - writes to Redis stream via `XADD`.
  - includes a per-session **payload digest** to avoid publishing identical payloads repeatedly.

This dual-send is intentional: websocket drives “live UI”, stream drives “durable / aggregated” transcript pipelines.

---

## 6) Remote backend: the core algorithm (LIFO + Algorithm A)

Remote backend (`ServeClientRemote`) is where most of the “real-time correctness” complexity lives.

### 6.1 Goals
We want:
- **No internal queue of requests** (no “process in sending order”).
- Always transcribe the **latest** available audio state.
- Maintain a **reconfirmation window** (don’t aggressively cut on every partial update).
- Cut/advance only when there is high confidence:
  - VAD silence (cut),
  - SAME_OUTPUT_THRESHOLD reconfirmation (cut),
  - Completed segments returned (optional cut).

### 6.2 Key configuration knobs (remote backend)
From `whisper_live/settings.py` and server options:

- `MIN_AUDIO_S`:
  - minimum buffered audio required before we attempt transcription.

- `SAME_OUTPUT_THRESHOLD`:
  - number of repeated identical partial outputs required to “commit” it as completed and advance the buffer.

- `MIN_TIME_BETWEEN_REQUESTS_S`:
  - minimum wall-clock spacing between remote transcription calls per connection.
  - This is a “safety / capacity” limiter.

Important deployment note:
- `settings.py` is **copied into the Docker image at build time** (`Dockerfile.cpu` does `COPY services/WhisperLive/ /app/`).
- Therefore **changing `settings.py` requires rebuilding** the `whisperlive-remote` image (restart alone is not enough).

### 6.3 LIFO + one-in-flight rule
We enforce:
- Only **one remote request in-flight** per connection.

Mechanism:
- `self.transcription_lock` protects `self.transcription_in_flight`.

Behavior:
- If a request is in-flight, the transcription thread waits (polling/sleep) until it completes.
- There is no request queue; audio keeps accumulating in `frames_np`.

### 6.4 Rate limiting and “latest audio after waiting”
Rate limiting must not break LIFO.

Correct behavior:
- If we must wait due to `MIN_TIME_BETWEEN_REQUESTS_S`, we should **sleep first**, then **re-fetch the latest audio chunk** and send *that* to the remote API.

Why:
- Audio continues accumulating during the sleep.
- If you keep using the pre-sleep chunk, you are not truly LIFO.

### 6.5 Algorithm A (buffer advancement / cutting rules)
Algorithm A policy (remote backend):

We only advance `timestamp_offset` when:

1) **VAD says silence**:
   - Remote returns `None` or empty segments (or language not available when not provided),
   - We treat it as “no speech activity” and **cut** the buffer by the duration we attempted:
     - `timestamp_offset += current_duration`

2) **Completed segments are returned**:
   - `update_segments()` appends completed segments,
   - sets `offset = min(duration, s.end)` for the last completed segment,
   - then advances:
     - `timestamp_offset += offset`

3) **SAME_OUTPUT_THRESHOLD reconfirms the partial**:
   - If the partial output repeats identically `> SAME_OUTPUT_THRESHOLD` times,
   - we “promote” it to a completed segment and advance by `end_time_for_same_output` (or duration):
     - `timestamp_offset += offset`

If none of the above are true:
- `offset` remains `None`,
- **we do not advance**,
- the buffer continues to accumulate and we re-transcribe a larger window next time.

This preserves the reconfirmation window behavior and prevents “eating” audio too early.

---

## 7) Remote backend: segment lifecycle (completed vs partial)

### 7.1 update_segments(): how remote results become transcript state
Remote returns an iterable/list of segments.

We treat:
- `segments[:-1]` as **completed** (if they pass `no_speech_thresh`),
- `segments[-1]` as **partial / unstable**.

For completed segments:
- Append to `self.transcript` with:
  - `start = timestamp_offset + s.start`
  - `end = timestamp_offset + min(duration, s.end)`
- Set `offset = min(duration, s.end)` (advance candidate)

For the last partial:
- Create `last_segment = format_segment(...)` with `completed=False`
- Maintain `current_out`, `prev_out`, and `same_output_count`

If partial repeats:
- Increment `same_output_count`
- On first repeat, record `end_time_for_same_output`

When threshold is exceeded:
- Promote partial to a completed segment:
  - start ~ `timestamp_offset`
  - end ~ `timestamp_offset + min(duration, end_time_for_same_output)`
- Set `offset` to advance the buffer.

### 7.2 Hallucination filtering
Before adding text to transcript:
- `_filter_hallucinations(text)` may return `None` to omit known hallucination strings.

Important nuance:
- Circuit-breaker timestamp updates happen **before filtering** so “activity” can still be observed even if text is filtered.

---

## 8) Remote backend: delta publishing (avoid “window spam”)

### Problem
Sending the full “last-N segments window” every loop causes:
- downstream spam,
- apparent “queueing”/re-processing behavior in the UI,
- hard-to-reason about “updates vs duplicates”.

### Solution (remote backend)
`ServeClientRemote.handle_transcription_output()` sends **only deltas**:

1) **New completed segments since last send**
- Track `self._last_sent_completed_idx`
- Only send `transcript[_last_sent_completed_idx:]`

2) **The latest partial segment (if changed)**
- Track `self._last_sent_partial_fingerprint = (start, end, text, completed=False)`
- Only send a partial if fingerprint differs from last send.

This ensures:
- partial segments can update in-place (same absolute start),
- completed segments are appended once,
- UI/store can properly upsert rather than duplicate.

---

## 9) Full remote-backend loop (step-by-step)

This is the conceptual order of operations in `ServeClientRemote.speech_to_text()`:

1) If exiting → stop.
2) If no audio yet (`frames_np is None`) → sleep.
3) Acquire `transcription_lock`:
   - If request in-flight → wait and retry.
   - Ensure buffer isn’t stuck (`clip_audio_if_no_valid_segment()`).
   - Ensure enough audio exists (`duration >= MIN_AUDIO_S`).
   - Mark request as in-flight.
4) Apply rate limit:
   - If not enough time passed since last request completion → sleep remaining time.
   - **After waiting**, re-fetch latest audio from buffer (LIFO correctness).
5) Call remote API (`transcribe_audio(current_chunk)`).
6) Update `last_transcription_time` when request completes.
7) Apply Algorithm A:
   - If silence → `timestamp_offset += current_duration`
   - Else → `handle_transcription_output(result, current_duration)`
8) After response:
   - Clip if needed.
   - Re-check if there is more audio available.
   - Clear in-flight flag and loop.

---

## 10) Operational / deployment notes

### 10.1 Rebuild vs restart when changing `settings.py`
Because code is copied into the image:
- Restarting a container will NOT pick up changes to `settings.py`.
- You must rebuild the image used by `whisperlive-remote`.

Recommended commands:
- rebuild: `docker-compose --profile remote build whisperlive-remote`
- recreate: `docker-compose --profile remote up -d --force-recreate whisperlive-remote`

### 10.2 Environment variables vs settings.py
- `MIN_AUDIO_S` and `SAME_OUTPUT_THRESHOLD` are provided via docker-compose env and passed into `server_options`.
- `MIN_TIME_BETWEEN_REQUESTS_S` currently comes from `settings.py` fallback (unless explicitly plumbed via server options).

---

## 11) Observability: what to look at in logs

Useful log lines:
- Connection handshake:
  - `Received raw message from client: ...`
  - `Connection parameters received: uid=..., platform=..., meeting_id=...`
- Remote client config:
  - `Remote client ...: min_audio_s=...`
  - `Remote client ...: same_output_threshold=...`
  - `Remote client ...: min_time_between_requests=...`
- Rate limiting:
  - `RATE_LIMIT: Waiting X.XXXs ...`
- LIFO correctness:
  - `LIFO: After rate limit wait, processing latest audio chunk (duration=...)`
- Segment updates:
  - `SEGMENT_UPDATE: ... partial_segment=...`
  - `SAME_OUTPUT_THRESHOLD: ... completed_segment=...`

---

## 12) Backends summary (TensorRT / FasterWhisper / Remote)

All backends share:
- WebSocket server + audio buffer + offsets.

Differences:
- **TensorRT**:
  - Uses a local GPU transcriber and may use extra VAD gating before adding frames to buffer.
- **FasterWhisper**:
  - Runs local CPU/GPU inference (depending on availability).
  - Historically sent “window outputs”; remote backend now uses deltas.
- **Remote**:
  - Delegates decoding to external HTTP API.
  - Focuses heavily on buffer stability, LIFO coalescing, and not spamming downstream.

---

## 13) Quick glossary

- **frames_np**: accumulated raw audio samples (float PCM) for the connection.
- **frames_offset**: how many seconds were trimmed off the front of `frames_np`.
- **timestamp_offset**: how many seconds we consider “confirmed/cut” and no longer need to transcribe.
- **duration**: duration of the *current transcribed chunk* (unconfirmed tail).
- **partial segment**: last segment of a transcription response; considered unstable.
- **completed segment**: a segment we consider stable and append permanently.
- **LIFO**: always process the newest buffer state, not “in-order queued chunks”.

