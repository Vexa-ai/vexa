"""Owner-scoped recording-object deletion primitives.

Object storage is erased before JSONB metadata is removed.  That ordering is deliberate: if an
S3/MinIO delete fails, the persisted paths remain addressable and the same request can be retried.
"""
from __future__ import annotations

from typing import Optional

from .ports import RecordingRepo, Storage


class MeetingNotTerminal(Exception):
    """The recording exists, but its meeting lifecycle may still produce more artifacts."""


def _recording_prefix(recording: dict) -> Optional[str]:
    """Return the canonical key prefix for every chunk/master belonging to ``recording``."""
    user_id = recording.get("user_id")
    recording_id = recording.get("id")
    session_uid = recording.get("session_uid")
    if user_id is None or recording_id is None or not session_uid:
        return None
    return f"recordings/{user_id}/{recording_id}/{session_uid}/"


async def recording_object_keys(storage: Storage, recording: dict) -> list[str]:
    """Discover every current object plus any explicitly persisted legacy/master path."""
    keys: set[str] = set()
    prefix = _recording_prefix(recording)
    if prefix:
        keys.update(await storage.list(prefix))
    for media_file in recording.get("media_files") or []:
        path = media_file.get("storage_path") if isinstance(media_file, dict) else None
        if path:
            keys.add(path)
    return sorted(keys)


async def delete_recording_objects(storage: Storage, recording: dict) -> list[str]:
    """Delete all discoverable objects idempotently and return the keys attempted."""
    keys = await recording_object_keys(storage, recording)
    for key in keys:
        await storage.delete(key)
    return keys


async def delete_owned_recording(
    repo: RecordingRepo, storage: Storage, *, user_id: int, recording_id: int
) -> Optional[dict]:
    """Delete one caller-owned recording; unknown and unowned ids are indistinguishable.

    Storage deletion completes before the atomic JSONB mutation.  A storage exception therefore
    leaves the recording metadata intact for a safe retry.
    """
    recording = await repo.prepare_recording_deletion(user_id, recording_id)
    if recording is None:
        return None
    if recording.get("error") == "conflict":
        raise MeetingNotTerminal

    meeting_id = int(recording["meeting_id"])
    deleted_keys = await delete_recording_objects(storage, recording)

    def _remove(current: list[dict]):
        remaining = [r for r in current if r.get("id") != recording_id]
        return remaining, len(remaining) != len(current)

    await repo.mutate_recordings(meeting_id, _remove)
    return {
        "status": "deleted",
        "recording_id": recording_id,
        "meeting_id": meeting_id,
        "objects_deleted": len(deleted_keys),
        "scope": "primary_object_storage",
    }
