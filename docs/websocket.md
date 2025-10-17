# Vexa WebSocket Usage

## Overview

WebSocket connections provide efficient, low-latency transcript updates compared to polling REST endpoints. Since REST transcript retrieval is not suitable for frequent polling due to server API efficiency concerns, WebSocket subscriptions offer real-time updates without the overhead of repeated HTTP requests.

This document describes how to connect to Vexa's WebSocket API for real-time meeting transcription. The protocol supports subscribing to active meetings and receiving live transcript updates with proper deduplication and speaker grouping.

**Implementation Reference**: The `testing/ws_realtime_transcription.py` script serves as a complete Python implementation of real-time transcript rendering using this WebSocket protocol. It demonstrates the full algorithm from REST bootstrap through WebSocket updates with proper deduplication, speaker grouping, and live terminal rendering.

**Prerequisites**: The meeting bot must already be running and active for the target meeting.

### Starting a Bot (if not already running)

To start a transcription bot for a meeting:

```bash
POST /bots
Headers: X-API-Key: YOUR_API_KEY
Body: {
  "platform": "google_meet",
  "native_meeting_id": "your-meeting-id"
}

# For Microsoft Teams (requires passcode):
Body: {
  "platform": "teams",
  "native_meeting_id": "9387167464734",
  "passcode": "qxJanYOcdjN4d6UlGa"
}
```



## Connection Details

### WebSocket URL

Derive the WebSocket URL from your API base URL:
- `https://api.example.com` → `wss://api.example.com/ws`
- `http://localhost:18056` → `ws://localhost:18056/ws`

### Authentication

Authentication is performed using the `X-API-Key` header:

```
Headers: X-API-Key: YOUR_API_KEY
```

### Meeting Identity

Meetings are identified by platform and native meeting ID:

```json
{
  "platform": "google_meet",
  "native_id": "kzj-grsa-cqf"
}
```

Supported platforms: `google_meet`, `teams`

## REST API Bootstrap

Before connecting to WebSocket, fetch the last full  transcript via REST API:

```
GET /transcripts/{platform}/{native_id}[?meeting_id=...]
Headers: X-API-Key: YOUR_API_KEY
```

**Response Format**:
```json
{
  "segments": [
    {
      "text": "Hello everyone",
      "speaker": "John",
      "absolute_start_time": "2025-01-15T10:30:00Z",
      "absolute_end_time": "2025-01-15T10:30:03Z"
    }
  ]
}
```

## WebSocket Protocol

### Subscription

Send subscription message after connecting:

```json
{
  "action": "subscribe",
  "meetings": [
    {
      "platform": "google_meet",
      "native_id": "kzj-grsa-cqf"
    }
  ]
}
```

**Fields**:
- `action`: Always `"subscribe"`
- `meetings`: Array of meeting objects with `platform` and `native_id`

### Message Types

#### `transcript.mutable`
Live transcript segments that may be updated.

```json
{
  "type": "transcript.mutable",
  "meeting": {"id": 12345},
  "payload": {
    "segments": [
      {
        "text": "This text may change",
        "speaker": "John",
        "language": "en",
        "session_uid": "abc123-456-def",
        "speaker_mapping_status": "NO_SPEAKER_EVENTS",
        "start": 1234.567,
        "end_time": 1237.890,
        "absolute_start_time": "2025-01-15T10:30:05Z",
        "absolute_end_time": "2025-01-15T10:30:08Z",
        "updated_at": "2025-01-15T10:30:08Z"
      }
    ]
  },
  "ts": "2025-01-15T10:30:08Z"
}
```

**Note**: Additional fields like `session_uid`, `speaker_mapping_status`, and relative timing (`start`, `end_time`) may be present but are not required for basic transcript processing.

#### `transcript.finalized`
**DEPRECATED**: No longer emitted. `transcript.finalized` messages are not used by clients. Only `transcript.mutable` messages are processed for live transcript updates. Use the REST API endpoint to fetch the complete, stable transcript.

#### `meeting.status`
Meeting status updates.

```json
{
  "type": "meeting.status",
  "meeting": {"platform": "google_meet", "native_id": "kzj-grsa-cqf"},
  "payload": {
    "status": "active"
  },
  "ts": "2025-01-15T10:30:00Z"
}
```

**Status Values**: `requested`, `joining`, `awaiting_admission`, `connecting`, `active`, `stopping`, `completed`, `failed`

#### `subscribed`
Confirmation of successful subscription.

```json
{
  "type": "subscribed",
  "meetings": [1, 2, 3]
}
```

#### `pong`
Response to ping messages.

```json
{
  "type": "pong"
}
```

#### `error`
Error messages.

```json
{
  "type": "error",
  "error": "Invalid meeting ID"
}
```

## Segment Schema

Minimum fields to consume:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `text` | string | Yes | Transcript text content |
| `speaker` | string | No | Speaker identifier |
| `language` | string | No | Language code (e.g., "en", "es") |
| `absolute_start_time` | string | Yes | UTC timestamp (ISO 8601) |
| `absolute_end_time` | string | Yes | UTC timestamp (ISO 8601) |
| `updated_at` | string | No | Last update timestamp |

## Algorithm
Implemented in `testing/ws_realtime_transcription.py`

### 1. Bootstrap

1. Fetch initial transcript via REST API
2. Seed in-memory map keyed by `absolute_start_time`
3. Ignore segments missing `absolute_start_time` for ordering

```python
transcript_by_abs_start = {}
for segment in rest_segments:
    if segment.get('absolute_start_time'):
        transcript_by_abs_start[segment['absolute_start_time']] = segment
```

