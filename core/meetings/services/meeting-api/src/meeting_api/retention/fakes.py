"""Offline fakes for the retention core's database and object-storage ports."""
from __future__ import annotations

import asyncio
from copy import deepcopy

from .ports import ErasurePlan


class InMemoryRetentionRepo:
    def __init__(self):
        self._meetings: dict[str, dict] = {}
        self._write_condition = asyncio.Condition()

    def seed_meeting(
        self,
        *,
        user_id: str,
        meeting_id: str,
        transcript_rows: list[str],
        summaries: list[str],
        recording_prefixes: list[str],
        recording_objects: int,
    ) -> None:
        self._meetings[meeting_id] = {
            "user_id": user_id,
            "transcript_rows": list(transcript_rows),
            "summaries": list(summaries),
            "recording_prefixes": list(recording_prefixes),
            "recording_objects": recording_objects,
            "state": "open",
            "in_flight_writes": 0,
        }

    def snapshot(self, meeting_id: str) -> dict | None:
        meeting = self._meetings.get(meeting_id)
        return deepcopy(meeting) if meeting is not None else None

    async def begin_recording_write(self, meeting_id: str) -> bool:
        async with self._write_condition:
            meeting = self._meetings.get(meeting_id)
            if meeting is None or meeting["state"] != "open":
                return False
            meeting["in_flight_writes"] += 1
            return True

    async def end_recording_write(self, meeting_id: str) -> None:
        async with self._write_condition:
            meeting = self._meetings.get(meeting_id)
            if meeting is None or meeting["in_flight_writes"] < 1:
                return
            meeting["in_flight_writes"] -= 1
            self._write_condition.notify_all()

    async def begin_erasure(self, user_id: str, meeting_id: str) -> ErasurePlan | None:
        async with self._write_condition:
            meeting = self._meetings.get(meeting_id)
            if meeting is None or meeting["user_id"] != user_id:
                return None
            meeting["state"] = "erasing"
            while meeting["in_flight_writes"]:
                await self._write_condition.wait()
            return ErasurePlan(
                user_id=user_id,
                meeting_id=meeting_id,
                transcript_rows=len(meeting["transcript_rows"]),
                summary_documents=len(meeting["summaries"]),
                recording_prefixes=tuple(meeting["recording_prefixes"]),
                recording_objects=meeting["recording_objects"],
            )

    async def commit_erasure(self, plan: ErasurePlan) -> dict[str, int]:
        async with self._write_condition:
            meeting = self._meetings.get(plan.meeting_id)
            if meeting is None or meeting["user_id"] != plan.user_id:
                return {"meeting_rows": 0, "transcript_rows": 0, "summary_documents": 0}
            deleted = {
                "meeting_rows": 1,
                "transcript_rows": len(meeting["transcript_rows"]),
                "summary_documents": len(meeting["summaries"]),
            }
            del self._meetings[plan.meeting_id]
            self._write_condition.notify_all()
            return deleted


class InMemoryRetentionStorage:
    def __init__(self):
        self._objects: dict[str, bytes] = {}

    def seed(self, key: str, value: bytes) -> None:
        self._objects[key] = bytes(value)

    def snapshot(self, prefix: str) -> dict[str, bytes]:
        return {key: value for key, value in self._objects.items() if key.startswith(prefix)}

    async def delete_prefix(self, prefix: str) -> int:
        keys = [key for key in self._objects if key.startswith(prefix)]
        for key in keys:
            del self._objects[key]
        return len(keys)
