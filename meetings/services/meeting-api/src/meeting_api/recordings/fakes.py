"""In-process fakes for the recordings ports — for this module's tests (drive the SAME shipped
``upload_chunk`` / ``finalize_master`` / ``build_router`` offline, no MinIO, no DB).

  * ``InMemoryStorage`` — a dict-backed ``Storage`` (key → bytes); ``list`` returns sorted keys
    under a prefix, so finalize gathers a recording's chunks deterministically.
  * ``InMemoryRecordingRepo`` — seeds meetings + sessions, holds ``meeting.data['recordings']`` in a
    dict, and serves the read/modify-write the upload flow uses.

NO production logic — they only stand in for object storage + Postgres so recordings run fully
in-process.
"""
from __future__ import annotations

from typing import Optional


class InMemoryStorage:
    """A dict-backed ``Storage`` (key → bytes)."""

    def __init__(self):
        self.blobs: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}

    async def upload(self, key: str, data: bytes, *, content_type: str) -> None:
        self.blobs[key] = data
        self.content_types[key] = content_type

    async def list(self, prefix: str) -> list[str]:
        return sorted(k for k in self.blobs if k.startswith(prefix))

    async def get(self, key: str) -> bytes:
        return self.blobs[key]

    async def exists(self, key: str) -> bool:
        return key in self.blobs


class InMemoryRecordingRepo:
    """A dict-backed ``RecordingRepo``. ``seed`` plants a meeting + its bot session."""

    def __init__(self):
        # meeting_id -> {user_id, recordings: [...]}; session_uid -> meeting_id
        self._meetings: dict[int, dict] = {}
        self._sessions: dict[str, int] = {}

    def seed(self, *, meeting_id: int, user_id: int, session_uid: str) -> None:
        self._meetings.setdefault(meeting_id, {"user_id": user_id, "recordings": []})
        self._sessions[session_uid] = meeting_id

    async def find_session(self, session_uid: str) -> Optional[dict]:
        mid = self._sessions.get(session_uid)
        return {"meeting_id": mid, "session_uid": session_uid} if mid is not None else None

    async def get_recordings(self, meeting_id: int) -> list[dict]:
        return list(self._meetings.get(meeting_id, {}).get("recordings", []))

    async def put_recordings(self, meeting_id: int, recordings: list[dict]) -> None:
        self._meetings.setdefault(meeting_id, {"user_id": None, "recordings": []})
        self._meetings[meeting_id]["recordings"] = list(recordings)

    async def owner_of(self, meeting_id: int) -> Optional[int]:
        return self._meetings.get(meeting_id, {}).get("user_id")

    async def list_meeting_recordings(self, user_id: int) -> list[dict]:
        out: list[dict] = []
        for mid, m in self._meetings.items():
            if m.get("user_id") == user_id:
                for r in m.get("recordings", []):
                    out.append({**r, "meeting_id": mid})
        return out
