"""The recordings flow — chunk upload + finalize → master in ``meeting.data`` JSONB.

Port of the parent ``recordings.internal_upload_recording`` + ``recording_finalizer`` CORE:

  * ``upload_chunk(...)`` — verify the MeetingToken, resolve the bot's ``MeetingSession`` by
    ``session_uid``, upload the chunk to object storage, fold it into the recording's JSONB payload
    (``jsonb.apply_chunk_to_recording``) under a read-modify-write on ``meeting.data['recordings']``,
    and return the upload receipt.
  * ``finalize_master(...)`` — concatenate a recording media-file's chunks into a master via the
    golden-locked ``build_recording_master`` codec, upload the master, and stamp the JSONB media-file
    (``storage_path`` → master key, ``finalized_by``, ``is_final``, ``playback_url``).

The codec itself (``meeting_api.build_recording_master``, recording.v1) is already ported +
golden-locked — this module only orchestrates the IO + the JSONB bookkeeping around it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from ..obs import log_event
from ..recording_codec import build_recording_master
from .jsonb import apply_chunk_to_recording, chunk_storage_key, master_storage_key, new_recording_numeric_id
from .ports import RecordingRepo, Storage

# Media content types (parent ``recording_codec._media_content_type``, reduced to the core set).
_CONTENT_TYPES = {"webm": "video/webm", "wav": "audio/wav"}


def _content_type(media_format: str) -> str:
    return _CONTENT_TYPES.get(media_format, "application/octet-stream")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SessionNotFound(Exception):
    """The upload's ``session_uid`` matches no MeetingSession AND it is the final chunk → 404."""


async def upload_chunk(
    repo: RecordingRepo,
    storage: Storage,
    *,
    token_meeting_id: int,
    session_uid: str,
    data: bytes,
    media_type: str = "audio",
    media_format: str = "wav",
    chunk_seq: int = 0,
    is_final: bool = True,
    duration_seconds: Optional[float] = None,
    sample_rate: Optional[int] = None,
) -> dict:
    """Process ONE recording chunk upload. ``token_meeting_id`` is the verified MeetingToken's
    meeting_id (the route verifies the token before calling this).

    Returns ``{recording_id, media_file_id, storage_path, status, chunk_seq}``. When the session is
    not yet known and the chunk is non-final, returns ``{"status": "pending"}`` (the bot retries).
    """
    session = await repo.find_session(session_uid)
    if session is None:
        if not is_final:
            return {"status": "pending"}
        raise SessionNotFound(f"no MeetingSession for session_uid {session_uid}")

    meeting_id = session["meeting_id"]
    if meeting_id != token_meeting_id:
        # The MeetingToken was minted for a different meeting — fail closed.
        raise SessionNotFound("MeetingToken meeting_id does not match the session's meeting")

    owner = await repo.owner_of(meeting_id)

    # Find / start the bot recording for this session.
    recordings = await repo.get_recordings(meeting_id)
    existing_rec = next(
        (r for r in recordings if r.get("session_uid") == session_uid and r.get("source") == "bot"),
        None,
    )
    recording_id = existing_rec["id"] if existing_rec else new_recording_numeric_id()

    # Upload the chunk to object storage.
    key = chunk_storage_key(
        user_id=owner or 0, recording_id=recording_id, session_uid=session_uid,
        media_type=media_type, media_format=media_format, chunk_seq=chunk_seq,
    )
    await storage.upload(key, data, content_type=_content_type(media_format))

    # Fold the chunk into the JSONB payload + write back.
    rec_payload, transitioned = apply_chunk_to_recording(
        existing_rec,
        recording_id=recording_id, meeting_id=meeting_id, user_id=owner or 0,
        session_uid=session_uid, media_type=media_type, media_format=media_format,
        storage_path=key, file_size=len(data), chunk_seq=chunk_seq, is_final=is_final,
        duration_seconds=duration_seconds, sample_rate=sample_rate,
    )
    others = [r for r in recordings if r.get("id") != recording_id]
    await repo.put_recordings(meeting_id, others + [rec_payload])

    media_file = next((mf for mf in rec_payload["media_files"] if mf["type"] == media_type), {})
    if transitioned:
        log_event(
            "recording_completed", audience="user", span="recordings.upload",
            user_id=owner, meeting_id=str(meeting_id),
            fields={"recording_id": recording_id, "media_type": media_type},
        )
    return {
        "recording_id": recording_id,
        "media_file_id": media_file.get("id"),
        "storage_path": key,
        "status": rec_payload["status"],
        "chunk_seq": chunk_seq,
    }


async def finalize_master(
    repo: RecordingRepo,
    storage: Storage,
    *,
    meeting_id: int,
    recording_id: int,
    media_type: str = "audio",
) -> Optional[str]:
    """Build + upload the master for a recording media-file and stamp the JSONB. Idempotent: if the
    master already exists in storage it is reused. Returns the master storage key, or ``None`` when
    there is nothing to finalize.
    """
    recordings = await repo.get_recordings(meeting_id)
    rec = next((r for r in recordings if r.get("id") == recording_id), None)
    if rec is None:
        return None
    mf = next((m for m in rec.get("media_files", []) if m.get("type") == media_type), None)
    if mf is None:
        return None

    media_format = mf.get("format", "wav")
    master_key = master_storage_key(mf["storage_path"], media_format)

    if not await storage.exists(master_key):
        # Gather the chunk objects under the recording's prefix (excluding any prior master).
        prefix = mf["storage_path"].rsplit("/", 1)[0]
        keys = [k for k in await storage.list(prefix) if not k.rsplit("/", 1)[-1].startswith("master.")]
        chunks = [await storage.get(k) for k in sorted(keys)]
        master_bytes = build_recording_master(chunks, media_format)
        await storage.upload(master_key, master_bytes, content_type=_content_type(media_format))

    # Stamp the media-file as finalized + write back.
    mf["storage_path"] = master_key
    mf["is_final"] = True
    mf["finalized_at"] = _now_iso()
    mf["finalized_by"] = "recording_finalizer.master"
    rec["playback_url"] = {
        "audio": f"/recordings/{recording_id}/master?type=audio" if media_type == "audio" else None,
        "video": f"/recordings/{recording_id}/master?type=video" if media_type == "video" else None,
    }
    others = [r for r in recordings if r.get("id") != recording_id]
    await repo.put_recordings(meeting_id, others + [rec])
    return master_key


def _verify_meeting_token(token: str, *, secret: Optional[str] = None) -> dict[str, Any]:
    """Verify a MeetingToken (HS256, ``ADMIN_TOKEN``-signed) and return its claims. Raises
    ``ValueError`` on a bad signature / expiry (the parent ``verify_meeting_token``)."""
    import base64
    import hmac
    import json
    import os

    secret = secret if secret is not None else os.environ.get("ADMIN_TOKEN")
    if not secret:
        raise ValueError("ADMIN_TOKEN not configured; cannot verify MeetingToken")
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
    except ValueError:
        raise ValueError("malformed MeetingToken")
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected = hmac.new(secret.encode(), signing_input, digestmod="sha256").digest()
    got = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
    if not hmac.compare_digest(expected, got):
        raise ValueError("MeetingToken signature mismatch")
    claims = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
    exp = claims.get("exp")
    if exp is not None and int(datetime.now(timezone.utc).timestamp()) > int(exp):
        raise ValueError("MeetingToken expired")
    return claims
