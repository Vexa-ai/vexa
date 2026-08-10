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

import asyncio
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from ..obs import log_event
from ..recording_codec import build_recording_master
from .jsonb import (
    SIGNAL_TAPE_PARTS,
    SIGNAL_TAPE_PART_FORMATS,
    apply_chunk_to_recording,
    chunk_storage_key,
    master_storage_key,
    new_recording_numeric_id,
    signal_tape_key,
)
from .ports import RecordingRepo, Storage

# Media content types (parent ``recording_codec._media_content_type``, reduced to the core set).
_CONTENT_TYPES = {"webm": "video/webm", "wav": "audio/wav", "jsonl": "application/x-ndjson",
                  "txt": "text/plain; charset=utf-8"}

# The ``media_type`` that means "a captured-signal tape, not playable media" (O-TEL-1).
SIGNAL_MEDIA_TYPE = "signal"
SIGNAL_MEDIA_FORMAT = "jsonl"


def _content_type(media_format: str) -> str:
    return _CONTENT_TYPES.get(media_format, "application/octet-stream")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SessionNotFound(Exception):
    """The upload's ``session_uid`` matches no MeetingSession AND it is the final chunk → 404."""


class InvalidSignalTape(Exception):
    """A tape upload named a part or format we do not accept → 422 (never a silent store)."""


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

    owner = await repo.owner_of(meeting_id)

    # Find / start the bot recording for this session.
    recordings = await repo.get_recordings(meeting_id)
    existing_rec = next(
        (r for r in recordings if r.get("session_uid") == session_uid and r.get("source") == "bot"),
        None,
    )
    recording_id = existing_rec["id"] if existing_rec else new_recording_numeric_id()

    # Upload the chunk to object storage (idempotent by key; OUTSIDE the row lock).
    key = chunk_storage_key(
        user_id=owner or 0, recording_id=recording_id, session_uid=session_uid,
        media_type=media_type, media_format=media_format, chunk_seq=chunk_seq,
    )
    await storage.upload(key, data, content_type=_content_type(media_format))

    # G3 — fold the chunk into the JSONB ATOMICALLY: the mutator reads the LIVE recordings under one
    # row lock and folds cumulatively, so a concurrent chunk/finalize can't clobber it (the old
    # get_recordings → apply → put_recordings were SEPARATE transactions → lost update). The mutator
    # re-resolves the recording for this session, so it reuses an id created concurrently.
    def _fold(recs):
        ex = next(
            (r for r in recs if r.get("session_uid") == session_uid and r.get("source") == "bot"), None
        )
        rid = ex["id"] if ex else recording_id
        payload, transitioned_ = apply_chunk_to_recording(
            ex,
            recording_id=rid, meeting_id=meeting_id, user_id=owner or 0,
            session_uid=session_uid, media_type=media_type, media_format=media_format,
            storage_path=key, file_size=len(data), chunk_seq=chunk_seq, is_final=is_final,
            duration_seconds=duration_seconds, sample_rate=sample_rate,
        )
        others = [r for r in recs if r.get("id") != rid]
        return others + [payload], (payload, transitioned_)

    rec_payload, transitioned = await repo.mutate_recordings(meeting_id, _fold)
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


