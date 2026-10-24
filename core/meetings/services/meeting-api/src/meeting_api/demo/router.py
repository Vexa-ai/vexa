"""``POST /demo-meetings`` — the MCP tool ``start_demo_meeting``.

A new person with no meeting right now still needs to see a live transcript in their first minutes.
This route gives them a meeting to watch: a fresh call room on the deployment's own LiveKit server
(``calls``), nobody to host it and nobody to admit anyone, and the person's own listener in it, as on
any call. The same listener also plays two scripted speakers into the room (invocation.v1
``call.presenter``; the script ships in the bot image, ``demo/demo-v1/``), so within seconds
the person's transcript fills with a short conversation, attributed to each speaker by name. When
the script ends the speakers leave and the call ends as an emptied room; the meeting completes like
any other.

* **Identity.** The speakers' tokens are minted here with the deployment's own LiveKit key
  (``calls.presenter_token``): this room only, publish audio, ten minutes. No person's credential is
  involved; the listener is the person's bot.
* **Marked.** The row carries ``data.demo = true``. Activation, usage and any count of real meetings
  exclude it on that field.
* **Free.** The service authority is not consulted: a demo is not admitted as billable service and
  gets no per-minute boundary. Its STT runs on the person's resolved backend as on any meeting (about
  a minute of audio).
* **Bounded.** ``DEMO_MEETINGS_PER_DAY`` demos per account in any 24 hours (default 3; 0 turns the
  route off), counted on the meeting rows; the call is capped at ``DEMO_MAX_DURATION_SEC`` (default
  300). It also takes one of the person's concurrent bot slots while it runs.
* **Events.** ``demo_started`` here and ``demo_completed`` when the call's meeting ends
  (:func:`demo_ended`), each one ``logevent.v1`` line with ``user_id`` and ``meeting_id``. A hosted
  deployment maps them to its product analytics; this domain names no analytics vendor.

Without calls (no ``LIVEKIT_URL``) the route answers ``503 demo_not_available``.
"""
from __future__ import annotations

import dataclasses
import os
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse

from ..bot_spawn import (
    DuplicateMeeting,
    MaxBotsExceeded,
    MeetingRepo,
    QuotaExceeded,
    RuntimeClient,
    SpawnFailed,
    TranscriptionNotConfigured,
    request_bot,
    resolve_max_concurrent,
)
from ..calls import PLATFORM as CALL_PLATFORM
from ..calls import CallLimits, discard_call, prepare_call, presenter_token, record_meeting
from ..obs import log_event
from ..regime import require_person

DEMO_SCRIPT = "demo-v1"
#: The voices of ``demo/demo-v1/script.json`` in the bot image, in the order they join.
DEMO_SPEAKERS = ("Maya", "Leo")
DEFAULT_PER_DAY = 3
DEFAULT_MAX_DURATION_SEC = 300
PRESENTER_TTL_SEC = 600

INSTRUCTION = (
    "A demo meeting started: two scripted speakers are talking for about a minute and your "
    "transcription bot is transcribing them. Read it live with get_meeting_transcript(platform, "
    "native_meeting_id), passing since_index to fetch only new segments. The meeting ends by itself "
    "when the script ends. It is a demo: say so, and do not treat it as one of the person's meetings."
)


def demos_per_day() -> int:
    raw = (os.getenv("DEMO_MEETINGS_PER_DAY") or "").strip()
    value = int(raw) if raw else DEFAULT_PER_DAY
    if value < 0:
        raise ValueError(f"DEMO_MEETINGS_PER_DAY must be at least 0, got {value}")
    return value


def _max_duration_sec() -> int:
    raw = (os.getenv("DEMO_MAX_DURATION_SEC") or "").strip()
    value = int(raw) if raw else DEFAULT_MAX_DURATION_SEC
    if value < 60:
        raise ValueError(f"DEMO_MAX_DURATION_SEC must be at least 60, got {value}")
    return value


async def demo_ended(meeting_row: dict) -> None:
    """The ``demo_completed`` event, for a demo call's meeting that has ended. Never raises."""
    if not isinstance(meeting_row, dict) or not (meeting_row.get("data") or {}).get("demo"):
        return
    log_event("demo_completed", audience="user", span="demo.end", user_id=meeting_row.get("user_id"),
              meeting_id=str(meeting_row.get("id")),
              fields={"platform": meeting_row.get("platform"),
                      "native_meeting_id": meeting_row.get("native_meeting_id")
                      or meeting_row.get("platform_specific_id"),
                      "status": meeting_row.get("status")})


