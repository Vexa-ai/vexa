"""harvest — the event-collection MECHANISM: watch a meeting for a window and group events by type.

Kept out of cookbook.py so the cookbook reads as pure intent (composed verbs). `listen_to_meeting`
composes this; the returned `Harvest` data model lives in `models.py`.
"""
from __future__ import annotations

from .client import Slim
from .models import Harvest


async def harvest(slim: Slim, native: str, *, seconds: float) -> Harvest:
    """Watch the agent for `seconds` and return everything it emitted, grouped by event `type`.
    No type is privileged or named here — the workspace template owns the vocabulary."""
    out = Harvest()

    def collect(evt: dict) -> None:
        out.by_type.setdefault(evt.get("type", "?"), []).append(evt)

    await slim.agent.watch(native, seconds=seconds, on_event=collect)
    return out
