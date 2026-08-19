"""The mid-call bot-config HTTP route — ``PUT /bots/{platform}/{native_meeting_id}/config``.

The config *logic* lives in ``config.py`` (build the act, validate it against sealed acts.v1,
decide what persists); this is its HTTP wrapper, a mountable ``APIRouter`` (P2), behaviour-matched
to the sibling stop route:

  1. Resolve the caller (``x-user-id`` the gateway injects after it validates ``x-api-key``).
  2. Reject a non-enum platform as a validation error (422), BEFORE the lookup — the sealed path
     param is the ``Platform`` enum, so an unsupported platform is not a missing resource.
  3. ``find_active`` the user's non-terminal meeting for ``(platform, native_id)`` — 404 if none.
     A bot that has already left has no live config to change; the caller must know that.
  4. Refuse a body that names no config field (422) — a command that commands nothing is a caller
     bug, and returning 202 for it would report a change that never happened.
  5. PERSIST the new values onto the meeting record (status unchanged) so ``GET /bots`` and
     ``GET /meetings`` report the config the bot is running, not the one it was spawned with.
  6. PUBLISH the acts.v1 ``reconfigure`` on ``bot_commands:meeting:{id}``; the running bot writes
     it to its live STT config and the next transcription request carries it.
  7. Return 202 with the config now in force.

``allowedLanguages`` is accepted (the sealed acts.v1 ``Reconfigure`` declares it) and FORWARDED to
the bot, but nothing in the bot consumes it today — it is contract-legal and inert. It is neither
persisted nor echoed in the response's ``config``, so no reader can mistake it for an enforced
constraint.

The redis side is the same ``CommandPublisher`` port the stop route uses, so tests drive it with an
in-memory capture and prod injects the real ``redis_client.publish``.
"""
from __future__ import annotations

import json
from typing import Optional

import jsonschema
from fastapi import APIRouter, Body, Header, HTTPException
from fastapi.responses import JSONResponse

from ..bot_spawn.ports import MeetingRepo
from .config import (
    CONFIG_FIELDS,
    conforms,
    merged_config,
    missing_config_fields,
    persisted_config,
    reconfigure_command_channel,
    reconfigure_command_payload,
)
from .stop_router import CommandPublisher, resolve_user_id

# The sealed api.v1 `Platform` enum — same guard, same reason, as the stop route.
_SUPPORTED_PLATFORMS = frozenset({"google_meet", "zoom", "teams", "jitsi", "browser_session"})


def build_config_router(repo: MeetingRepo, publisher: CommandPublisher) -> APIRouter:
    """The mid-call config route over the injected ``MeetingRepo`` + ``CommandPublisher`` ports."""
    router = APIRouter()

    @router.put("/bots/{platform}/{native_meeting_id}/config", status_code=202)
    async def update_bot_config(
        platform: str,
        native_meeting_id: str,
        body: Optional[dict] = Body(default=None),
        x_user_id: Optional[str] = Header(default=None),
    ):
        user_id = resolve_user_id(x_user_id)
        if platform not in _SUPPORTED_PLATFORMS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"unsupported platform '{platform}' — "
                    f"must be one of: {', '.join(sorted(_SUPPORTED_PLATFORMS))}"
                ),
            )
        fields = body if isinstance(body, dict) else {}
        if body is not None and not isinstance(body, dict):
            raise HTTPException(status_code=422, detail="body must be a JSON object")
        # Unknown keys are REFUSED rather than dropped: the sealed operation declares no
        # requestBody, so this route is the only place a typo like {"lang":"es"} can be caught —
        # accepting it would 202 a command that changes nothing (P18, fail loud).
        unknown = sorted(set(fields) - set(CONFIG_FIELDS))
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"unknown config field(s): {', '.join(unknown)} — "
                    f"expected any of: {', '.join(CONFIG_FIELDS)}"
                ),
            )
        if missing_config_fields(fields):
            raise HTTPException(
                status_code=422,
                detail=(
                    "no config fields — send at least one of: "
                    f"{', '.join(CONFIG_FIELDS)}"
                ),
            )
        act = reconfigure_command_payload(fields)
        # Validate against the SEALED acts.v1 shape before anything is persisted or published: a
        # wrong-typed field (language: 5) is the caller's error, and the bot would ignore it in
        # silence — which is exactly the failure mode this issue exists to end.
        try:
            conforms(act)
        except jsonschema.ValidationError as e:
            raise HTTPException(
                status_code=422, detail=f"not a valid acts.v1 reconfigure: {e.message}"
            ) from e

        meeting = await repo.find_active(user_id, platform, native_meeting_id)
        if not meeting:
            raise HTTPException(status_code=404, detail="No active meeting for this bot")
        # A bot already asked to leave is on its way out — reconfiguring it would report a change
        # to a pipeline that is finalizing. Same guard, same reason, as the second-DELETE guard.
        if (meeting.get("data") or {}).get("stop_requested"):
            raise HTTPException(status_code=404, detail="No active meeting for this bot")
        meeting_id = meeting["id"]
        applied = persisted_config(act)

        # Persist FIRST, publish second: a published command the record does not reflect makes
        # `GET /bots` lie about a bot that already changed. The status is written back UNCHANGED —
        # this route never moves the FSM; only the `data` merge is the point. (The repo port keys
        # the write by session; an unknown session no-ops, exactly as the stop route relies on.)
        if applied:
            sessions = await repo.list_sessions(meeting_id=meeting_id)
            if sessions:
                await repo.update_meeting_status(
                    session_uid=sessions[-1],
                    status=meeting.get("status"),
                    data=applied,
                )
        # #809's rule for a genuinely Redis-dependent path: fail NARROWLY per-request (503,
        # retryable) rather than as an opaque 500. Unlike the stop path there is no reconcile
        # backstop for a config change, so the 503 says plainly that nothing reached the bot.
        try:
            await publisher.publish(
                reconfigure_command_channel(meeting_id), json.dumps(act)
            )
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 — Redis unreachable → narrow, retryable failure
            raise HTTPException(
                status_code=503,
                detail="bot command bus (redis) unavailable; the config change did NOT reach the "
                       "bot — retry to re-issue it",
            ) from e
        return JSONResponse(
            status_code=202,
            content={
                "status": "reconfiguring",
                "meeting_id": meeting_id,
                "native_meeting_id": native_meeting_id,
                "config": merged_config(meeting.get("data"), applied),
            },
        )

    return router


__all__ = ["build_config_router"]
