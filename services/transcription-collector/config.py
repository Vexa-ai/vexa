import os

# Configuration for Redis Stream consumer
REDIS_STREAM_NAME = os.environ.get("REDIS_STREAM_NAME", "transcription_segments")
REDIS_CONSUMER_GROUP = os.environ.get("REDIS_CONSUMER_GROUP", "collector_group")
REDIS_STREAM_READ_COUNT = int(os.environ.get("REDIS_STREAM_READ_COUNT", "10"))
REDIS_STREAM_BLOCK_MS = int(os.environ.get("REDIS_STREAM_BLOCK_MS", "2000"))  # 2 seconds
# Use a fixed consumer name, potentially add hostname later if scaling replicas
CONSUMER_NAME = os.environ.get("POD_NAME", "collector-main")  # Get POD_NAME from env if avail (k8s), else fixed
PENDING_MSG_TIMEOUT_MS = 60000  # Milliseconds: Timeout after which pending messages are considered stale (e.g., 1 minute)

# Configuration for Speaker Events Stream (NEW)
REDIS_SPEAKER_EVENTS_STREAM_NAME = os.environ.get("REDIS_SPEAKER_EVENTS_STREAM_NAME", "speaker_events_relative")
REDIS_SPEAKER_EVENTS_CONSUMER_GROUP = os.environ.get("REDIS_SPEAKER_EVENTS_CONSUMER_GROUP", "collector_speaker_group")
REDIS_SPEAKER_EVENT_KEY_PREFIX = os.environ.get("REDIS_SPEAKER_EVENT_KEY_PREFIX", "speaker_events") # For sorted sets
REDIS_SPEAKER_EVENT_TTL = int(os.environ.get("REDIS_SPEAKER_EVENT_TTL", "86400")) # 24 hours default TTL for speaker events sorted sets

# Configuration for background processing
BACKGROUND_TASK_INTERVAL = int(os.environ.get("BACKGROUND_TASK_INTERVAL", "10"))  # seconds
IMMUTABILITY_THRESHOLD = int(os.environ.get("IMMUTABILITY_THRESHOLD", "30"))  # seconds
REDIS_SEGMENT_TTL = int(os.environ.get("REDIS_SEGMENT_TTL", "3600"))  # 1 hour default TTL for Redis segments

# Logging configuration
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# Security - API Key auth
API_KEY_NAME = "X-API-Key"

# Redis connection details
REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", None)
