"""The ``POST /bots`` core flow — port of the parent ``meetings.request_bot`` happy path.

CORE PATH ONLY (P2). The behavioral follow-ons are P3 and explicitly left as seams here:
  * continue_meeting (reuse a stopping meeting) — NOT handled; a new spawn always.
  * max-bots / concurrency quota — surfaced ONLY where the runtime kernel raises it (429); the
    meeting-api side does no pre-check (parent's ``max_concurrent`` gate is P3).
  * join-retry / bot-timeout scheduling — NOT handled (the scheduling brick wires that in P3).

The flow (parent ``meetings.py`` lines ~1010-1403, reduced to the standard-bot branch):
  1. construct the meeting URL (or use the supplied one),
  2. dedup — 409 if the user already has an active/requested meeting for (platform, native_id),
  3. insert the ``Meeting`` row (status ``requested``) → meeting_id,
  4. mint the MeetingToken + build the ``invocation.v1`` invocation (BOT_CONFIG),
  5. spawn the meeting-bot workload over ``runtime.v1`` (``RuntimeClient.create_workload``),
  6. eager-create the ``MeetingSession`` keyed by the bot's ``connectionId`` (== session_uid),
  7. write the kernel workload id back as ``bot_container_id``,
  8. return the ``api.v1`` ``MeetingResponse``.
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Optional

from ..obs import log_event
from .invocation import build_invocation, build_workload_spec, mint_meeting_token
from .ports import MeetingRepo, QuotaExceeded, RuntimeClient, SpawnFailed

# Construct-URL templates per platform (the parent's ``Platform.construct_meeting_url``, core set).
_URL_TEMPLATES = {
    "google_meet": "https://meet.google.com/{native_meeting_id}",
    "teams": "https://teams.microsoft.com/l/meetup-join/{native_meeting_id}",
}


def construct_meeting_url(platform: str, native_meeting_id: str) -> Optional[str]:
    """Best-effort meeting URL for ``(platform, native_id)`` (zoom needs more than the id →
    None; the caller may pass an explicit ``meeting_url`` instead)."""
    tmpl = _URL_TEMPLATES.get(platform)
    return tmpl.format(native_meeting_id=native_meeting_id) if tmpl else None


def _meeting_response(row: dict) -> dict:
    """Shape a meeting row into an ``api.v1`` MeetingResponse-conforming dict (required:
    id, user_id, status, bot_container_id, start_time, end_time, created_at, updated_at)."""
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "platform": row.get("platform"),
        "native_meeting_id": row.get("native_meeting_id") or row.get("platform_specific_id"),
        "constructed_meeting_url": data.get("constructed_meeting_url"),
        "status": row.get("status", "requested"),
        "bot_container_id": row.get("bot_container_id"),
        "start_time": row.get("start_time"),
        "end_time": row.get("end_time"),
        "completion_reason": data.get("completion_reason"),
        "failure_stage": data.get("failure_stage"),
        "data": data,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


class DuplicateMeeting(Exception):
    """The user already has an active/requested meeting for (platform, native_id) → HTTP 409."""


async def request_bot(
    repo: MeetingRepo,
    runtime: RuntimeClient,
    *,
    user_id: int,
    platform: str,
    native_meeting_id: str,
    bot_name: Optional[str] = None,
    meeting_url: Optional[str] = None,
    language: Optional[str] = None,
    task: Optional[str] = None,
    transcription_tier: str = "realtime",
    recording_enabled: bool = False,
    transcribe_enabled: bool = True,
    redis_url: Optional[str] = None,
    meeting_api_url: Optional[str] = None,
    internal_secret: Optional[str] = None,
    token_secret: Optional[str] = None,
) -> dict:
    """Run the core spawn flow and return a MeetingResponse-shaped dict.

    Raises ``DuplicateMeeting`` (409), ``QuotaExceeded`` (429), or ``SpawnFailed`` (502/failed).
    """
    # 1. URL.
    constructed_url = meeting_url or construct_meeting_url(platform, native_meeting_id)

    # 2. Dedup.
    if await repo.find_active(user_id, platform, native_meeting_id):
        raise DuplicateMeeting(
            f"An active meeting already exists for {platform}/{native_meeting_id}"
        )

    # 3. Insert the Meeting row (status requested).
    meeting_data: dict[str, Any] = {}
    if constructed_url:
        meeting_data["constructed_meeting_url"] = constructed_url
    meeting_data["transcribe_enabled"] = transcribe_enabled
    meeting_data["recording_enabled"] = recording_enabled
    row = await repo.create_meeting(
        user_id=user_id,
        platform=platform,
        native_meeting_id=native_meeting_id,
        data=meeting_data,
    )
    meeting_id = row["id"]

    # 4. MeetingToken + invocation. connection_id IS the session_uid (parent's connectionId).
    connection_id = str(uuid.uuid4())
    redis_url = redis_url or os.getenv("REDIS_URL", "redis://redis:6379/0")
    meeting_api_url = meeting_api_url or os.getenv("MEETING_API_URL", "http://meeting-api:8080")
    internal_secret = internal_secret if internal_secret is not None else os.getenv(
        "INTERNAL_API_SECRET"
    )
    token = mint_meeting_token(
        meeting_id, user_id, platform, native_meeting_id, secret=token_secret
    )
    invocation = build_invocation(
        meeting_id=meeting_id,
        platform=platform,
        meeting_url=constructed_url,
        bot_name=bot_name or f"VexaBot-{uuid.uuid4().hex[:6]}",
        token=token,
        native_meeting_id=native_meeting_id,
        connection_id=connection_id,
        language=language,
        task=task,
        transcription_tier=transcription_tier,
        redis_url=redis_url,
        meeting_api_callback_url=f"{meeting_api_url}/bots/internal/callback/lifecycle",
        internal_secret=internal_secret,
        transcribe_enabled=transcribe_enabled,
        recording_enabled=recording_enabled,
        capture_modes=(["audio", "video"] if recording_enabled else None),
        recording_upload_url=f"{meeting_api_url}/internal/recordings/upload",
    )

    # 5. Spawn over runtime.v1.
    spec = build_workload_spec(
        workload_id=f"mtg-{meeting_id}-{connection_id[:8]}",
        invocation=invocation,
        callback_url=f"{meeting_api_url}/runtime/callback",
    )
    try:
        result = await runtime.create_workload(spec)
    except QuotaExceeded:
        log_event(
            "bot_spawn_quota_exceeded", audience="user", level="warning",
            span="bots.create", user_id=user_id, meeting_id=str(meeting_id),
        )
        raise
    except SpawnFailed:
        log_event(
            "bot_spawn_failed", audience="system", level="error",
            span="bots.create", user_id=user_id, meeting_id=str(meeting_id),
        )
        raise

    workload_id = result.get("workloadId") or result.get("name") or spec["workloadId"]

    # 6. Eager-create the MeetingSession (connectionId == session_uid).
    await repo.create_session(meeting_id=meeting_id, session_uid=connection_id)

    # 7. Write the kernel workload id back as bot_container_id.
    row = await repo.set_bot_container(meeting_id=meeting_id, bot_container_id=workload_id)

    # USER-facing: a bot was requested for this user.
    log_event(
        "bot_join_requested", audience="user", span="bots.create",
        user_id=user_id, meeting_id=f"{platform}/{native_meeting_id}",
        fields={"platform": platform, "status": row.get("status", "requested")},
    )
    return _meeting_response(row)