### 2. WebSocket Updates

For each `transcript.mutable` message:

1. For every segment with `absolute_start_time`:
   - Upsert into map by key
   - If `updated_at` exists on both existing and incoming, keep the newer (`updated_at` max)
   - Discard segments with empty/whitespace-only `text`

```python
for segment in ws_segments:
    abs_start = segment.get('absolute_start_time')
    if not abs_start or not segment.get('text', '').strip():
        continue
    
    existing = transcript_by_abs_start.get(abs_start)
    if existing and existing.get('updated_at') and segment.get('updated_at'):
        if segment['updated_at'] < existing['updated_at']:
            continue  # Keep existing (newer)
    
    transcript_by_abs_start[abs_start] = segment
```

### 3. Rendering Order

Sort by `absolute_start_time` ascending:

```python
sorted_segments = sorted(
    transcript_by_abs_start.values(),
    key=lambda s: s['absolute_start_time']
)
```

### 4. Speaker Merging

Group consecutive segments by same speaker:

```python
def group_by_speaker(segments):
    groups = []
    current_group = None
    
    for segment in segments:
        speaker = segment.get('speaker', 'Unknown')
        if current_group and current_group['speaker'] == speaker:
            current_group['text'] += ' ' + segment['text']
            current_group['end_time'] = segment['absolute_end_time']
        else:
            if current_group:
                groups.append(current_group)
            current_group = {
                'speaker': speaker,
                'text': segment['text'],
                'start_time': segment['absolute_start_time'],
                'end_time': segment['absolute_end_time']
            }
    
    if current_group:
        groups.append(current_group)
    return groups
```

### 5. Rendering Strategy

For maximum readability, re-render the entire transcript on every update:

```python
def render_full_transcript():
    # Clear screen and move cursor to top
    print('\033[H\033[J', end='')
    
    # Render header
    print("=" * 60)
    print("📝 LIVE TRANSCRIPT")
    print("=" * 60)
    
    # Get sorted segments and group by speaker
    sorted_segments = sorted(transcript_by_abs_start.values(), key=lambda s: s['absolute_start_time'])
    groups = group_by_speaker(sorted_segments)
    
    # Render all groups
    for group in groups:
        start_time = format_time(group['start_time'])
        end_time = format_time(group['end_time'])
        speaker = group['speaker']
        text = clean_text(group['text'])
        print(f"[{start_time} - {end_time}] {speaker}: {text}")
```

**ANSI Control Sequences**:
- `\033[H`: Move cursor to home position (top-left)
- `\033[J`: Clear screen from cursor to end
- `end=''`: Suppress newline for immediate effect

This ensures the terminal always shows a clean, complete transcript without duplicate or stale lines.

## Keepalive

Client may send ping messages:

```json
{
  "action": "ping"
}
```

Server responds with `pong`. Recommended ping interval: 25 seconds.

## Error Handling

- Log `error` messages but continue processing
- Handle connection drops gracefully
- Reconnect and resubscribe as needed
- Idempotent merging preserves order on reconnection

## Environment Variables

```bash
export API_BASE="http://localhost:18056"
export WS_URL="ws://localhost:18056/ws"
export API_KEY="your_api_key_here"
```

## Example Usage

See the real-time transcription script for a complete implementation:

```bash
# Basic usage
python testing/ws_realtime_transcription.py \
  --api-base http://localhost:18056 \
  --ws-url ws://localhost:18056/ws \
  --api-key $API_KEY \
  --platform google_meet \
  --native-id kzj-grsa-cqf

# Debug mode (show raw frames)
python testing/ws_realtime_transcription.py \
  --api-base http://localhost:18056 \
  --ws-url ws://localhost:18056/ws \
  --api-key $API_KEY \
  --platform google_meet \
  --native-id kzj-grsa-cqf \
  --raw
```

The real-time transcription script implements the exact algorithm described above and renders a live, grouped transcript in the terminal. It demonstrates the complete flow from REST bootstrap through WebSocket updates with proper deduplication and speaker merging.

## Complete Implementation

The real-time transcription script (`testing/ws_realtime_transcription.py`) serves as a complete reference implementation of this WebSocket protocol. It demonstrates:

1. **REST API Bootstrap**: Fetching initial transcript data
2. **WebSocket Connection**: Proper authentication and subscription
3. **Message Processing**: Handling all WebSocket event types
4. **Data Deduplication**: Merging segments by `absolute_start_time` with `updated_at` precedence
5. **Speaker Grouping**: Combining consecutive segments by speaker
6. **Live Rendering**: Full re-render strategy with ANSI escape codes
7. **Error Handling**: Graceful handling of connection issues

The script includes comprehensive comments explaining each step of the algorithm, making it a valuable reference for implementing real-time WebSocket transcription clients in other languages.

## Raw Debug Mode

Use the `--raw` flag to debug WebSocket message flow:

1. **Display raw JSON frames** in terminal with `RAW:` prefix
2. **Log all messages** to `testing/logs/ws_raw.log`

**Log file location**: `testing/logs/ws_raw.log` (single file, appends all runs)

**Example log file line:**
```
2025-10-04T14:50:35.101823 - {"type": "transcript.mutable", "meeting": {"platform": "google_meet", "native_id": "tys-tztv-nrj"}, "payload": {"segments": [...]}, "ts": "2025-10-04T11:50:35.100142+00:00"}
```

Use these logs to verify message structure, timing, and payload formats for your implementation.
