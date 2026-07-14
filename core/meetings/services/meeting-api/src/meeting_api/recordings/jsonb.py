"""recording.v1 JSONB record materialization — the ``meeting.data['recordings'][]`` writer.

Ported from the parent ``recording_jsonb.py``: fold one uploaded chunk into the recording's JSONB
payload — per-type ``media_files`` cumulative tracking, late-chunk-master-preserve (Pack U.7), and
sticky COMPLETED status. PURE dict logic, NO IO/DB: the route keeps the storage upload + the row
lock + commit; this owns only the record shape (recordings live in ``meetings.data`` JSONB — there
is NO separate recordings table).
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

_STATUS_COMPLETED = "completed"
_STATUS_IN_PROGRESS = "in_progress"
_SOURCE_BOT = "bot"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_recording_numeric_id() -> int:
    """A random 12-digit recording id (parent ``new_recording_numeric_id``)."""
    return int(uuid.uuid4().int % 900000000000 + 100000000000)


def recording_numeric_id_for_session(
    *, user_id: int, meeting_id: int, session_uid: str
) -> int:
    """Return one stable 12-digit bot-recording id for a meeting session.

    Concurrent first chunks calculate the same identity before either can publish JSONB. IDs are
    routing identifiers, not secrets; owner checks remain authoritative at every read boundary.
    """

    identity = f"{user_id}:{meeting_id}:{session_uid}:bot".encode("utf-8")
    digest = hashlib.sha256(identity).digest()
    return int.from_bytes(digest[:8], "big") % 900000000000 + 100000000000


def apply_chunk_to_recording(
    existing_rec: Optional[dict],
    *,
    recording_id: int,
    meeting_id: int,
    user_id: int,
    session_uid: str,
    media_type: str,
    media_format: str,
    storage_path: str,
    file_size: int,
    chunk_seq: int,
    is_final: bool,
    duration_seconds: Optional[float],
    sample_rate: Optional[int],
) -> tuple[dict, bool]:
    """Fold one uploaded chunk into the recording payload.

    ``existing_rec`` is the prior recording dict for this (session_uid, source=bot), or ``None`` to
    start fresh. Returns ``(rec_payload, status_transitioned_to_completed)``; the caller places
    ``rec_payload`` into ``data['recordings']`` and commits.
    """
    if existing_rec is None:
        rec_payload: dict[str, Any] = {
            "id": recording_id,
            "meeting_id": meeting_id,
            "user_id": user_id,
            "session_uid": session_uid,
            "source": _SOURCE_BOT,
            "status": _STATUS_COMPLETED if is_final else _STATUS_IN_PROGRESS,
            "created_at": _now_iso(),
            "completed_at": _now_iso() if is_final else None,
            "media_files": [],
        }
        was_completed = False
    else:
        rec_payload = dict(existing_rec)
        was_completed = rec_payload.get("status") == _STATUS_COMPLETED

    status_transitioned = False
    prior_media_files = list(rec_payload.get("media_files") or [])
    prior_same_type = next((mf for mf in prior_media_files if mf.get("type") == media_type), None)
    prior_bytes = int((prior_same_type or {}).get("file_size_bytes") or 0) if prior_same_type else 0
    prior_chunk_count = int((prior_same_type or {}).get("chunk_count") or (1 if prior_same_type else 0))
    prior_first_chunk_at = (prior_same_type or {}).get("first_chunk_at") if prior_same_type else None
    prior_metadata = dict((prior_same_type or {}).get("metadata") or {})
    raw_chunk_sizes = prior_metadata.get("zaki_chunk_sizes")
    chunk_sizes = {
        str(sequence): int(size)
        for sequence, size in (raw_chunk_sizes.items() if isinstance(raw_chunk_sizes, dict) else [])
        if isinstance(size, int) and not isinstance(size, bool) and size >= 0
    }
    if prior_same_type and not chunk_sizes:
        prior_sequence = (prior_same_type or {}).get("chunk_seq")
        prior_size = (prior_same_type or {}).get("last_chunk_size_bytes")
        if isinstance(prior_sequence, int) and isinstance(prior_size, int) and prior_size >= 0:
            chunk_sizes[str(prior_sequence)] = prior_size
    sequence_key = str(chunk_seq)
    replaced_size = chunk_sizes.get(sequence_key)
    cumulative_bytes = prior_bytes + file_size - (replaced_size or 0)
    cumulative_chunk_count = prior_chunk_count + (0 if replaced_size is not None else 1)
    chunk_sizes[sequence_key] = file_size
    first_chunk_at = prior_first_chunk_at or _now_iso()
    media_files = [mf for mf in prior_media_files if mf.get("type") != media_type]

    # Pack U.7 — preserve a finalized master path against a late-chunk overwrite.
    prior_sp = (prior_same_type or {}).get("storage_path") or ""
    prior_is_final = bool((prior_same_type or {}).get("is_final"))
    master_finalized = (
        prior_sp.endswith("/audio/master.webm")
        or prior_sp.endswith("/audio/master.wav")
        or prior_is_final
    )
    new_storage_path = prior_sp if master_finalized else storage_path
    new_is_final = True if master_finalized else is_final

    media_files.append({
        "id": (prior_same_type or {}).get("id") or new_recording_numeric_id(),
        "type": media_type,
        "format": media_format,
        "storage_path": new_storage_path,
        "storage_backend": os.environ.get("STORAGE_BACKEND", "minio"),
        "file_size_bytes": cumulative_bytes,
        "last_chunk_size_bytes": file_size,
        "chunk_count": cumulative_chunk_count,
        "duration_seconds": duration_seconds,
        "chunk_seq": chunk_seq,
        "first_chunk_at": first_chunk_at,
        "metadata": {
            **prior_metadata,
            **({"sample_rate": sample_rate} if sample_rate else {}),
            "zaki_chunk_sizes": chunk_sizes,
        },
        "created_at": _now_iso(),
        "is_final": new_is_final,
        "finalized_at": (prior_same_type or {}).get("finalized_at"),
        "finalized_by": (prior_same_type or {}).get("finalized_by"),
    })
    rec_payload["media_files"] = media_files

    # Advertise the stable master route per available media type so a player can surface the recording
    # as soon as a chunk lands (clients gate on ``playback_url.audio``); the master is built and its
    # bytes resolved lazily on the first ``GET /recordings/{id}/master`` (finalize-on-read).
    _types_present = {mf["type"] for mf in media_files}
    rec_payload["playback_url"] = {
        "audio": f"/recordings/{recording_id}/master?type=audio" if "audio" in _types_present else None,
        "video": f"/recordings/{recording_id}/master?type=video" if "video" in _types_present else None,
    }

    if is_final:
        rec_payload["status"] = _STATUS_COMPLETED
        rec_payload["completed_at"] = _now_iso()
        status_transitioned = not was_completed
    elif not was_completed:
        # Terminal state is sticky — never downgrade COMPLETED → IN_PROGRESS on a stray late chunk.
        rec_payload["status"] = _STATUS_IN_PROGRESS

    return rec_payload, status_transitioned


def chunk_storage_key(
    *, user_id: int, recording_id: int, session_uid: str, media_type: str, media_format: str,
    chunk_seq: int,
) -> str:
    """The object key for one uploaded chunk (parent's scheme — ``media_type`` in the path keeps
    audio/video from colliding at ``chunk_seq=0``)."""
    return (
        f"recordings/{user_id}/{recording_id}/{session_uid}/{media_type}/"
        f"{chunk_seq:06d}.{media_format}"
    )


def master_storage_key(chunk_key: str, media_format: str) -> str:
    """The master object key for a recording's media file — ``<chunk-prefix>/master.<fmt>`` (the
    finalizer's ``_chunk_prefix(storage_path) + '/master.<fmt>'``)."""
    prefix = chunk_key.rsplit("/", 1)[0]
    return f"{prefix}/master.{media_format}"
