"""The recordings routes — mounted onto the unified meeting-api app (modular monolith, P2).

  * **POST /internal/recordings/upload** — the bot's chunk upload. Auth via the MeetingToken it
    carries (``Authorization: Bearer <token>``, re-verified here — the parent's
    ``require_recording_upload_token``), which names the meeting and the session it may write; the
    internal tier is also accepted. Multipart form: ``file`` + ``session_uid`` + media metadata.
    Folds the chunk into ``meeting.data['recordings']`` JSONB. ``include_in_schema=False`` (internal).
  * **GET /recordings** — the caller's recordings (from ``meeting.data``), scoped by the
    gateway-injected ``x-user-id``.
  * **GET /recordings/{recording_id}/master?type=audio|video** — finalize-on-read: build + upload the
    master if absent or behind its chunks, then return its storage key and the raw byte route.
  * **GET /recordings/{recording_id}/media/{media_file_id}/raw** — the master's bytes, STREAMED from
    object storage in bounded pieces; honors ``Range`` (206 / 416). A master the media file's own
    counters show as current is served without finalize; a read writes nothing unless the master
    changed.
"""
from __future__ import annotations

import hmac
import json
import os
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ..meeting_token import InvalidMeetingToken, admit_session
from ..regime import require_person
from .ports import RecordingRepo, Storage
from .deletion import MeetingNotTerminal, delete_owned_recording
from .jsonb import RECORDING_MEDIA_FORMATS, RECORDING_MEDIA_TYPES
from .service import (
    SIGNAL_MEDIA_TYPE,
    InvalidSignalTape,
    SessionNotFound,
    finalize_master,
    master_is_current,
    upload_chunk,
    upload_signal_tape,
)


#: Default page size for ``GET /recordings``. The route used to have none: it answered with every
#: recording the account had ever made, full detail, oldest first — 201 rows and 1.6 MB for one
#: dogfooding account, straight into a calling model's context through the MCP's `list_recordings`
#: (fr_db203061a7a1d953). A default is what makes the first call cheap for a caller who did not
#: know to ask; the cap is what keeps it cheap for one who asks for too much.
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200

#: Per-media-file keys the LIST row keeps. Everything else on a media file describes HOW the bytes
#: were assembled — `chunk_seq`, `chunk_count`, `last_chunk_size_bytes`, `first_chunk_at`,
#: `storage_path`, `storage_backend`, `is_final`, `finalized_at/by`, `metadata` — which is upload
#: bookkeeping no list renders and no caller can act on. It stays whole on
#: ``GET /recordings/{id}``, which is where a caller goes when they want one recording.
LIST_MEDIA_FILE_KEYS = ("id", "type", "format", "duration_seconds", "file_size_bytes", "capture_started_at_ms")

#: Top-level keys the LIST row keeps. `playback_url` is the one a client actually follows, so it
#: stays; `user_id`/`session_uid`/`source` identify the producer, not the recording, and the
#: caller already knows they are themselves.
LIST_RECORDING_KEYS = (
    "id", "meeting_id", "status", "created_at", "completed_at", "playback_url",
    "deletion_pending",
)


def _list_sort_key(rec: dict) -> tuple:
    """NEWEST FIRST, deterministically.

    There is no recordings table — recordings live inside ``meeting.data`` JSONB (see
    ``recordings/jsonb.py``), so this cannot be an ``ORDER BY`` and the stored order is worse than
    arbitrary: ``service.py`` appends the freshest recording to the TAIL of its meeting's list, so
    the natural order is oldest-first. `created_at` is a UTC ISO-8601 string from ``_now_iso()``, so
    lexicographic ordering IS chronological ordering; the id breaks ties so a page boundary does not
    move between two calls.
    """
    return (str(rec.get("created_at") or ""), rec.get("id") or 0)


def _project_list_recording(rec: dict) -> dict:
    """One recording, shaped for a LIST row.

    ``duration_seconds`` is lifted to the top level as the longest of the recording's media files —
    a list wants "how long is this", and the answer otherwise only exists per media file. The audio
    and video tracks of one recording are the same take, so the longest is the recording's length.
    """
    out = {k: rec[k] for k in LIST_RECORDING_KEYS if k in rec}
    media = rec.get("media_files")
    media = media if isinstance(media, list) else []
    out["media_files"] = [
        {k: m[k] for k in LIST_MEDIA_FILE_KEYS if k in m}
        for m in media if isinstance(m, dict)
    ]
    durations = [m.get("duration_seconds") for m in media
                 if isinstance(m, dict) and isinstance(m.get("duration_seconds"), (int, float))]
    out["duration_seconds"] = max(durations) if durations else None
    return out


