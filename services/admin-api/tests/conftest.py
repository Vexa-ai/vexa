"""conftest.py – make shared_models and the service root importable."""

import sys
from pathlib import Path

# Add libs/shared-models so `import shared_models` works
_repo = Path(__file__).resolve().parents[3]  # <repo>/services/admin-api/tests -> <repo>
sys.path.insert(0, str(_repo / "libs" / "shared-models"))

# Add the service root so `import app` works
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
