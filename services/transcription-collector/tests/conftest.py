"""conftest.py -- pytest path setup for transcription-collector unit tests."""
import sys
import os

# Add service root so `import filters`, `import config`, etc. work
SERVICE_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, SERVICE_ROOT)

# Add shared-models so any shared imports resolve
SHARED_MODELS = os.path.join(os.path.dirname(__file__), "..", "..", "..", "libs", "shared-models")
sys.path.insert(0, SHARED_MODELS)
