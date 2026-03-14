"""conftest.py -- pytest path setup for mcp unit tests."""
import sys
import os

SERVICE_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, SERVICE_ROOT)

SHARED_MODELS = os.path.join(os.path.dirname(__file__), "..", "..", "..", "libs", "shared-models")
sys.path.insert(0, SHARED_MODELS)