async def upload_signal_tape(
    repo: RecordingRepo,
    storage: Storage,
    *,
    token_meeting_id: Optional[int],
    session_uid: str,
    data: bytes,
    part: str,
    media_format: str = SIGNAL_MEDIA_FORMAT,
) -> dict:
    """Store ONE captured-signal tape part for a bot session (O-TEL-1 fixture collection).

    Deliberately NOT ``upload_chunk`` with a third media_type. Three properties differ, and each of
    them is the reason:

      * **No JSONB fold.** A tape is an internal fixture, not the user's recording. Folding it into
        ``meeting.data['recordings']`` would surface a phantom recording in ``GET /recordings`` for
        every meeting the user never asked to record.
      * **No master, no chunk sequence.** The bot uploads a whole flushed file once at teardown;
        there is nothing to assemble and nothing to finalize-on-read.
      * **Its own keyspace** (``signal/…``), so the budget janitor can list every tape in the
        deployment without paging through the recordings prefix.

    Returns ``{"status": "stored", "storage_path", "bytes"}``. Raises ``SessionNotFound`` (404) when
    the session is unknown — unlike a chunk there is no "pending" case, because a tape is only ever
    uploaded at teardown, long after the session row exists — and ``InvalidSignalTape`` (422) for an
    unknown part/format, since the part name lands in an object key.
    """
    if part not in SIGNAL_TAPE_PARTS:
        raise InvalidSignalTape(
            f"unknown signal tape part {part!r}; known: {list(SIGNAL_TAPE_PARTS)}"
        )
    # Most parts are JSONL; the bot log is plain text. The expected format is a property of the
    # PART, not of the caller, so an uploader cannot choose an extension — the key would otherwise
    # be caller-shaped, and a curator downloading `botlog.jsonl` would get a text file their tools
    # try to parse a line at a time as JSON.
    expected = SIGNAL_TAPE_PART_FORMATS.get(part, SIGNAL_MEDIA_FORMAT)
    if media_format != expected:
        raise InvalidSignalTape(
            f"signal tape part {part!r} is {expected}, got {media_format!r}"
        )

    session = await repo.find_session(session_uid)
    if session is None:
        raise SessionNotFound(f"no MeetingSession for session_uid {session_uid}")
    meeting_id = session["meeting_id"]
    if token_meeting_id is not None and meeting_id != token_meeting_id:
        # Same fail-closed rule as a chunk upload: a MeetingToken minted for another meeting must
        # not be able to write into this one's prefix.
        raise SessionNotFound("MeetingToken meeting_id does not match the session's meeting")

    owner = await repo.owner_of(meeting_id)
    key = signal_tape_key(user_id=owner or 0, meeting_id=meeting_id, session_uid=session_uid,
                          part=part, media_format=media_format)
    await storage.upload(key, data, content_type=_content_type(media_format))
    log_event(
        "signal_tape_stored", audience="operator", span="recordings.signal",
        user_id=owner, meeting_id=str(meeting_id),
        fields={"session_uid": session_uid, "part": part, "bytes": len(data),
                "storage_path": key},
    )
    return {"status": "stored", "storage_path": key, "bytes": len(data),
            "meeting_id": meeting_id, "part": part}


