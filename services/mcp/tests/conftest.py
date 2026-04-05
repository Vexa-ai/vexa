"""conftest.py -- pytest path setup for mcp unit tests."""
import sys
import os

SERVICE_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, SERVICE_ROOT)

MEETING_API = os.path.join(os.path.dirname(__file__), "..", "..", "..", "packages", "meeting-api")
sys.path.insert(0, MEETING_API)
