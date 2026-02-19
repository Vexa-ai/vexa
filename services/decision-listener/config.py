import os

# Redis connection
REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", None)

# Pub/Sub channel pattern — transcription-collector publishes here
# Channel per meeting: tc:meeting:{meeting_id}:mutable
PUBSUB_CHANNEL_PATTERN = os.environ.get("PUBSUB_CHANNEL_PATTERN", "tc:meeting:*:mutable")

# Sliding window hyperparams
WINDOW_SEGMENTS = int(os.environ.get("WINDOW_SEGMENTS", "30"))   # how many segments to feed to LLM
OFFSET_SEGMENTS = int(os.environ.get("OFFSET_SEGMENTS", "3"))    # skip last N (in-flight / unfinished)

# Debounce: minimum ms between LLM calls per meeting
DEBOUNCE_MS = int(os.environ.get("DEBOUNCE_MS", "2000"))

# LLM
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4.1-mini")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", None)        # None = default OpenAI
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# Storage
DECISIONS_KEY_PREFIX = "meeting:{meeting_id}:decisions"
DECISIONS_TTL = int(os.environ.get("DECISIONS_TTL", "7200"))     # 2 hours

# HTTP server
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8765"))

# Logging
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
