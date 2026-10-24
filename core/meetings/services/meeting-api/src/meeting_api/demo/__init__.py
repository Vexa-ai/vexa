"""demo — a demo meeting for a person who has no meeting right now (``POST /demo-meetings``).

Front door (P6): import from here, never a deep module path.
"""
from __future__ import annotations

from .router import DEMO_SCRIPT, DEMO_SPEAKERS, build_router, demo_ended, demos_per_day

__all__ = ["DEMO_SCRIPT", "DEMO_SPEAKERS", "build_router", "demo_ended", "demos_per_day"]
