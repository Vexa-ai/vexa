"""conftest.py – make admin_models, meeting_api, and the service root importable."""

import sys
from pathlib import Path

_repo = Path(__file__).resolve().parents[3]  # <repo>/services/admin-api/tests -> <repo>
sys.path.insert(0, str(_repo / "libs" / "admin-models"))
sys.path.insert(0, str(_repo / "packages" / "meeting-api"))

# Add the service root so `import app` works
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
