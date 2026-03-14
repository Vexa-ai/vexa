# Transcription Collector

## Why

Transcription segments arrive in Redis from multiple sources -- the bot's per-speaker pipeline and (optionally) WhisperLive for external WebSocket clients. These segments are ephemeral in Redis. Something needs to consume them, deduplicate, filter noise, and persist meaningful content to Postgres so transcripts survive restarts and are queryable via the API. The collector is that persistence layer.

## What

A background service that reads from Redis streams, filters non-informative segments, and writes finalized transcripts to PostgreSQL. It also exposes a REST API for transcript retrieval and meeting management.

| Component | Details |
|---|---|
| Redis stream consumer | Reads `transcription_segments` and `speaker_events` streams via XREADGROUP |
| Redis hash | Per-meeting segment deduplication (mutable until confirmed) |
| PostgreSQL writer | Persists finalized (immutable) segments |
| Background processor | Periodically flushes confirmed segments from Redis to Postgres |
| Filtering system | Removes non-informative segments before persistence |
| REST API | Transcript retrieval, meeting management, health checks |

### How segments arrive

Segments are pre-labeled with speaker identity by the producer:

- **Bot (primary):** Per-speaker pipeline publishes segments to Redis via XADD with speaker label, meeting ID, and platform already set. No diarization needed -- the bot has per-speaker audio tracks.
- **WhisperLive (optional):** External WebSocket clients sending mixed audio. WhisperLive publishes segments to the same Redis stream.

The collector treats all segments identically regardless of source. It reads from the stream, deduplicates, filters, and persists.

### Filtering system

Each segment passes through multiple filters before persistence:
- Minimum character length check
- Pattern matching against known non-informative patterns
- Real word counting (excluding stopwords and special symbols)
- Custom filter functions (configurable in `filter_config.py`)

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/meetings` | List meetings for a user |
| GET | `/transcripts/{platform}/{native_meeting_id}` | Fetch transcript segments |
| PATCH | `/meetings/{platform}/{native_meeting_id}` | Update meeting metadata |
| DELETE | `/meetings/{platform}/{native_meeting_id}` | Delete/anonymize a meeting |

### Dependencies

- **Redis** -- source streams (`transcription_segments`, `speaker_events`), segment dedup hashes
- **PostgreSQL** -- permanent transcript storage via shared-models ORM
- **shared-models** -- ORM models, schemas, database session factory

## How

### Run

```bash
# Via docker-compose (from repo root)
docker compose up transcription-collector

# Standalone
cd services/transcription-collector
uvicorn app.main:app --host 0.0.0.0 --port 8004
```

### Configure

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_HOST` | `redis` | Redis hostname |
| `REDIS_PORT` | `6379` | Redis port |
| `REDIS_DB` | `0` | Redis database index |
| `REDIS_STREAM_NAME` | `transcription_segments` | Stream key to consume |
| `REDIS_CONSUMER_GROUP` | `collector_group` | Consumer group name |
| `REDIS_STREAM_READ_COUNT` | `10` | Messages per XREADGROUP call |
| `REDIS_STREAM_BLOCK_MS` | `2000` | XREADGROUP block timeout |
| `REDIS_SEGMENT_TTL` | `3600` | Segment cache TTL (seconds) |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | -- | PostgreSQL connection |

### Test

```bash
# Unit tests
cd services/transcription-collector && pytest tests/ -v

# Health check (service must be running)
curl http://localhost:8004/health
```

### Customizing filters

Edit `filter_config.py` to add patterns, adjust thresholds, or register custom filter functions:

```python
ADDITIONAL_FILTER_PATTERNS = [
    r"^testing$",
]

MIN_CHARACTER_LENGTH = 3
MIN_REAL_WORDS = 1

def filter_out_repeated_characters(text):
    """Filter out strings with excessive character repetition"""
    import re
    if re.search(r'(.)\1{4,}', text):
        return False
    return True

CUSTOM_FILTERS = [filter_out_repeated_characters]
```
