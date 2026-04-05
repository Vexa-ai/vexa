"""conftest.py -- pytest path setup for tts-service unit tests."""
import sys
import os

SERVICE_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, SERVICE_ROOT)