async def finalize_master(
    repo: RecordingRepo,
    storage: Storage,
    *,
    meeting_id: int,
    recording_id: int,
    media_type: str = "audio",
) -> Optional[str]:
    """Build + upload the master for a recording media-file and stamp the JSONB. Returns the master
    storage key, or ``None`` when there is nothing to finalize.

    RE-ASSEMBLABLE, not write-once (#768). Existence is the WRONG freshness signal: a read while the
    meeting is still recording must not permanently freeze the master. The master is (re)built when
    it is absent OR when the number of chunk objects under the recording's prefix differs from the
    count the current master already represents (``assembled_chunk_count``). So a mid-recording read
    assembles a partial, and every later read that finds new chunks rebuilds — which also repairs a
    master frozen by a pre-fix stack on its next read. The freeze is impossible to reintroduce
    silently: the assembled-chunk-count is recorded and compared, and a rebuild-on-growth is logged.
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

    # Gather the chunk objects under the recording's prefix (excluding any prior master).
    prefix = mf["storage_path"].rsplit("/", 1)[0]
    keys = sorted(
        k for k in await storage.list(prefix) if not k.rsplit("/", 1)[-1].startswith("master.")
    )
    listed_count = len(keys)
    assembled_count = mf.get("assembled_chunk_count")

    # Loud guard (#769): the number of chunks we're about to assemble vs what the JSONB fold counted.
    # A mismatch means chunks were dropped from the listing (truncation) or the fold — surface it.
    jsonb_count = mf.get("chunk_count")
    if jsonb_count is not None and listed_count != jsonb_count:
        log_event(
            "recording_chunk_count_mismatch", audience="operator", span="recordings.finalize",
            meeting_id=str(meeting_id),
            fields={"recording_id": recording_id, "media_type": media_type,
                    "listed_count": listed_count, "jsonb_chunk_count": jsonb_count},
        )

    master_exists = await storage.exists(master_key)
    # Rebuild only when there ARE chunks to assemble (listed_count > 0) and either no master exists yet
    # or the chunk count changed since the master was last assembled. With zero chunk objects we never
    # rebuild — an existing master is served as-is rather than assembled from nothing.
    rebuild = listed_count > 0 and ((not master_exists) or assembled_count != listed_count)
    if rebuild:
        if master_exists and assembled_count is not None and listed_count > assembled_count:
            # A prior (partial) master is being superseded by chunks that arrived after it — the exact
            # #768 unfreeze. Log it so a re-freeze regression is noisy rather than silent.
            log_event(
                "recording_master_reassembled", audience="operator", span="recordings.finalize",
                meeting_id=str(meeting_id),
                fields={"recording_id": recording_id, "media_type": media_type,
                        "prior_assembled_count": assembled_count, "new_count": listed_count},
            )
        chunks = [await storage.get(k) for k in keys]
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
        m["assembled_chunk_count"] = listed_count
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


# ── ffmpeg-for-all: live MPEG-DASH storage + the on-demand combined download ──────────────────────

def _hls_content_type(rel: str) -> str:
    if rel.endswith(".m3u8"):
        return "application/vnd.apple.mpegurl"
    if rel.endswith(".m4s") or rel.endswith(".mp4"):
        return "video/mp4"
    return "application/octet-stream"


async def upload_hls_file(
    repo: RecordingRepo,
    storage: Storage,
    *,
    token_meeting_id: Optional[int],
    session_uid: str,
    relpath: str,
    data: bytes,
    is_final: bool = False,
    started_at: Optional[str] = None,
    has_video: Optional[bool] = None,
) -> dict:
    """Store ONE live-HLS file (``init.mp4``, ``chunk-<NNNNN>.m4s``, or ``playlist.m3u8``) for a
    recording under ``recordings/{u}/{rec}/{sid}/hls/<relpath>`` — verbatim, since the playlist's
    relative URIs must resolve. On the playlist (and the final marker) (re)register the recording's
    ``hls`` media-file + ``playback_url.hls`` so GET /recordings and the player find it. The bot's
    ffmpeg produced HLS-native segments, so there is NO server-side assembly. Returns
    ``{recording_id, meeting_id, status}``; ``{"status": "pending"}`` when the session isn't known
    yet (bot retries)."""
    if ".." in relpath or relpath.startswith("/"):
        raise SessionNotFound("bad HLS relpath")
    session = await repo.find_session(session_uid)
    if session is None:
        return {"status": "pending"}
    meeting_id = session["meeting_id"]
    if token_meeting_id is not None and meeting_id != token_meeting_id:
        raise SessionNotFound("MeetingToken meeting_id does not match the session's meeting")
    owner = await repo.owner_of(meeting_id)

    # Resolve a STABLE recording_id BEFORE storing, ensuring the recording row exists on EVERY upload —
    # not just the playlist. HLS files can arrive in any order (an init/chunk before the first playlist),
    # and every one must land under the SAME .../{recording_id}/.../hls/ prefix so the playlist's relative
    # URIs resolve. On the playlist (and the final marker) also (re)stamp the `hls` media-file +
    # playback_url.hls so GET /recordings and the player find it; `is_final` completes the recording.
    is_playlist = relpath == "playlist.m3u8"

    def _fold(recs):
        ex = next((r for r in recs if r.get("session_uid") == session_uid and r.get("source") == "bot"), None)
        others = [r for r in recs if not (r.get("session_uid") == session_uid and r.get("source") == "bot")]
        rec = dict(ex) if ex else {
            "id": new_recording_numeric_id(), "meeting_id": meeting_id, "user_id": owner or 0,
            "session_uid": session_uid, "source": "bot", "status": "in_progress",
            "created_at": _now_iso(), "completed_at": None, "media_files": [],
        }
        if is_final:
            rec["status"] = "completed"
            rec["completed_at"] = _now_iso()
        if is_playlist or is_final:
            playlist_key = f"recordings/{owner or 0}/{rec['id']}/{session_uid}/hls/playlist.m3u8"
            prior = next((m for m in (rec.get("media_files") or []) if m.get("type") == "hls"), None)
            mfs = [m for m in (rec.get("media_files") or []) if m.get("type") != "hls"]
            mfs.append({
                "id": (prior or {}).get("id") or new_recording_numeric_id(),
                "type": "hls", "format": "m3u8", "storage_path": playlist_key, "is_final": is_final,
                # Anchor to the recorder's true t=0 (ffmpeg start) the bot sends, NOT the receive time of
                # this (first) playlist — which lands a whole segment late and drags transcript sync back.
                "first_chunk_at": (prior or {}).get("first_chunk_at") or started_at or _now_iso(),
                # Whether this recording captured video — the player renders a <video> vs an audio control.
                "has_video": has_video if has_video is not None else (prior or {}).get("has_video", False),
            })
            rec["media_files"] = mfs
            pb = dict(rec.get("playback_url") or {})
            pb["hls"] = f"/recordings/{rec['id']}/hls/playlist.m3u8"
            rec["playback_url"] = pb
        return others + [rec], {"recording_id": rec["id"]}

    result = await repo.mutate_recordings(meeting_id, _fold)
    recording_id = (result or {}).get("recording_id")

    prefix = f"recordings/{owner or 0}/{recording_id}/{session_uid}/hls"
    await storage.upload(f"{prefix}/{relpath}", data, content_type=_hls_content_type(relpath))
    return {"recording_id": recording_id, "meeting_id": meeting_id, "status": "ok"}


def _ffmpeg_hls_to_mp4(hls_files: dict[str, bytes]) -> Optional[bytes]:
    """Remux a bot recording's live-HLS (playlist.m3u8 + init/chunk segments) into ONE self-contained
    downloadable mp4 via ffmpeg — video is always stream-COPIED (same codec as the recording). Blocking —
    call via ``asyncio.to_thread``. Returns the mp4 bytes, or ``None`` if ffmpeg is missing/fails (the
    caller then leaves the HLS stream as the only playback source).

    ffmpeg reads the playlist and its segments straight from the temp dir (paths are relative). Audio
    defaults to ``copy`` (zero transcode → the download's audio matches the recording); set
    RECORDING_COMBINED_AUDIO_CODEC to an ffmpeg encoder name (e.g. ``aac``, ``libopus``) to TRANSCODE it.
    """
    if "playlist.m3u8" not in hls_files:
        return None
    override = os.getenv("RECORDING_COMBINED_AUDIO_CODEC", "").strip().lower()
    acodec = override or "copy"
    with tempfile.TemporaryDirectory() as d:
        for rel, data in hls_files.items():
            p = os.path.join(d, rel)
            os.makedirs(os.path.dirname(p) or d, exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
        opath = os.path.join(d, "combined.mp4")
        cmd = [
            "ffmpeg", "-y",
            # -copyts + -start_at_zero: keep the HLS's already-aligned A/V timestamps and rebase to 0.
            # Without them ffmpeg's fMP4/HLS demuxer mis-offsets the AUDIO track to the first segment
            # boundary (~25 s) on a plain copy — the HLS itself is aligned (audio & video both start
            # ~0.1 s, so it plays synced in a browser) but the remuxed download would open with ~25 s of
            # silent, out-of-sync video. Verified on a real recording: audio start_time 0.000000 with
            # these flags vs 24.8 without (holds for both -c:a copy and a transcode override).
            "-copyts", "-start_at_zero",
            "-allowed_extensions", "ALL",
            "-i", os.path.join(d, "playlist.m3u8"),
            "-c:v", "copy",
            "-c:a", acodec,
            "-movflags", "+faststart",
            opath,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True)
        except FileNotFoundError:
            log_event("recording_combined_ffmpeg_missing", audience="system", level="error", span="recordings.combined")
            return None
        if proc.returncode != 0 or not os.path.exists(opath):
            log_event(
                "recording_combined_mux_failed", audience="system", level="error", span="recordings.combined",
                fields={"stderr": (proc.stderr or b"").decode("utf-8", "replace")[-500:]},
            )
            return None
        with open(opath, "rb") as f:
            return f.read()


def _hls_download_key(playlist_key: str) -> str:
    """The combined-download object, a sibling of the ``hls/`` dir: ``.../combined/combined.mp4``."""
    base = playlist_key.rsplit("/hls/", 1)[0]
    return f"{base}/combined/combined.mp4"


async def finalize_hls_download(
    repo: RecordingRepo,
    storage: Storage,
    *,
    meeting_id: int,
    recording_id: int,
    muxer: Callable[[dict], Optional[bytes]] = _ffmpeg_hls_to_mp4,
) -> Optional[str]:
    """Remux a bot recording's live-HLS into ONE downloadable mp4 (ENABLE_COMBINED_RECORDING) and stamp
    a synthetic ``combined`` media-file + ``playback_url.combined`` so the /master + /raw routes serve it
    by type. Idempotent + cached: reuses the combined object if present. Returns the combined key, or
    ``None`` when the recording has no HLS media-file yet (or ffmpeg failed). ``muxer`` is injectable so
    tests exercise the storage/JSONB orchestration without ffmpeg."""
    recordings = await repo.get_recordings(meeting_id)
    rec = next((r for r in recordings if r.get("id") == recording_id), None)
    if rec is None:
        return None
    hmf = next((m for m in rec.get("media_files", []) if m.get("type") == "hls"), None)
    playlist_key = (hmf or {}).get("storage_path")
    if not playlist_key:
        return None
    combined_key = _hls_download_key(playlist_key)

    if not await storage.exists(combined_key):
        prefix = playlist_key.rsplit("/", 1)[0]  # .../hls
        keys = await storage.list(prefix)
        if not keys:
            return None
        hls_files: dict[str, bytes] = {}
        for k in keys:
            rel = k[len(prefix) + 1:]  # strip "prefix/"
            if rel:
                hls_files[rel] = await storage.get(k)
        combined_bytes = await asyncio.to_thread(muxer, hls_files)
        if not combined_bytes:
            return None
        await storage.upload(combined_key, combined_bytes, content_type="video/mp4")

    def _stamp(recs):
        r = next((x for x in recs if x.get("id") == recording_id), None)
        if r is None:
            return recs, None
        mfs = r.setdefault("media_files", [])
        cmf = next((m for m in mfs if m.get("type") == "combined"), None)
        if cmf is None:
            cmf = {"id": new_recording_numeric_id(), "type": "combined"}
            mfs.append(cmf)
        cmf.update({
            "format": "mp4",
            "storage_path": combined_key,
            "is_final": True,
            "finalized_at": _now_iso(),
            "finalized_by": "recording_finalizer.hls_download",
        })
        pb = dict(r.get("playback_url") or {})
        pb["combined"] = f"/recordings/{recording_id}/master?type=combined"
        r["playback_url"] = pb
        others = [x for x in recs if x.get("id") != recording_id]
        return others + [r], combined_key

    return await repo.mutate_recordings(meeting_id, _stamp)


async def hls_download_ready_key(
    repo: RecordingRepo,
    storage: Storage,
    *,
    meeting_id: int,
    recording_id: int,
) -> Optional[str]:
    """Fast, NO-MUX check: return the combined-download key IF it already exists in storage (stamping
    its synthetic media-file so /raw serves it), else None. The READ path uses this so a page load never
    blocks on (or restarts) the seconds-to-minutes ffmpeg remux — that runs in the background via
    ``finalize_hls_download``, which short-circuits on ``storage.exists`` (so this only stamps)."""
    recordings = await repo.get_recordings(meeting_id)
    rec = next((r for r in recordings if r.get("id") == recording_id), None)
    hmf = next((m for m in (rec or {}).get("media_files", []) if m.get("type") == "hls"), None) if rec else None
    playlist_key = (hmf or {}).get("storage_path")
    if not playlist_key:
        return None
    if not await storage.exists(_hls_download_key(playlist_key)):
        return None
    return await finalize_hls_download(repo, storage, meeting_id=meeting_id, recording_id=recording_id)
