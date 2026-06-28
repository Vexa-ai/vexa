"""L3 SEAM wiring: put BOTH services' `src` trees AND the gateway's own test-fakes dir on sys.path so
this one venv can import the REAL producer (`meeting_api.collector.create_app`) and the REAL consumer
(`gateway.app._run_multiplex`) in-process, and REUSE the gateway's injected fakes (FakeRedis pub/sub +
FakeAuthorizer) verbatim. Import direction is preserved: the seam imports each service; neither service
imports the seam.
"""
from __future__ import annotations

import sys
from pathlib import Path


def _repo_root() -> Path:
    # .../vexa-ei/core (the dir that holds gateway/ and meetings/)
    rel = Path("gateway") / "services" / "gateway" / "src"
    for parent in Path(__file__).resolve().parents:
        if (parent / rel).is_dir():
            return parent
    raise FileNotFoundError("monorepo `core` root (with gateway/services/gateway/src) not found")


_ROOT = _repo_root()
_PATHS = [
    _ROOT / "gateway" / "services" / "gateway" / "src",
    _ROOT / "gateway" / "services" / "gateway" / "tests",   # FakeRedis / FakeAuthorizer
    _ROOT / "meetings" / "services" / "meeting-api" / "src",
]
for p in _PATHS:
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)
