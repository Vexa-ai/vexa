"""Ports (Protocols) for the recordings flow — chunk upload + finalize → master in
``meeting.data`` JSONB.

The parent ``recordings.internal_upload_recording`` + ``recording_finalizer`` talk to two
collaborators:

  * **object storage (MinIO/S3)** — each chunk is uploaded under a per-(recording, session, type)
    key; finalize concatenates the chunks into a master and uploads that. Expressed as a ``Storage``
    Protocol: ``upload(key, data, content_type)``, ``list(prefix)``, ``get(key)``.
  * **the meeting store** — resolve the ``MeetingSession`` by ``session_uid`` (the upload arrives
    with the bot's ``connectionId``), and read/modify-under-lock ``meeting.data['recordings']``.
    Expressed as a ``RecordingRepo`` Protocol.

Each is a ``typing.Protocol`` so the app depends on BEHAVIOR, not a concrete client. ``adapters.py``
supplies the production implementations (MinIO + SQLAlchemy); the module's tests supply in-process
fakes (an in-memory blob store + an in-memory meeting store).
"""
from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Optional, Protocol, runtime_checkable

from ..meeting_writes import MEETING_WRITE_LOCK_NAMESPACE

RECORDING_CHUNK_LOCK_NAMESPACE = 23116


class RecordingWriteRefused(RuntimeError):
    """The meeting disappeared or entered the durable erasing state before a write lease."""


@runtime_checkable
class Storage(Protocol):
    """Object storage for recording chunks + masters (MinIO/S3 in prod)."""

    async def upload(self, key: str, data: bytes, *, content_type: str) -> None: ...

    async def delete(self, key: str) -> None:
        """Idempotently remove one exact object after an upload/database compensation failure."""
        ...

    async def list(self, prefix: str) -> list[str]:
        """Object keys under ``prefix`` (sorted) — used by finalize to gather a recording's chunks."""
        ...

    async def get(self, key: str) -> bytes: ...

    async def exists(self, key: str) -> bool: ...

    async def size(self, key: str) -> int:
        """Object byte size WITHOUT fetching the body — lets the raw media route resolve
        ``Content-Range`` / a 416 for an HTTP Range without downloading the whole master."""
        ...

    async def get_range(self, key: str, start: int, end: int) -> bytes:
        """The INCLUSIVE byte slice ``[start, end]`` — S3/MinIO pass the Range through to
        ``get_object`` so seeking fetches only the requested window, not the whole object."""
        ...


@runtime_checkable
class RecordingRepo(Protocol):
    """The DB side of recordings: resolve the session, read/modify ``meeting.data['recordings']``."""

    async def find_session(self, session_uid: str) -> Optional[dict]:
        """The ``MeetingSession`` for ``session_uid`` → ``{meeting_id, session_uid}`` (the bot's
        ``connectionId``), or ``None`` when no session exists yet (upload before spawn)."""
        ...

    def recording_write(self, meeting_id: int) -> AbstractAsyncContextManager[None]:
        """Hold the shared meeting-write lease across object and database mutation.

        The context refuses entry after erasure has made the meeting non-writable.
        """
        ...

    def chunk_write(self, key: str) -> AbstractAsyncContextManager[None]:
        """Serialize one deterministic chunk key across object write, JSONB fold and compensation."""
        ...

    async def register_recording_prefix(self, meeting_id: int, prefix: str) -> None:
        """Durably register a narrow prefix before its first object write."""
        ...

    async def get_recordings(self, meeting_id: int) -> list[dict]:
        """The current ``meeting.data['recordings']`` list (under the same read the writer locks)."""
        ...

    async def put_recordings(self, meeting_id: int, recordings: list[dict]) -> None:
        """Persist the updated ``meeting.data['recordings']`` list (the row-locked write-back)."""
        ...

    async def mutate_recordings(self, meeting_id: int, mutator):
        """ATOMIC read→modify→write of ``meeting.data['recordings']`` under a SINGLE row lock (G3).
        ``mutator(recordings) -> (new_recordings, result)`` runs while the lock is held — the
        separate ``get_recordings`` + ``put_recordings`` released the lock between read and write, so
        a concurrent chunk-upload / finalize clobbered the other (lost update). Returns ``result``."""
        ...

    async def owner_of(self, meeting_id: int) -> Optional[int]:
        """The ``user_id`` that owns ``meeting_id`` — used to scope ``GET /recordings`` listing."""
        ...

    async def list_meeting_recordings(self, user_id: int) -> list[dict]:
        """Every recording across the user's meetings (for ``GET /recordings``)."""
        ...
