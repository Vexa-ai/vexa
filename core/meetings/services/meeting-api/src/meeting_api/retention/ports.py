"""Injected boundaries for owner-scoped Minutes erasure."""
from __future__ import annotations

from contextlib import AbstractAsyncContextManager
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
    recording_objects: int | None


class RetentionRepo(Protocol):
    async def begin_erasure(self, user_id: str, meeting_id: str) -> ErasurePlan | None:
        """Owner-check, block new writes, drain in-flight writes, then return the stable plan."""

    async def record_object_census(
        self, plan: ErasurePlan, recording_objects: int
    ) -> ErasurePlan:
        """Persist the pre-delete object count once so retries return a stable receipt."""

    async def commit_erasure(self, plan: ErasurePlan) -> dict[str, int]:
        """Delete meeting-owned database content and return actual non-content row counts."""


class RetentionStorage(Protocol):
    async def count_prefix(self, prefix: str) -> int:
        """Count every current object under one validated prefix without returning its keys."""

    async def delete_prefix(self, prefix: str) -> int:
        """Delete every object under one validated prefix; return the number removed."""


class MeetingWriteGate(Protocol):
    def recording_write(self, meeting_id: str) -> AbstractAsyncContextManager[None]:
        """Hold a shared lease for the entire object + database recording mutation."""
