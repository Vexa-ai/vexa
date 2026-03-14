# Redis

## Why

Vexa has multiple services that need to communicate without tight coupling. Redis provides three distinct messaging patterns that the system depends on:

1. **Streams** -- durable, ordered message delivery for transcription segments. The bot (and optionally WhisperLive) produces segments, transcription-collector consumes them. If the collector is slow or restarts, the stream retains messages until they're consumed. This is why transcripts don't get lost.

2. **Pub/Sub** — real-time event broadcasting for meeting status changes and WebSocket multiplexing. When a bot's status changes (joining → active → completed), all interested clients hear about it instantly. This is how the dashboard shows live meeting status.

3. **Queue** — durable webhook retry. Failed webhook deliveries are pushed to a Redis list with backoff metadata. A retry worker polls the list and re-delivers. This is why webhooks eventually arrive even if the customer's endpoint is temporarily down.

## What

Redis 7 (Alpine), single instance, no persistence configuration (data is ephemeral by design — the source of truth for transcripts is Postgres).

### Data flows

```
Bot (per-speaker) ──XADD──► Redis Stream (transcription_segments) ──XREADGROUP──► transcription-collector ──► Postgres
Bot (per-speaker) ──XADD──► Redis Stream (speaker_events)         ──XREADGROUP──► transcription-collector
Bot (per-speaker) ──PUBLISH──► Redis Pub/Sub (meeting:{id}:segments) ──SUBSCRIBE──► api-gateway ──► WebSocket (dashboard)

WhisperLive (optional, external clients) ──XADD──► Redis Stream (transcription_segments) ──► same consumer

bot-manager ──PUBLISH──► Redis Pub/Sub (meeting:{id}:status)  ──SUBSCRIBE──► api-gateway ──► WebSocket clients
bot-manager ──PUBLISH──► Redis Pub/Sub (bot_commands:meeting:{id}) ──SUBSCRIBE──► vexa-bot

webhook_delivery ──LPUSH──► Redis List (webhook_retry_queue) ──BRPOP──► retry_worker ──► customer endpoint
```

### Keys and channels

| Pattern | Type | Producer | Consumer | Purpose |
|---------|------|----------|----------|---------|
| `transcription_segments` | Stream | Bot, WhisperLive (optional) | transcription-collector | Transcript segments with text, timestamps, speaker info |
| `speaker_events` | Stream | Bot | transcription-collector | Speaker activity events (join, leave, speaking) |
| `meeting:{meeting_id}:segments` | Pub/Sub | Bot | api-gateway -> WebSocket | Real-time segment delivery to dashboard |
| `meeting:{meeting_id}:status` | Pub/Sub | bot-manager | api-gateway -> WebSocket | Meeting status changes (joining, active, completed, failed) |
| `bot_commands:meeting:{meeting_id}` | Pub/Sub | bot-manager | vexa-bot | Bot control commands (leave, reconfigure) |
| `webhook_retry_queue` | List | webhook_delivery (shared-models) | retry_worker (bot-manager) | Failed webhook deliveries with backoff metadata |

### Stream details

**transcription_segments stream:**
```
XADD transcription_segments * \
  token <api_token> \
  session_uid <uuid> \
  meeting_id <meeting_id> \
  platform <google_meet|teams|zoom> \
  type <segment|session_start|session_end> \
  text "Hello, this is the transcript" \
  start 0.0 \
  end 2.5 \
  speaker <speaker_id>
```

Consumer group: `collector_group`. The collector reads with `XREADGROUP`, acknowledges with `XACK`, and persists to Postgres.

**Pub/Sub meeting status:**
```json
{
  "meeting_id": 123,
  "status": "active",
  "platform": "google_meet",
  "native_meeting_id": "abc-defg-hij",
  "user_id": 42
}
```

### Configuration

| Env var | Service | Default | Purpose |
|---------|---------|---------|---------|
| `REDIS_URL` | bot-manager, api-gateway | `redis://redis:6379/0` | Connection URL |
| `REDIS_HOST` | WhisperLive, transcription-collector | `redis` | Hostname |
| `REDIS_PORT` | WhisperLive, transcription-collector | `6379` | Port |
| `REDIS_DB` | WhisperLive, transcription-collector | `0` | Database index |
| `REDIS_URL` | vexa-bot | `redis://redis:6379/0` | Bot Redis connection |
| `REDIS_STREAM_URL` | WhisperLive | `redis://redis:6379/0/transcription_segments` | Stream connection URL |
| `REDIS_STREAM_NAME` | WhisperLive, transcription-collector | `transcription_segments` | Stream key |
| `REDIS_CONSUMER_GROUP` | transcription-collector | `collector_group` | Consumer group name |
| `REDIS_STREAM_READ_COUNT` | transcription-collector | `10` | Messages per XREADGROUP call |
| `REDIS_STREAM_BLOCK_MS` | transcription-collector | `2000` | XREADGROUP block timeout |
| `REDIS_SEGMENT_TTL` | transcription-collector | `3600` | Segment cache TTL (seconds) |

### References

- Stream producer (bot): [`services/vexa-bot/core/src/services/segment-publisher.ts`](vexa-bot/core/src/services/segment-publisher.ts) -- XADD + PUBLISH per segment
- Stream producer (WhisperLive, optional): [`services/WhisperLive/whisper_live/server.py`](WhisperLive/whisper_live/server.py) -- `TranscriptionCollectorClient.xadd()`
- Stream consumer: [`services/transcription-collector/streaming/consumer.py`](transcription-collector/streaming/consumer.py) -- `XREADGROUP` loop
- Pub/Sub producer: [`services/bot-manager/app/main.py`](bot-manager/app/main.py) -- `publish_meeting_status_change()`
- Pub/Sub consumer: [`services/api-gateway/main.py`](api-gateway/main.py) -- `websocket_multiplex()` subscribes for real-time updates
- Webhook retry: [`libs/shared-models/shared_models/webhook_retry_worker.py`](../libs/shared-models/shared_models/webhook_retry_worker.py) -- `BRPOP` loop with backoff
- Webhook enqueue: [`libs/shared-models/shared_models/webhook_delivery.py`](../libs/shared-models/shared_models/webhook_delivery.py) -- `LPUSH` on failure

## How

Redis runs as a Docker Compose service. No special configuration needed.

```yaml
# docker-compose.yml
redis:
  image: redis:7-alpine
  expose:
    - "6379"
```

No persistence, no auth, no cluster. If Redis restarts, in-flight stream messages are lost but:
- WhisperLive re-publishes on the next audio chunk
- Webhook retry queue is rebuilt from the next failure
- Pub/Sub is inherently ephemeral

For production, consider enabling `appendonly yes` for stream durability.