def _bearer_token(authorization: Optional[str]) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing recording upload token")
    return authorization.split(" ", 1)[1].strip()


def _upload_scope(authorization: Optional[str], session_uid: str,
                  token_secret: Optional[str]) -> Optional[int]:
    """Authenticate one upload. Returns the MeetingToken's meeting id (which the service checks
    against the session's meeting), or ``None`` for the internal tier, which is scoped by the
    session alone. A MeetingToken admits only the session it is bound to
    (``meeting_token.admit_session``, the rule the lifecycle callback applies too). Raises 401."""
    bearer = _bearer_token(authorization)
    internal_secret = os.getenv("INTERNAL_API_SECRET") or ""
    if internal_secret and bearer and hmac.compare_digest(bearer.encode(), internal_secret.encode()):
        return None
    try:
        claims = admit_session(bearer, session_uid=session_uid, secret=token_secret)
        return int(claims["meeting_id"])
    except (InvalidMeetingToken, KeyError, TypeError, ValueError) as e:
        raise HTTPException(status_code=401, detail=f"Invalid recording upload token: {e}")


def _attachment_name(rec: dict, mf: dict) -> str:
    """The filename a ``?download=1`` read is saved under: ``meeting-<id>-<date>-<type>.<ext>``.

    Built only from server-owned fields (ids, the recording's ``created_at`` date and the media
    file's own ``format``), never from anything the caller sent, so the header cannot be steered.
    The extension is the real container's; an unknown format falls back to ``bin`` rather than
    lying about what the bytes are."""
    fmt = str(mf.get("format") or "")
    ext = fmt if fmt.isalnum() and len(fmt) <= 8 else "bin"
    kind = "video" if mf.get("type") == "video" else "audio"
    date = str(rec.get("created_at") or "")[:10]
    date_part = f"-{date}" if len(date) == 10 and date[4] == "-" and date[7] == "-" and \
        date.replace("-", "").isdigit() else ""
    return f"meeting-{int(rec['meeting_id'])}{date_part}-{kind}.{ext}"


