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
import hmac
import re
from typing import Any, Optional

from ..obs import log_event
from ..recording_codec import build_recording_master
from .jsonb import (
    apply_chunk_to_recording,
    chunk_storage_key,
    master_storage_key,
    recording_numeric_id_for_session,
)
from .ports import RecordingRepo, RecordingWriteRefused, Storage

# Media content types (parent ``recording_codec._media_content_type``, reduced to the core set).
_CONTENT_TYPES = {
    "webm": "video/webm",
    "wav": "audio/wav",
    "mp4": "video/mp4",
    "mkv": "video/x-matroska",
}
_MEDIA_TYPES = frozenset({"audio", "video"})
_MEDIA_FORMATS = frozenset(_CONTENT_TYPES)
_KEY_SEGMENT = re.compile(r"^[A-Za-z0-9._:-]+$")
_MAX_CHUNK_SEQ = 2_147_483_647


def _content_type(media_format: str) -> str:
    return _CONTENT_TYPES.get(media_format, "application/octet-stream")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SessionNotFound(Exception):
    """The upload's ``session_uid`` matches no MeetingSession AND it is the final chunk → 404."""


class InvalidRecordingMetadata(ValueError):
    """An upload field could produce an unsafe or unsupported object-storage key."""


class RecordingChunkConflict(RuntimeError):
    """A deterministic chunk sequence already exists with different bytes."""


def _validate_upload_metadata(
    *, session_uid: str, media_type: str, media_format: str, chunk_seq: int
) -> None:
    if (
        not isinstance(session_uid, str)
        or not _KEY_SEGMENT.fullmatch(session_uid)
        or session_uid in {".", ".."}
    ):
        raise InvalidRecordingMetadata("invalid session identity")
    if media_type not in _MEDIA_TYPES:
        raise InvalidRecordingMetadata("invalid media type")
    if media_format not in _MEDIA_FORMATS:
        raise InvalidRecordingMetadata("invalid media format")
    if (
        isinstance(chunk_seq, bool)
        or not isinstance(chunk_seq, int)
        or not 0 <= chunk_seq <= _MAX_CHUNK_SEQ
    ):
        raise InvalidRecordingMetadata("invalid chunk sequence")


