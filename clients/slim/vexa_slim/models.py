"""Data models for the slim client — plain shapes the cookbook returns, kept out of the cookbook so it
reads as pure intent."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Harvest:
    """Whatever the agent emitted during a watch window, grouped by event `type`.

    The client deliberately does NOT know which types exist or what they mean — that contract (and how
    each is rendered for the user) is owned by the agent's WORKSPACE TEMPLATE, the single source of truth.
    This stays a generic carrier, so a workspace that emits new kinds needs zero client changes.
    """
    by_type: dict = field(default_factory=dict)

    def of(self, kind: str) -> list:
        """The events of one type (e.g. whatever the workspace calls a 'card'); [] if none seen."""
        return self.by_type.get(kind, [])

    def counts(self) -> dict:
        return {k: len(v) for k, v in self.by_type.items()}

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.by_type.values())
