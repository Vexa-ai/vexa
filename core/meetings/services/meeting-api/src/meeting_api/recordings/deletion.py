"""Owner-scoped object deletion primitives — recordings AND captured-signal tapes.

Object storage is erased before JSONB metadata is removed.  That ordering is deliberate: if an
S3/MinIO delete fails, the persisted paths remain addressable and the same request can be retried.

Two keyspaces, because a meeting leaves artifacts in two: ``recordings/{user}/{recording}/...``
(folded into ``meeting.data['recordings']``, reachable through the recordings API) and
``signal/{user}/{meeting}/{session}/...`` (the captured-signal tape, an internal replay fixture
that is deliberately invisible to the recordings API). An owner asking for a meeting to be erased
means both; only the first was erased before #116.
"""
from __future__ import annotations

from typing import Optional

from ..obs import log_event
from .jsonb import signal_meeting_prefix
from .ports import RecordingRepo, Storage
from .signal_janitor import group_tapes


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


def _owner_prefix(recording: dict) -> Optional[str]:
    """The owner's whole object namespace — the boundary a deletion may never reach past."""
    user_id = recording.get("user_id")
    return f"recordings/{user_id}/" if user_id is not None else None


async def recording_object_keys(storage: Storage, recording: dict) -> list[str]:
    """Discover every current object plus any explicitly persisted legacy/master path."""
    keys: set[str] = set()
    prefix = _recording_prefix(recording)
    if prefix:
        keys.update(await storage.list(prefix))
    owner_prefix = _owner_prefix(recording)
    for media_file in recording.get("media_files") or []:
        path = media_file.get("storage_path") if isinstance(media_file, dict) else None
        # A persisted path is data, not authority. Every writer derives it from
        # ``chunk_storage_key(user_id=owner, ...)``, so it already lies inside the owner's namespace
        # and this rejects nothing today. It is what keeps the blast radius owner-bounded if a
        # ``storage_path`` ever becomes writable from a request, or a backend normalises ``..``:
        # the worst such a path could then do is name another object of the SAME owner.
        if path and owner_prefix and path.startswith(owner_prefix):
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


# ── captured-signal tapes (#116) ────────────────────────────────────────────────────────────────
# A tape is not a recording and deliberately does not fold into ``meeting.data['recordings']``
# (see ``jsonb.py``), so the recording sweep above cannot see it: erasing every recording object of
# a meeting left the raw per-channel PCM the bot teed for offline replay sitting in the bucket,
# under ``signal/{user}/{meeting}/{session}/``, until the 50 GB budget janitor happened to evict it.
# For an owner who asked for a meeting to be erased that is the same personal data by another name,
# and "it expires eventually, at a size threshold, in an order nobody can predict" is not an answer
# a retention commitment can be written against.
#
# Two properties this does NOT share with the janitor:
#
#   * **The PROMOTED marker does not spare a tape here.** Promotion means "keep this one in the
#     regression library" and it outranks a *budget*; it does not outrank the person whose voice is
#     on the tape asking for it to go. A promoted tape that is erased is named in the audit line
#     (``promoted: true``) precisely so a curator can see a fixture left the library and why.
#   * **No ``min_age_s`` guard.** That guard exists so a sweep firing on a timer cannot race an
#     upload still in flight. Deletion here is not on a timer: the meeting is already terminal, so
#     nothing is still uploading, and a deliberate erasure that skipped "too young" objects would
#     silently leave the newest ones behind.


async def signal_tape_object_keys(storage: Storage, *, user_id: int, meeting_id: int) -> list[str]:
    """Every object under the meeting's tape prefix, owner-bounded."""
    prefix = signal_meeting_prefix(user_id=user_id, meeting_id=meeting_id)
    # Belt and braces: ``list`` is already prefix-scoped, and the re-check is what holds the bound
    # if a backend ever returned a key it was not asked for.
    return sorted(k for k in await storage.list(prefix) if k.startswith(prefix))


async def delete_signal_tapes(storage: Storage, *, user_id: int, meeting_id: int) -> list[str]:
    """Erase every captured-signal tape of one owned meeting, and say so in the audit log.

    Returns the keys attempted. Every object under the prefix goes, including a ``PROMOTED``
    marker and including any key that is not shaped like a tape part — the prefix is already
    bounded to one owner's one meeting, so "everything under it" has no blast radius beyond the
    thing the owner asked to erase, whereas "only what I recognise" leaves residue.
    """
    prefix = signal_meeting_prefix(user_id=user_id, meeting_id=meeting_id)
    objects = [o for o in await storage.list_detailed(prefix)
               if str(o.get("key") or "").startswith(prefix)]
    if not objects:
        return []
    # Grouped only to make the audit line readable per tape; deletion iterates the flat listing so
    # an unrecognised key is still removed.
    tapes = group_tapes(objects)
    keys = sorted(str(o["key"]) for o in objects)
    for key in keys:
        # No try/except: a failed object delete must abort the whole erasure BEFORE the DB is
        # scrubbed, so the same request retries against the same keys (the janitor swallows errors
        # because a sweep must not stall; an erasure must not lie).
        await storage.delete(key)
    for tape in sorted(tapes.values(), key=lambda t: t["prefix"]):
        log_event(
            "signal_tape_erased", audience="operator", span="recordings.signal.erase",
            user_id=user_id, meeting_id=str(meeting_id),
            fields={"prefix": tape["prefix"], "parts": len(tape["keys"]),
                    "bytes": tape["bytes"], "promoted": tape["promoted"],
                    "reason": "owner_artifact_deletion"},
        )
    unaccounted = len(keys) - sum(len(t["keys"]) for t in tapes.values())
    if unaccounted:
        log_event(
            "signal_tape_erased_unshaped", audience="operator", level="warning",
            span="recordings.signal.erase", user_id=user_id, meeting_id=str(meeting_id),
            fields={"prefix": prefix, "objects": unaccounted,
                    "hint": "objects under the meeting's signal prefix that are not shaped like a "
                            "tape part were erased with it"},
        )
    return keys
