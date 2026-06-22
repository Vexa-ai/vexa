"""The user-stop HTTP route — ``DELETE /bots/{platform}/{native_meeting_id}`` (api.v1).

The stop *logic* lives in ``stop.py`` (``request_stop`` → publish a ``leave`` command + mark
``stop_requested``); this is its HTTP wrapper, a mountable ``APIRouter`` (the modular-monolith
composition, P2), behaviour-matched to the parent ``meetings.stop_bot``:

  1. Resolve the caller (``x-user-id`` the gateway injects after it validates ``x-api-key``).
  2. ``find_active`` the user's non-terminal meeting for ``(platform, native_id)`` — 404 if none.
  3. Mark it ``stopping`` + ``stop_requested`` (so the exit is later attributed to a user stop, never a
     silent failure), then PUBLISH ``bot_commands:meeting:{id}`` ``{"action":"leave"}``.
  4. The bot honours the command, leaves, and emits its terminal ``lifecycle.v1`` event — which the
     existing ``/bots/internal/callback/lifecycle`` handler classifies (→ ``completed``/``failed``,
     ``meeting.status_change`` webhook fires). This route TRIGGERS the stop; it never jumps the FSM itself.

The redis side is a port (``CommandPublisher``) so tests drive it with an in-memory capture and prod
injects the real ``redis_client.publish``.
"""
from __future__ import annotations

import json
from typing import Any, Optional, Protocol, runtime_checkable

from fastapi import APIRouter, Header, HTTPException

from ..bot_spawn.ports import MeetingRepo
from .stop import leave_command_channel, leave_command_payload


@runtime_checkable
class CommandPublisher(Protocol):
    """The redis pub/sub side of the stop path — ``redis_client.publish(channel, message)``.

    ``redis.asyncio``'s client satisfies this directly; an in-memory capture satisfies it in tests."""

    async def publish(self, channel: str, message: str) -> Any:
        ...


class InMemoryCommandPublisher:
    """Default capture publisher (the app-factory fake / tests)."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> Any:
        self.published.append((channel, message))
        return 0


def _resolve_user_id(x_user_id: Optional[str]) -> int:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing user identity")
    try:
        return int(x_user_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid user identity")


def build_stop_router(repo: MeetingRepo, publisher: CommandPublisher) -> APIRouter:
    """The user-stop route over the injected ``MeetingRepo`` + ``CommandPublisher`` ports."""
    router = APIRouter()

    @router.delete("/bots/{platform}/{native_meeting_id}")
    async def stop_bot(
        platform: str,
        native_meeting_id: str,
        x_user_id: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        meeting = await repo.find_active(user_id, platform, native_meeting_id)
        if not meeting:
            raise HTTPException(status_code=404, detail="No active meeting for this bot")
        meeting_id = meeting["id"]
        # Mark stop-requested + move to 'stopping' (mirrors main's DELETE), keyed by the latest session
        # so the exit classifier reads the user-intent signal. Best-effort: an unknown session no-ops.
        sessions = await repo.list_sessions(meeting_id=meeting_id)
        if sessions:
            await repo.update_meeting_status(
                session_uid=sessions[-1], status="stopping", data={"stop_requested": True},
            )
        # Publish the leave command — the bot honours it, leaves, emits its terminal lifecycle event.
        await publisher.publish(
            leave_command_channel(meeting_id), json.dumps(leave_command_payload(meeting_id))
        )
        return {
            "status": "stopping",
            "meeting_id": meeting_id,
            "native_meeting_id": native_meeting_id,
        }

    return router
