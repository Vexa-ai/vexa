import os

# WebSocket server
WS_HOST = os.environ.get("TRANSCRIPTION_GATEWAY_WS_HOST", "0.0.0.0")
WS_PORT = int(os.environ.get("TRANSCRIPTION_GATEWAY_WS_PORT", "9090"))

# Redis (same stream as transcription-collector consumes)
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
REDIS_STREAM_NAME = os.environ.get("REDIS_STREAM_NAME", "transcription_segments")
REDIS_SPEAKER_EVENTS_STREAM_NAME = os.environ.get(
    "REDIS_SPEAKER_EVENTS_STREAM_NAME", "speaker_events_relative"
)

# AWS Transcribe
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
DEFAULT_LANGUAGE = os.environ.get("DEFAULT_LANGUAGE", "en-US")

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