async def upload_chunk(
    repo: RecordingRepo,
    storage: Storage,
    *,
    token_meeting_id: Optional[int],
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
    _validate_upload_metadata(
        session_uid=session_uid,
        media_type=media_type,
        media_format=media_format,
        chunk_seq=chunk_seq,
    )
    session = await repo.find_session(session_uid)
    if session is None:
        if not is_final:
            return {"status": "pending"}
        raise SessionNotFound(f"no MeetingSession for session_uid {session_uid}")

    meeting_id = session["meeting_id"]
    if token_meeting_id is not None and meeting_id != token_meeting_id:
        # A MeetingToken was used and was minted for a different meeting — fail closed.
        # (token_meeting_id is None for internal-secret auth, which is already meeting-scoped by session.)
        raise SessionNotFound("MeetingToken meeting_id does not match the session's meeting")

    # The lease spans BOTH object upload and JSONB mutation. Erasure takes the exclusive side of the
    # same gate, waits for this block to drain, persists the non-writable state, then sweeps objects.
    async with repo.recording_write(meeting_id):
        owner = await repo.owner_of(meeting_id)
        if owner is None:
            raise RecordingWriteRefused("meeting is not writable")

        # Find / start the bot recording for this session.
        recordings = await repo.get_recordings(meeting_id)
        existing_rec = next(
            (r for r in recordings if r.get("session_uid") == session_uid and r.get("source") == "bot"),
            None,
        )
        recording_id = existing_rec["id"] if existing_rec else recording_numeric_id_for_session(
            user_id=owner,
            meeting_id=meeting_id,
            session_uid=session_uid,
        )

        # Upload the chunk to object storage (idempotent by key; OUTSIDE the row lock but INSIDE the
        # meeting write lease).
        key = chunk_storage_key(
            user_id=owner, recording_id=recording_id, session_uid=session_uid,
            media_type=media_type, media_format=media_format, chunk_seq=chunk_seq,
        )
        prefix = key.rsplit("/", 2)[0] + "/"
        await repo.register_recording_prefix(meeting_id, prefix)
        async with repo.chunk_write(key):
            object_already_present = await storage.exists(key)
            try:
                if object_already_present:
                    stored_data = await storage.get(key)
                    if not hmac.compare_digest(stored_data, data):
                        raise RecordingChunkConflict(
                            "recording chunk conflicts with its existing sequence"
                        )
                else:
                    await storage.upload(key, data, content_type=_content_type(media_format))

                # G3 — fold the chunk into the JSONB ATOMICALLY: the mutator reads the LIVE recordings
                # under one row lock and folds cumulatively, so concurrent chunks cannot clobber one
                # another. If this durable fold fails, remove the exact object before releasing the
                # meeting write lease; otherwise the random first-recording prefix is undiscoverable.
                def _fold(recs):
                    ex = next(
                        (r for r in recs if r.get("session_uid") == session_uid and r.get("source") == "bot"), None
                    )
                    rid = ex["id"] if ex else recording_id
                    payload, transitioned_ = apply_chunk_to_recording(
                        ex,
                        recording_id=rid, meeting_id=meeting_id, user_id=owner,
                        session_uid=session_uid, media_type=media_type, media_format=media_format,
                        storage_path=key, file_size=len(data), chunk_seq=chunk_seq, is_final=is_final,
                        duration_seconds=duration_seconds, sample_rate=sample_rate,
                    )
                    others = [r for r in recs if r.get("id") != rid]
                    return others + [payload], (payload, transitioned_)

                rec_payload, transitioned = await repo.mutate_recordings(meeting_id, _fold)
            except BaseException:
                if not object_already_present:
                    try:
                        await storage.delete(key)
                    except BaseException:
                        raise RuntimeError("recording upload compensation requires retry") from None
                raise
        recording_id = rec_payload["id"]

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
    # Finalization reads chunks, writes a master, and stamps JSONB; all three belong to one recording
    # write and must drain before erasure takes its exclusive barrier.
    async with repo.recording_write(meeting_id):
        return await _finalize_master_under_lease(
            repo,
            storage,
            meeting_id=meeting_id,
            recording_id=recording_id,
            media_type=media_type,
        )


async def _finalize_master_under_lease(
    repo: RecordingRepo,
    storage: Storage,
    *,
    meeting_id: int,
    recording_id: int,
    media_type: str,
) -> Optional[str]:
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

    # G3 — stamp the media-file finalized ATOMICALLY (read→modify→write under one row lock), so a late
    # concurrent chunk upload can't clobber the finalized master pointer (the master bytes are already
    # uploaded above, idempotently by key). The mutator re-reads the LIVE recording.
    def _stamp(recs):
        r = next((x for x in recs if x.get("id") == recording_id), None)
        if r is None:
            return recs, None
        m = next((x for x in r.get("media_files", []) if x.get("type") == media_type), None)
        if m is None:
            return recs, None
        m["storage_path"] = master_key
        m["is_final"] = True
        m["finalized_at"] = _now_iso()
        m["finalized_by"] = "recording_finalizer.master"
        existing_pb = r.get("playback_url") or {}
        r["playback_url"] = {
            "audio": existing_pb.get("audio")
            or (f"/recordings/{recording_id}/master?type=audio" if media_type == "audio" else None),
            "video": existing_pb.get("video")
            or (f"/recordings/{recording_id}/master?type=video" if media_type == "video" else None),
        }
        others = [x for x in recs if x.get("id") != recording_id]
        return others + [r], master_key

    return await repo.mutate_recordings(meeting_id, _stamp)


def _verify_meeting_token(token: str, *, secret: Optional[str] = None) -> dict[str, Any]:
    """Verify an upload-purpose MeetingToken and return its claims.

    The signature alone is not authorization: the JOSE profile, issuer, audience, and scope must
    all match the recording collector's one intended use.
    """
    import base64
    import binascii
    import hmac
    import json
    import os

    secret = secret if secret is not None else os.environ.get("ADMIN_TOKEN")
    if not secret:
        raise ValueError("ADMIN_TOKEN not configured; cannot verify MeetingToken")
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
        header = json.loads(base64.urlsafe_b64decode(header_b64 + "=" * (-len(header_b64) % 4)))
        claims = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
        got = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
        raise ValueError("malformed MeetingToken") from None
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise ValueError("malformed MeetingToken")
    if header.get("alg") != "HS256" or header.get("typ") != "JWT":
        raise ValueError("MeetingToken JOSE profile mismatch")
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected = hmac.new(secret.encode(), signing_input, digestmod="sha256").digest()
    if not hmac.compare_digest(expected, got):
        raise ValueError("MeetingToken signature mismatch")
    if (
        claims.get("iss") != "meeting-api"
        or claims.get("aud") != "transcription-collector"
        or claims.get("scope") != "transcribe:write"
    ):
        raise ValueError("MeetingToken purpose mismatch")
    meeting_id = claims.get("meeting_id")
    if isinstance(meeting_id, bool) or not isinstance(meeting_id, int) or meeting_id <= 0:
        raise ValueError("MeetingToken meeting identity missing or invalid")
    exp = claims.get("exp")
    try:
        expires_at = int(exp)
    except (TypeError, ValueError):
        raise ValueError("MeetingToken expiry missing or invalid") from None
    if int(datetime.now(timezone.utc).timestamp()) >= expires_at:
        raise ValueError("MeetingToken expired")
    return claims
