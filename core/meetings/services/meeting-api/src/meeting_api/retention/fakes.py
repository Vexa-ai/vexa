"""Offline fakes for the retention core's database and object-storage ports."""
from __future__ import annotations

from copy import deepcopy

from .ports import ErasurePlan


class InMemoryRetentionRepo:
    def __init__(self):
        self._meetings: dict[str, dict] = {}

    def seed_meeting(
        self,
        *,
        user_id: str,
        meeting_id: str,
        transcript_rows: list[str],
        summaries: list[str],
        recording_keys: list[str],
    ) -> None:
        self._meetings[meeting_id] = {
            "user_id": user_id,
            "transcript_rows": list(transcript_rows),
            "summaries": list(summaries),
            "recording_keys": list(recording_keys),
        }

    def recording_keys(self) -> list[str]:
        return [key for meeting in self._meetings.values() for key in meeting["recording_keys"]]

    def snapshot(self, meeting_id: str) -> dict | None:
        meeting = self._meetings.get(meeting_id)
        return deepcopy(meeting) if meeting is not None else None

    async def plan_erasure(self, user_id: str, meeting_id: str) -> ErasurePlan | None:
        meeting = self._meetings.get(meeting_id)
        if meeting is None or meeting["user_id"] != user_id:
            return None
        return ErasurePlan(
            user_id=user_id,
            meeting_id=meeting_id,
            transcript_rows=len(meeting["transcript_rows"]),
            summary_documents=len(meeting["summaries"]),
            recording_keys=tuple(meeting["recording_keys"]),
        )

    async def commit_erasure(self, plan: ErasurePlan) -> dict[str, int]:
        meeting = self._meetings.get(plan.meeting_id)
        if meeting is None or meeting["user_id"] != plan.user_id:
            return {"meeting_rows": 0, "transcript_rows": 0, "summary_documents": 0}
        deleted = {
            "meeting_rows": 1,
            "transcript_rows": len(meeting["transcript_rows"]),
            "summary_documents": len(meeting["summaries"]),
        }
        del self._meetings[plan.meeting_id]
        return deleted


class InMemoryRetentionStorage:
    def __init__(self):
        self._objects: dict[str, bytes] = {}

    def seed(self, key: str, value: bytes) -> None:
        self._objects[key] = bytes(value)

    def snapshot(self, prefix: str) -> dict[str, bytes]:
        return {key: value for key, value in self._objects.items() if key.startswith(prefix)}

    async def delete(self, key: str) -> bool:
        return self._objects.pop(key, None) is not None
