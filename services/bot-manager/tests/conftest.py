"""conftest.py – make the service root importable so `import app` works."""

import sys
from pathlib import Path

# Add the service root (services/bot-manager) to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
