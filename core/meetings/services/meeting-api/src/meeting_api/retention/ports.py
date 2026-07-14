"""Injected boundaries for owner-scoped Minutes erasure."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ErasurePlan:
    """Non-content identifiers and counts required to erase one owned meeting."""

    user_id: str
    meeting_id: str
    transcript_rows: int
    summary_documents: int
    recording_keys: tuple[str, ...]


class RetentionRepo(Protocol):
    async def plan_erasure(self, user_id: str, meeting_id: str) -> ErasurePlan | None:
        """Return an owner-checked plan, or None without revealing why it is unavailable."""

    async def commit_erasure(self, plan: ErasurePlan) -> dict[str, int]:
        """Delete meeting-owned database content and return actual non-content row counts."""


class RetentionStorage(Protocol):
    async def delete(self, key: str) -> bool:
        """Delete one object idempotently; return whether an object existed."""
