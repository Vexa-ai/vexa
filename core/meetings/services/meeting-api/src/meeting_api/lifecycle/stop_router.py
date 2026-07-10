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

from ..bot_spawn.ports import MeetingRepo, WorkloadUnknown
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


# Statuses where the bot is still BOOTING — it has not yet subscribed to its command channel, so the
# fire-and-forget leave below can be LOST (the POST→immediate-DELETE orphan). For these we ALSO tear the
# workload down directly. An `active`/`needs_help` bot IS listening → trust the graceful leave (so it
# finalizes its recording cleanly); the reconcile loop is the backstop if it never completes.
_BOOTING_STATUSES = {"requested", "joining", "awaiting_admission"}

# The sealed api.v1 `Platform` enum — the DELETE path param is typed as this enum in the contract, so an
# unsupported platform is a VALIDATION error (422), not a missing-resource (404). Mirrors the POST /bots
# platform guard (A1/A3): reject a non-enum platform up front, BEFORE the find_active lookup (which would
# otherwise miss and 404 — drifting from the contract a client must code against).
_SUPPORTED_PLATFORMS = frozenset({"google_meet", "zoom", "teams", "jitsi", "browser_session"})


def build_stop_router(repo: MeetingRepo, publisher: CommandPublisher, runtime=None) -> APIRouter:
    """The user-stop route over the injected ``MeetingRepo`` + ``CommandPublisher`` (+ optional runtime
    ``RuntimeClient`` for the direct-teardown guarantee) ports."""
    router = APIRouter()

    @router.delete("/bots/{platform}/{native_meeting_id}")
    async def stop_bot(
        platform: str,
        native_meeting_id: str,
        x_user_id: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        # A3: the sealed path param is the `Platform` enum → an unsupported platform is a 422
        # (validation error), not a 404. Reject it BEFORE find_active (which would miss → 404),
        # mirroring the POST /bots platform guard. Valid platforms keep idempotent-delete
        # semantics (a nonexistent meeting on a valid platform still → 404 below).
        if platform not in _SUPPORTED_PLATFORMS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"unsupported platform '{platform}' — "
                    f"must be one of: {', '.join(sorted(_SUPPORTED_PLATFORMS))}"
                ),
            )
        meeting = await repo.find_active(user_id, platform, native_meeting_id)
        if not meeting:
            raise HTTPException(status_code=404, detail="No active meeting for this bot")
        meeting_id = meeting["id"]
        status = meeting.get("status")
        bot_container_id = meeting.get("bot_container_id")
        # Mark stop-requested + move to 'stopping' (mirrors main's DELETE), keyed by the latest session
        # so the exit classifier reads the user-intent signal. Best-effort: an unknown session no-ops.
        sessions = await repo.list_sessions(meeting_id=meeting_id)
        if sessions:
            await repo.update_meeting_status(
                session_uid=sessions[-1], status="stopping", data={"stop_requested": True},
            )
        # Publish the leave command — an ACTIVE (listening) bot honours it, leaves, emits its terminal event.
        await publisher.publish(
            leave_command_channel(meeting_id), json.dumps(leave_command_payload(meeting_id))
        )
        # GUARANTEE no orphan: a stop must not rely solely on a fire-and-forget command the bot may never
        # receive. A BOOTING bot (status in _BOOTING_STATUSES) has likely not subscribed yet → directly
        # tear its workload down (it has nothing to finalize). Best-effort: logged, never fails the stop.
        if runtime is not None and bot_container_id and status in _BOOTING_STATUSES:
            try:
                await runtime.delete_workload(bot_container_id)
            except Exception as e:  # noqa: BLE001 — teardown is best-effort; the reconcile loop backstops
                _log_stop_teardown_failed(meeting_id, bot_container_id, e)
        return {
            "status": "stopping",
            "meeting_id": meeting_id,
            "native_meeting_id": native_meeting_id,
        }

    return router


def _log_stop_teardown_failed(meeting_id, workload_id, err) -> None:
    # A runtime 404 (WorkloadUnknown) means termination is UNCONFIRMED — a container may still be
    # live. That is a louder failure (error) than a transient delete error (warning): the meeting
    # stays `stopping` until the reconcile sweep gets a CONFIRMED teardown, never silently "done".
    unconfirmed = isinstance(err, WorkloadUnknown)
    try:
        from ..obs import log_event

        log_event(
            "stop_workload_teardown_unconfirmed" if unconfirmed else "stop_workload_teardown_failed",
            audience="system",
            level="error" if unconfirmed else "warning",
            span="bots.stop",
            fields={"meeting_id": meeting_id, "workload_id": workload_id, "error": str(err)},
        )
    except Exception:
        pass
