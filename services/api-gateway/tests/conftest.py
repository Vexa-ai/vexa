"""conftest.py -- pytest path setup for api-gateway unit tests."""
import sys
import os

# Set required env vars BEFORE importing main (it validates at import time)
os.environ.setdefault("ADMIN_API_URL", "http://admin-api:8000")
os.environ.setdefault("BOT_MANAGER_URL", "http://bot-manager:8000")
os.environ.setdefault("TRANSCRIPTION_COLLECTOR_URL", "http://transcription-collector:8000")
os.environ.setdefault("MCP_URL", "http://mcp:18888")

SERVICE_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, SERVICE_ROOT)

SHARED_MODELS = os.path.join(os.path.dirname(__file__), "..", "..", "..", "libs", "shared-models")
sys.path.insert(0, SHARED_MODELS)
