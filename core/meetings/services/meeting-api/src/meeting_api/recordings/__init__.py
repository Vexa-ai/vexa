"""recordings — chunk upload + finalize → master in ``meeting.data`` JSONB (recording.v1).

Front door (P6): import from here, never a deep module path.

Port of the parent ``recordings.internal_upload_recording`` + ``recording_finalizer`` + the
``recording_jsonb`` writer. The bot streams recording chunks (authenticated by the MeetingToken it
carries); each chunk lands in object storage and is folded into the recording's JSONB payload under
``meeting.data['recordings']`` (there is NO separate recordings table). Finalize concatenates a
recording's chunks into a master via the golden-locked ``build_recording_master`` codec and stamps
the JSONB media-file.

Collaborators (object storage, the meeting store) are injected as PORTS so the same flow runs with
real adapters (MinIO + SQLAlchemy) in prod and in-process fakes in tests.

Public surface:
  * ``build_router(repo, storage)`` — the mountable routes (the unified app mounts them):
    POST ``/internal/recordings/upload``, GET ``/recordings``, GET ``/recordings/{id}/master``.
  * ``upload_chunk(...)`` / ``finalize_master(...)`` — the flow core (callable directly in tests).
  * ``apply_chunk_to_recording`` / ``chunk_storage_key`` / ``master_storage_key`` /
    ``new_recording_numeric_id`` — the pure JSONB record materializers.
  * ``Storage`` / ``RecordingRepo`` ports + ``SessionNotFound``.
  * ``adapters.build_production_router(...)`` — wire with real MinIO/S3 + SQLAlchemy.
  * ``fakes`` — ``InMemoryStorage`` / ``InMemoryRecordingRepo`` (offline drivers).
"""
from __future__ import annotations

from .jsonb import (
    apply_chunk_to_recording,
    chunk_storage_key,
    master_storage_key,
    new_recording_numeric_id,
)
from .ports import RecordingRepo, Storage
from .router import build_router
from .service import InvalidRecordingMetadata, SessionNotFound, finalize_master, upload_chunk

__all__ = [
    "build_router",
    "upload_chunk",
    "finalize_master",
    "apply_chunk_to_recording",
    "chunk_storage_key",
    "master_storage_key",
    "new_recording_numeric_id",
    "RecordingRepo",
    "Storage",
    "SessionNotFound",
    "InvalidRecordingMetadata",
]
