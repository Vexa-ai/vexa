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
    recording_prefixes: tuple[str, ...]
    recording_objects: int


class RetentionRepo(Protocol):
    async def begin_erasure(self, user_id: str, meeting_id: str) -> ErasurePlan | None:
        """Owner-check, block new writes, drain in-flight writes, then return the stable plan."""

    async def commit_erasure(self, plan: ErasurePlan) -> dict[str, int]:
        """Delete meeting-owned database content and return actual non-content row counts."""


class RetentionStorage(Protocol):
    async def delete_prefix(self, prefix: str) -> int:
        """Delete every object under one validated prefix; return the number removed."""


class MeetingWriteGate(Protocol):
    async def begin_recording_write(self, meeting_id: str) -> bool:
        """Acquire one recording-write lease, or refuse after erasure has started."""

    async def end_recording_write(self, meeting_id: str) -> None:
        """Release a recording-write lease so an erasure waiting for quiescence can continue."""
