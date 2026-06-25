"""Re-export the gateway package's OWN injected test fakes (FakeRedis pub/sub hub + FakeAuthorizer) so the
seam reuses them VERBATIM. They live in gateway/services/gateway/tests/conftest.py, which can't be imported
as `conftest` here (that name is taken by this seam's own conftest.py), so we load it by file path.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load():
    rel = Path("gateway") / "services" / "gateway" / "tests" / "conftest.py"
    for parent in Path(__file__).resolve().parents:
        cand = parent / rel
        if cand.is_file():
            spec = importlib.util.spec_from_file_location("gateway_tests_conftest", cand)
            mod = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            spec.loader.exec_module(mod)
            return mod
    raise FileNotFoundError("gateway/services/gateway/tests/conftest.py not found")


_gw = _load()
FakeRedis = _gw.FakeRedis
FakeAuthorizer = _gw.FakeAuthorizer
