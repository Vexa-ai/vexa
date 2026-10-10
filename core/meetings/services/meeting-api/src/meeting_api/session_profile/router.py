"""``PUT /internal/browser-session/{session_uid}`` — an authenticated bot's session write-back.

The bots' storage key is read-only, so this route is the only way a bot's rotated session reaches
the store. Four checks, in this order, each before the next does any work:

1. **The MeetingToken** — ``Authorization: Bearer <token>`` admitted for exactly ``session_uid``
   (``meeting_token.admit_session``, the rule the lifecycle callback and the uploads apply). 401.
2. **Authenticated mode** — ``BOT_AUTHENTICATED`` with a complete store (``auth_session_config``).
   Off: 409. Misconfigured, or the bots' pair reusing a storage root key: 503.
3. **The live authenticated bot** — the token's session must be the newest session spawned on the
   deployment's identity (``MeetingRepo.latest_auth_session``) and belong to the token's meeting;
   its meeting must be live, or have ended less than ``WRITEBACK_GRACE_S`` ago (the bot writes back
   on teardown, after its terminal callback). A bot superseded by a later spawn, or one whose
   meeting ended long ago, is refused. 403.
4. **The body** — at most ``MAX_BODY_BYTES`` (413), then ``parse_profile_upload``: SESSION_PROFILE
   paths only, no traversal, no extra fields, each file and the whole within the limits. One bad
   entry refuses the whole body (422) and nothing is written.

Then each file is stored at ``<BOT_USERDATA_S3_PATH>/browser-data/<path>`` with meeting-api's own
storage credentials (``writer.S3SessionWriter``). ``include_in_schema=False``: internal, bots only;
the gateway routes nothing here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional

from fastapi import APIRouter, Header, HTTPException, Request

from ..bot_spawn import AuthSessionConfig, AuthSessionNotConfigured, auth_session_config
from ..meeting_token import InvalidMeetingToken, admit_session
from ..obs import log_event
from .profile import MAX_BODY_BYTES, InvalidSessionProfile, parse_profile_upload
from .writer import S3SessionWriter, SessionWriter

SESSION_WRITEBACK_ROUTE = "/internal/browser-session/{session_uid}"

#: How long after its meeting ended a session may still write back. The bot's teardown runs after
#: its terminal callback (pipeline stop, tape upload, then the browser closes), so the write-back
#: arrives after the meeting is terminal; past this, a token that outlived its bot writes nothing.
WRITEBACK_GRACE_S = 600.0

_TERMINAL = ("completed", "failed")


def _bearer(authorization: Optional[str]) -> str:
    if not authorization:
        return ""
    scheme, _, value = authorization.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _read_capped(request: Request, cap: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        raise HTTPException(status_code=413, detail=f"the write-back body is larger than {cap} bytes")
    buf = bytearray()
    async for chunk in request.stream():
        buf.extend(chunk)
        if len(buf) > cap:
            raise HTTPException(status_code=413, detail=f"the write-back body is larger than {cap} bytes")
    return bytes(buf)


def _default_writer(cfg: AuthSessionConfig) -> SessionWriter:
    return S3SessionWriter.from_env(endpoint_url=cfg.s3_endpoint, bucket=cfg.s3_bucket)


def build_router(
    meeting_repo: Any,
    *,
    token_secret: Optional[str] = None,
    writer_factory: Optional[Callable[[AuthSessionConfig], SessionWriter]] = None,
    clock: Optional[Callable[[], datetime]] = None,
) -> APIRouter:
    """The write-back route over the meeting repo (``latest_auth_session``) and a session writer."""
    router = APIRouter()
    now = clock or (lambda: datetime.now(timezone.utc))
    make_writer = writer_factory or _default_writer

    @router.put(SESSION_WRITEBACK_ROUTE, include_in_schema=False)
    async def write_back_session(
        session_uid: str,
        request: Request,
        authorization: Optional[str] = Header(default=None),
    ):
        # 1. the MeetingToken, for exactly this session
        token = _bearer(authorization)
        if not token:
            raise HTTPException(status_code=401, detail="a session write-back needs the bot's MeetingToken")
        try:
            claims = admit_session(token, session_uid=session_uid, secret=token_secret)
            meeting_id = int(claims["meeting_id"])
        except (InvalidMeetingToken, KeyError, TypeError, ValueError) as e:
            raise HTTPException(status_code=401, detail=f"invalid session token: {e}")

        # 2. authenticated mode, configured
        try:
            cfg = auth_session_config()
        except AuthSessionNotConfigured as e:
            raise HTTPException(status_code=503, detail=str(e))
        if cfg is None:
            raise HTTPException(status_code=409,
                                detail="authenticated-bot mode is off: there is no stored session to write")

        # 3. the live authenticated bot
        latest = await meeting_repo.latest_auth_session(cfg.userdata_path)
        if (
            latest is None
            or latest.get("session_uid") != session_uid
            or int(latest.get("meeting_id") or 0) != meeting_id
        ):
            log_event("session_writeback_refused", audience="system", level="warning",
                      span="session.writeback", meeting_id=str(meeting_id),
                      fields={"reason": "not the live authenticated session"})
            raise HTTPException(status_code=403,
                                detail="this session is not the live authenticated bot on this deployment")
        if latest.get("status") in _TERMINAL:
            ended = _parse_ts(latest.get("end_time") or latest.get("updated_at"))
            if ended is None or (now() - ended).total_seconds() > WRITEBACK_GRACE_S:
                log_event("session_writeback_refused", audience="system", level="warning",
                          span="session.writeback", meeting_id=str(meeting_id),
                          fields={"reason": "meeting ended past the write-back window"})
                raise HTTPException(
                    status_code=403,
                    detail=f"this session's meeting ended more than {int(WRITEBACK_GRACE_S)}s ago")

        # 4. the body: session profile files only
        raw = await _read_capped(request, MAX_BODY_BYTES)
        try:
            files = parse_profile_upload(raw)
        except InvalidSessionProfile as e:
            log_event("session_writeback_refused", audience="system", level="warning",
                      span="session.writeback", meeting_id=str(meeting_id), fields={"reason": str(e)})
            raise HTTPException(status_code=422, detail=str(e))

        prefix = f"{cfg.userdata_path.strip('/')}/browser-data"
        writer = make_writer(cfg)
        written = 0
        try:
            for path, data in files:
                await writer.put(f"{prefix}/{path}", data)
                written += 1
        except Exception as e:  # noqa: BLE001 — any store failure is one attributed 502
            log_event("session_writeback_store_failed", audience="system", level="error",
                      span="session.writeback", meeting_id=str(meeting_id),
                      fields={"written": written, "of": len(files), "error": type(e).__name__})
            raise HTTPException(status_code=502, detail="the session store did not accept the write-back")
        total = sum(len(d) for _, d in files)
        log_event("session_writeback_stored", audience="system", span="session.writeback",
                  meeting_id=str(meeting_id), fields={"files": written, "bytes": total})
        return {"written": written, "bytes": total}

    return router
