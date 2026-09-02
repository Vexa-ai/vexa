import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(autouse=True)
def _admin_key_present(monkeypatch):
    """A test process has an admin identity, the same way a deployment does.

    `VEXA_FLOWS_ADMIN_KEY` lost its `changeme` default (R-B11): it opens `ensure_platform_user`
    and `user_api_key`, so an unset value now REFUSES rather than trying a placeholder. That is
    the right behaviour for a deployment and it makes every offline test that touches a step
    raise on the refusal instead of on the thing it is testing — so the suite declares a key,
    exactly as the lane's start script does, and the tests that are ABOUT the refusal unset it
    themselves (`test_admin_key_refusal.py`).

    `setdefault` semantics on purpose: a run that already exports a real key — the live contract
    smoke against a running admin-api — keeps it.
    """
    if not (os.environ.get("VEXA_FLOWS_ADMIN_KEY") or "").strip():
        monkeypatch.setenv("VEXA_FLOWS_ADMIN_KEY", "test-admin-key-not-a-placeholder")