def _resolve_user_id(x_user_id: Optional[str]) -> int:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing user identity")
    try:
        return int(x_user_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid user identity")


def _parse_range(range_header: Optional[str], total: int) -> Optional[tuple[int, int]]:
    """Parse an HTTP ``Range: bytes=start-end`` header against a known total size.

    Returns the resolved INCLUSIVE ``(start, end)`` byte offsets, or ``None`` when there is no
    range / it is not a byte range we honor (caller serves the full 200 body). Raises
    ``HTTPException(416)`` for a syntactically valid but unsatisfiable range. Forms handled:
    ``bytes=start-end``, ``bytes=start-`` (to EOF), ``bytes=-suffix`` (last N bytes); a multi-range
    header (commas) honors only the FIRST range.
    """
    if not range_header:
        return None
    spec = range_header.strip()
    if not spec.lower().startswith("bytes="):
        return None  # only byte ranges; fall back to full body
    spec = spec[len("bytes="):]
    first = spec.split(",", 1)[0].strip()  # multi-range → honor the first
    if "-" not in first:
        return None
    start_s, _, end_s = first.partition("-")
    start_s, end_s = start_s.strip(), end_s.strip()
    try:
        if start_s == "":
            # bytes=-suffix → the last `suffix` bytes
            if end_s == "":
                return None
            suffix = int(end_s)
            if suffix <= 0:
                return None
            start = max(0, total - suffix)
            end = total - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s != "" else total - 1
    except ValueError:
        return None  # unparseable → fall back to full body
    if start < 0:
        return None
    if start >= total:
        # Syntactically valid but unsatisfiable → 416 with Content-Range: bytes */total.
        raise HTTPException(
            status_code=416,
            detail="Requested range not satisfiable",
            headers={"Content-Range": f"bytes */{total}", "Accept-Ranges": "bytes"},
        )
    end = min(end, total - 1)  # clamp to the last byte
    if end < start:
        return None  # e.g. bytes=5-3 → ignore, serve full body
    return start, end


def _media_content_type(mf: dict) -> str:
    media_format = mf.get("format", "webm")
    if media_format == "wav":
        return "audio/wav"
    if media_format == "webm":
        return "audio/webm" if mf.get("type") == "audio" else "video/webm"
    return "application/octet-stream"


def build_router(
    repo: RecordingRepo,
    storage: Storage,
    *,
    token_secret: Optional[str] = None,
) -> APIRouter:
    """The recordings routes over the injected ``RecordingRepo`` + ``Storage`` ports."""

    async def _readable(user_id: int, request: Request, *, shared: bool = True) -> list:
        """The recordings this caller may READ: their own, plus the recordings of meetings shared with
        them whose owner allowed recipients the recording (``shared_access``) — a transcript-share
        recipient or a member of the bound workspace. ``shared=False`` keeps exactly the caller's own
        (the account-wide list stays "my recordings"). Read off ``request`` so the route signatures,
        which other changes also touch, stay as they are."""
        recs = await repo.list_meeting_recordings(user_id)
        if not shared:
            return recs
        workspaces = {w.strip() for w in (request.headers.get("x-user-workspaces") or "").split(",")
                      if w.strip()}
        raw_mid = request.query_params.get("meeting_id")
        meeting_id = int(raw_mid) if raw_mid and raw_mid.isdigit() else None
        own = {r.get("meeting_id") for r in recs}
        extra = await repo.list_shared_recordings(user_id, workspaces, meeting_id=meeting_id)
        return recs + [r for r in extra if r.get("meeting_id") not in own]
    router = APIRouter()

    @router.post("/internal/recordings/upload", include_in_schema=False)
    async def internal_upload_recording(
        file: UploadFile = File(...),
        session_uid: Optional[str] = Form(None),
        media_type: Optional[str] = Form(None),
        media_format: Optional[str] = Form(None),
        chunk_seq: Optional[int] = Form(None),
        is_final: Optional[bool] = Form(None),
        duration_seconds: Optional[float] = Form(None),
        sample_rate: Optional[int] = Form(None),
        metadata: Optional[str] = Form(None),
        authorization: Optional[str] = Header(default=None),
    ):
        # The bot's RecordingService sends a JSON `metadata` part + the `file` (with flat chunk_seq/
        # is_final on chunk uploads). Parse `metadata` for the fields it carries (session_uid lives
        # only there); any flat Form field overrides its metadata counterpart.
        meta: dict = {}
        if metadata:
            try:
                meta = json.loads(metadata)
            except (ValueError, TypeError):
                meta = {}
        session_uid = session_uid or meta.get("session_uid")
        if not session_uid:
            raise HTTPException(status_code=422, detail="session_uid required (flat field or metadata)")
        media_type = media_type or meta.get("media_type") or "audio"
        media_format = media_format or meta.get("media_format") or meta.get("format") or "wav"
        # O-TEL-1 — a captured-signal tape rides this same endpoint (one internal upload edge, one
        # set of credentials, one network path already open from every bot) but takes a DIFFERENT
        # server path: no JSONB fold, no chunking, no master. See service.upload_signal_tape.
        if media_type == SIGNAL_MEDIA_TYPE:
            part = str(meta.get("part") or "")
            token_meeting_id = _upload_scope(authorization, session_uid, token_secret)
            try:
                receipt = await upload_signal_tape(
                    repo, storage,
                    token_meeting_id=token_meeting_id,
                    session_uid=session_uid, data=await file.read(),
                    part=part, media_format=media_format,
                )
            except InvalidSignalTape as e:
                raise HTTPException(status_code=422, detail=str(e))
            except SessionNotFound as e:
                raise HTTPException(status_code=404, detail=str(e))
            return JSONResponse(content=receipt)
        # Both become path segments of the chunk's object key: only the media a bot records reach it.
        if media_type not in RECORDING_MEDIA_TYPES or media_format not in RECORDING_MEDIA_FORMATS:
            raise HTTPException(
                status_code=422,
                detail=(f"media_type must be one of {list(RECORDING_MEDIA_TYPES)} (or {SIGNAL_MEDIA_TYPE!r} "
                        f"for a signal tape) and media_format one of {list(RECORDING_MEDIA_FORMATS)}"),
            )
        chunk_seq = chunk_seq if chunk_seq is not None else int(meta.get("chunk_seq", 0) or 0)
        is_final = is_final if is_final is not None else bool(meta.get("is_final", True))
        duration_seconds = duration_seconds if duration_seconds is not None else meta.get("duration_seconds")
        sample_rate = sample_rate if sample_rate is not None else meta.get("sample_rate")

        # Auth: the bot's MeetingToken (its meeting, and the session it is bound to), or the internal
        # tier (scoped by session_uid).
        token_meeting_id = _upload_scope(authorization, session_uid, token_secret)

        data = await file.read()
        try:
            receipt = await upload_chunk(
                repo, storage,
                token_meeting_id=token_meeting_id,
                session_uid=session_uid, data=data,
                media_type=media_type, media_format=media_format,
                chunk_seq=chunk_seq, is_final=is_final,
                duration_seconds=duration_seconds, sample_rate=sample_rate,
                capture_started_at_ms=meta.get("capture_started_at_ms"),
            )
        except SessionNotFound as e:
            raise HTTPException(status_code=404, detail=str(e))
        return JSONResponse(content=receipt)

    @router.get("/recordings")
    async def list_recordings(
        request: Request,
        x_user_id: Optional[str] = Header(default=None),
        limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
        offset: int = Query(default=0, ge=0),
        meeting_id: Optional[int] = Query(default=None,
                                          description="Only this meeting's recordings."),
    ):
        """The caller's recordings, NEWEST FIRST, one page at a time, in the list shape.

        Every one of those three was missing, and the gateway and the MCP tool had been forwarding
        `limit`/`offset`/`meeting_id` into a route that declared none of them — FastAPI drops an
        undeclared query parameter in silence, so `list_recordings(limit=3)` answered with all 201
        recordings, 1.6 MB, oldest first, each carrying its per-chunk upload bookkeeping. A page
        argument that is accepted and ignored is worse than one that is refused: the caller has no
        way to tell the answer is not the one they asked for.

        The read behind this is still the whole account (there is no recordings table — they live
        in `meeting.data` JSONB), so the sort and the slice happen here. That is unchanged and out
        of scope; what changes is that the RESPONSE is bounded, which is what reaches a caller.
        """
        user_id = _resolve_user_id(x_user_id)
        recs = await _readable(user_id, request, shared=meeting_id is not None)
        if meeting_id is not None:
            recs = [r for r in recs if r.get("meeting_id") == meeting_id]
        recs = sorted(recs, key=_list_sort_key, reverse=True)
        total = len(recs)
        page = recs[offset:offset + limit]
        return JSONResponse(content={
            "recordings": [_project_list_recording(r) for r in page],
            # Named so a caller can page without guessing. `total` is honest here because the read
            # already materialized every row; it is not a promise that a future paged read will
            # still be able to count for free.
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(page) < total,
        })

    @router.get("/recordings/{recording_id}")
    async def get_recording(
        recording_id: int,
        request: Request,
        x_user_id: Optional[str] = Header(default=None),
    ):
        """Get a single recording and its media files. 404 if the id is not one of your recordings."""
        user_id = _resolve_user_id(x_user_id)
        recs = await _readable(user_id, request)
        rec = next((r for r in recs if r.get("id") == recording_id), None)
        if rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        return JSONResponse(content=rec)

    @router.delete("/recordings/{recording_id}", dependencies=[Depends(require_person)])
    async def delete_recording(
        recording_id: int,
        x_user_id: Optional[str] = Header(default=None),
    ):
        """Delete one owned recording's primary-storage objects and JSONB metadata.

        Unknown and unowned ids both return 404. Storage is deleted first so a backend failure
        leaves the metadata/path available for a retry rather than orphaning an undiscoverable
        object. Backup expiry remains a deployment retention concern, not a claim of this route.
        """
        user_id = _resolve_user_id(x_user_id)
        try:
            receipt = await delete_owned_recording(
                repo, storage, user_id=user_id, recording_id=recording_id
            )
        except MeetingNotTerminal:
            raise HTTPException(
                status_code=409,
                detail="Recording can only be deleted after the meeting lifecycle is terminal",
            )
        if receipt is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        return JSONResponse(content=receipt)

    @router.get("/recordings/{recording_id}/master")
    async def get_recording_master(
        recording_id: int,
        request: Request,
        type: str = "audio",
        x_user_id: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        # Find which meeting owns this recording (scoped to the caller).
        recs = await _readable(user_id, request)
        rec = next((r for r in recs if r.get("id") == recording_id), None)
        if rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        mf = next((m for m in rec.get("media_files", []) if m.get("type") == type), None)
        master_key = await finalize_master(
            repo, storage, meeting_id=rec["meeting_id"], recording_id=recording_id, media_type=type
        )
        if master_key is None:
            raise HTTPException(status_code=404, detail="No such media file to finalize")
        # The dashboard player (api.ts getRecordingMasterStreamUrl) reads ``raw_url`` and streams it via
        # the proxy — the master metadata (``storage_path``) alone is not playable. Point it at the byte
        # route below so playback actually resolves (recordings P3: master byte stream).
        media_file_id = (mf or {}).get("id")
        raw_url = (
            f"/recordings/{recording_id}/media/{media_file_id}/raw?type={type}"
            if media_file_id is not None
            else None
        )
        return JSONResponse(content={
            "id": recording_id,
            "type": type,
            "storage_path": master_key,
            "media_file_id": media_file_id,
            "raw_url": raw_url,
            "duration_seconds": (mf or {}).get("duration_seconds"),
        })

    @router.get("/recordings/{recording_id}/media/{media_file_id}/raw")
    async def get_recording_media_raw(
        recording_id: int,
        media_file_id: int,
        request: Request,
        type: str = "audio",
        download: bool = Query(default=False, description=(
            "Ask for the bytes as a file to save: adds `Content-Disposition: attachment`. Same "
            "ownership check as playback; nothing else about the answer changes.")),
        x_user_id: Optional[str] = Header(default=None),
    ):
        # Stream the finalized master bytes from object storage (recordings P3). The player fetches
        # /master first (which finalizes), then this; finalize-on-read here too as a safety net.
        user_id = _resolve_user_id(x_user_id)
        recs = await _readable(user_id, request)
        rec = next((r for r in recs if r.get("id") == recording_id), None)
        if rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        mf = next(
            (m for m in rec.get("media_files", []) if str(m.get("id")) == str(media_file_id)),
            None,
        )
        if mf is None:
            raise HTTPException(status_code=404, detail="No such media file")
        # Serve the ASSEMBLED master, never a raw part or the empty final-signal chunk: the ONLY
        # playable object is master.<fmt>. When the media file's own counters say that master
        # already holds every folded chunk (``master_is_current``: assembled_chunk_count ==
        # chunk_count, both bumped under the row lock), the read serves it as it stands: no chunk
        # listing, no row lock, no write. Otherwise ``finalize_master`` (re)assembles first (#768),
        # and it writes only when something changed. The freshness signal is the chunk COUNT, not
        # the master's existence: a guard that skipped finalize once storage_path named the master
        # key froze a mid-meeting partial forever (#768); any chunk folded after assembly bumps
        # chunk_count and drops assembled_chunk_count, so this read reassembles.
        if master_is_current(mf):
            storage_path = mf["storage_path"]
        else:
            storage_path = await finalize_master(
                repo, storage, meeting_id=rec["meeting_id"], recording_id=recording_id,
                media_type=mf.get("type", type),
            )
            if not storage_path:
                # Deleted while this read ran, or nothing to assemble: there is no master to serve.
                raise HTTPException(status_code=404, detail="Media file has no master")
        content_type = _media_content_type(mf)

        # Honor HTTP Range so the <audio>/<video> element + dashboard proxy can seek without
        # downloading the whole master. The size comes from a HEAD; the bytes are STREAMED from
        # object storage in bounded pieces (``Storage.open_range``), so neither a 200 nor a 206
        # holds the object in this process: memory per read is one piece, whatever the file size.
        range_header = request.headers.get("range") or request.headers.get("Range")
        total = await storage.size(storage_path)

        # A save, not a play: the same owner-scoped bytes, marked as a file. Set only on request so
        # the <audio> element's reads stay inline.
        disposition = (
            {"Content-Disposition": f'attachment; filename="{_attachment_name(rec, mf)}"'}
            if download else {}
        )

        rng = _parse_range(range_header, total)  # may raise 416
        if rng is None:
            start, end, status, headers = 0, total - 1, 200, {}
        else:
            start, end = rng
            status, headers = 206, {"Content-Range": f"bytes {start}-{end}/{total}"}
        length = end - start + 1
        headers = {"Accept-Ranges": "bytes", **headers, "Content-Length": str(length), **disposition}
        if length <= 0:
            return Response(content=b"", status_code=status, media_type=content_type, headers=headers)
        # Opened BEFORE the response starts, so a storage fault is a 5xx, not a cut-off body.
        pieces = await storage.open_range(storage_path, start, end)
        return StreamingResponse(pieces, status_code=status, media_type=content_type, headers=headers)

    return router