def build_router(
    repo: MeetingRepo,
    runtime: RuntimeClient,
    *,
    calls=None,
    transcript_stream_purge: "Optional[Callable[[int], Awaitable[None]]]" = None,
    redis_grant: "Optional[Callable[[str, int], Awaitable[str]]]" = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc).replace(tzinfo=None),
) -> APIRouter:
    router = APIRouter()

    @router.post("/demo-meetings", status_code=201, dependencies=[Depends(require_person)])
    async def start_demo_meeting(
        x_user_id: Optional[str] = Header(default=None),
        x_user_limits: Optional[str] = Header(default=None),
    ):
        """Start a demo meeting the person can watch being transcribed live, when they have no
        meeting of their own right now (or just want to see how Vexa works). Nothing to paste and
        nobody to admit: Vexa opens a room, two scripted speakers talk for about a minute, and the
        person's own transcription bot transcribes them. Returns `platform` and `native_meeting_id`:
        read the transcript with get_meeting_transcript while it runs (the first lines arrive within
        seconds). The meeting ends by itself when the script ends. A few per account per day;
        demos are free and are marked as demos (`data.demo`)."""
        if not x_user_id or not x_user_id.strip().isdigit():
            raise HTTPException(status_code=401, detail="Missing user identity")
        user_id = int(x_user_id)
        if calls is None:
            raise HTTPException(status_code=503, detail={
                "error": "demo_not_available",
                "message": "demo meetings need calls, and this deployment runs no LiveKit server"})
        per_day = demos_per_day()
        used = await repo.count_demo_meetings(user_id=user_id, since=now() - timedelta(hours=24))
        if used >= per_day:
            raise HTTPException(status_code=429, detail={
                "error": "demo_limit_reached", "limit": per_day,
                "message": f"{per_day} demo meetings per day; try Vexa on a real meeting instead "
                           "(request_meeting_bot with its link)"})

        limits = dataclasses.replace(CallLimits.from_env(), max_duration_sec=_max_duration_sec())
        prepared = await prepare_call(calls, user_id=user_id, session_id=None, transcribe=True, limits=limits)
        room = prepared.native_meeting_id
        invocation = {**prepared.invocation, "presenter": {
            "script": DEMO_SCRIPT,
            "speakers": [{"name": n, "token": presenter_token(calls.config, room=room, name=n,
                                                              ttl_sec=PRESENTER_TTL_SEC)}
                         for n in DEMO_SPEAKERS],
        }}
        try:
            meeting = await request_bot(
                repo, runtime,
                authority=None,
                user_id=user_id,
                platform=CALL_PLATFORM,
                native_meeting_id=room,
                transcribe_enabled=True,
                recording_enabled=False,
                max_concurrent=resolve_max_concurrent(x_user_limits),
                transcript_stream_purge=transcript_stream_purge,
                redis_grant=redis_grant,
                call_invocation=invocation,
                call_data={**prepared.data, "demo_script": DEMO_SCRIPT},
                demo=True,
            )
        except Exception as e:
            await discard_call(calls, prepared)
            if isinstance(e, (MaxBotsExceeded, QuotaExceeded)):
                raise HTTPException(status_code=429, detail=str(e) or "Bot concurrency limit reached")
            if isinstance(e, TranscriptionNotConfigured):
                raise HTTPException(status_code=503, detail=str(e))
            if isinstance(e, DuplicateMeeting):
                raise HTTPException(status_code=409, detail=str(e))
            if isinstance(e, SpawnFailed):
                raise HTTPException(status_code=502, detail=str(e) or "Failed to start bot workload")
            raise
        await record_meeting(calls, prepared, int(meeting["id"]))
        log_event("demo_started", audience="user", span="demo.start", user_id=user_id,
                  meeting_id=str(meeting["id"]),
                  fields={"platform": CALL_PLATFORM, "native_meeting_id": room, "script": DEMO_SCRIPT,
                          "demos_today": used + 1})
        return JSONResponse(status_code=201, content={
            **meeting,
            "demo": {"script": DEMO_SCRIPT, "speakers": list(DEMO_SPEAKERS), "expected_duration_sec": 60,
                     "demos_left_today": max(0, per_day - used - 1)},
            "instruction": INSTRUCTION,
        })

    return router
