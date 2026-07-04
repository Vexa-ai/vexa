"""conftest.py -- pytest path setup for api-gateway unit tests."""
import sys
import os

# Set required env vars BEFORE importing main (it validates at import time)
os.environ.setdefault("ADMIN_API_URL", "http://admin-api:8000")
os.environ.setdefault("MEETING_API_URL", "http://meeting-api:8000")
os.environ.setdefault("TRANSCRIPTION_COLLECTOR_URL", "http://transcription-collector:8000")
os.environ.setdefault("MCP_URL", "http://mcp:18888")

# fastapi-guard: keep it installed (so the integration is exercised) but
# in-memory and with rate limiting off, so the unit suite never depends on
# Redis and guard can never throttle/block a unit-test request.
os.environ.setdefault("GUARD_ENABLED", "true")
os.environ.setdefault("GUARD_ENABLE_REDIS", "false")
os.environ.setdefault("GUARD_RATE_LIMIT_RPM", "0")

SERVICE_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, SERVICE_ROOT)

MEETING_API = os.path.join(os.path.dirname(__file__), "..", "..", "..", "packages", "meeting-api")
sys.path.insert(0, MEETING_API)

ADMIN_MODELS = os.path.join(os.path.dirname(__file__), "..", "..", "..", "libs", "admin-models")
sys.path.insert(0, ADMIN_MODELS)
