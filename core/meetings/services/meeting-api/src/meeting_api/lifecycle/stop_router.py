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

import asyncio
import json
from datetime import datetime, timedelta, timezone
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
# Only ``awaiting_admission`` proves both facts the graceful handshake requires: the bot subscribed
# to acts before entering join(), and a host-visible knock may exist. ``requested``/``joining`` do
# not confirm a listener; preserving their direct P22 delete prevents a Stop from being lost while
# a bot continues booting into the meeting.
_WITHDRAWAL_STATUSES = {"awaiting_admission"}
WITHDRAWAL_SLA_SECONDS = 10
WITHDRAWAL_TIMEOUT_SECONDS = 24

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
        # The stop trigger is ONE-SHOT. Redelivering DELETE must not re-publish a leave command or
        # re-tear-down a workload, so the guard is the user-intent flag itself rather than a status
        # side-effect: `stop_requested` is set by the first stop and is true regardless of which
        # status the meeting was stopped at. (Reading it off the status cannot work — the SQL
        # adapter's active set contains `stopping`, so a second DELETE still FINDS the row.)
        if (meeting.get("data") or {}).get("stop_requested"):
            raise HTTPException(status_code=404, detail="No active meeting for this bot")
        meeting_id = meeting["id"]
        status = meeting.get("status")
        bot_container_id = meeting.get("bot_container_id")
        # Mark stop-requested, keyed by the latest session so the exit classifier reads the user-intent
        # signal. Best-effort: an unknown session no-ops.
        #
        # `stopping` is written ONLY over a status where the bot actually reached the meeting. It means
        # "a live bot is being asked to leave", and the whole terminal chain reads it that way:
        # `reconcile._WAS_ACTIVE_STATUSES` completes it, and `machine._PERSISTED_STATUS_TO_BOTSTATUS`
        # rehydrates it as ACTIVE. Writing it over a PRE-ACTIVE status (a bot still in the waiting room)
        # destroyed the only record of the stage the bot died in, and every downstream reader then
        # concluded the bot had been live — so a bot that was never admitted was persisted as
        # `completed` with zero transcript, via an `awaiting_admission → completed` edge the state
        # machine does not even consider legal (#807). A pre-active bot has nothing to leave: its
        # workload is torn down directly below, and the terminal is attributed to the stage it really
        # reached.
        sessions = await repo.list_sessions(meeting_id=meeting_id)
        session_uid = sessions[-1] if sessions else None
        withdrawal = None
        if status in _WITHDRAWAL_STATUSES and session_uid:
            requested_at = datetime.now(timezone.utc)
            withdrawal = {
                "status": "pending",
                "requested_at": requested_at.isoformat().replace("+00:00", "Z"),
                "deadline_at": (
                    requested_at + timedelta(seconds=WITHDRAWAL_TIMEOUT_SECONDS)
                ).isoformat().replace("+00:00", "Z"),
                "sla_seconds": WITHDRAWAL_SLA_SECONDS,
                "timeout_seconds": WITHDRAWAL_TIMEOUT_SECONDS,
            }
        if session_uid:
            await repo.update_meeting_status(
                session_uid=session_uid,
                status=status if status in _BOOTING_STATUSES else "stopping",
                data={
                    "stop_requested": True,
                    **({"withdrawal": withdrawal} if withdrawal is not None else {}),
                },
            )
        # The timeout is a capacity guarantee, not graceful success. Schedule it from the durable
        # pending row before touching redis: even a command-bus outage still converges to the typed
        # timeout outcome and a bounded delete. The deterministic oracle calls
        # ``enforce_withdrawal_timeout`` directly with a fake time; it never proves ordering by sleep.
        if withdrawal is not None and runtime is not None and bot_container_id:
            task = asyncio.create_task(
                _withdrawal_timeout_task(
                    repo, runtime, session_uid, meeting_id, bot_container_id
                ),
                name=f"withdrawal-timeout-{meeting_id}",
            )
            task.add_done_callback(_consume_timeout_task)
        # Publish the leave command — an ACTIVE (listening) bot honours it, leaves, emits its terminal event.
        # #809: this is a GENUINELY Redis-dependent path (pub/sub is the only delivery). During a Redis
        # outage it must fail NARROWLY per-request (503, retryable) — not as an opaque 500 stack trace,
        # and never process-wide. The `stopping` mark above is already persisted to Postgres, so the
        # stop-reconcile sweep still converges the meeting when Redis returns; a 503 tells the caller to
        # retry the leave once the cache is back.
        try:
            await publisher.publish(
                leave_command_channel(meeting_id), json.dumps(leave_command_payload(meeting_id))
            )
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 — Redis unreachable → narrow, retryable failure
            raise HTTPException(
                status_code=503,
                detail="stop command bus (redis) unavailable; the stop is recorded and will "
                       "reconcile when redis returns — retry to re-issue the leave",
            ) from e
        # P22 remains the guarantee for a consumer not yet confirmed listening. There cannot be a
        # host-visible lobby knock in ``requested``; ``joining`` has not emitted the subscribed
        # waiting-room status yet. Delete these directly so a lost pub/sub command cannot let a
        # stopped bot proceed into the meeting.
        if (
            runtime is not None
            and bot_container_id
            and status in (_BOOTING_STATUSES - _WITHDRAWAL_STATUSES)
        ):
            try:
                await runtime.delete_workload(bot_container_id)
            except Exception as e:  # noqa: BLE001 — reconcile backstops and logs
                _log_stop_teardown_failed(meeting_id, bot_container_id, e)
        return {
            "status": "stopping",
            "meeting_id": meeting_id,
            "native_meeting_id": native_meeting_id,
        }

    return router


async def enforce_withdrawal_timeout(
    repo: MeetingRepo,
    runtime: Any,
    *,
    session_uid: str,
    meeting_id: Any,
    workload_id: str,
    timed_out_at: Optional[datetime] = None,
) -> bool:
    """Persist the typed timeout and then force-delete, exactly once.

    Returns ``True`` only when this timeout won the durable compare-and-set. A completed bot
    acknowledgement wins the same CAS first, so a late timer performs no second delete and can
    never overwrite graceful-success evidence.
    """
    at = timed_out_at or datetime.now(timezone.utc)
    row = await repo.finalize_withdrawal(
        session_uid=session_uid,
        expected_status="pending",
        outcome={
            "status": "timed_out",
            "timed_out_at": at.isoformat().replace("+00:00", "Z"),
            "reason": "withdrawal acknowledgement not persisted before the bounded deadline",
        },
    )
    if row is None:
        return False
    try:
        await runtime.delete_workload(workload_id)
    except Exception as e:  # noqa: BLE001 — persisted timeout remains truthful; reconcile retries
        _log_stop_teardown_failed(meeting_id, workload_id, e)
    return True


async def _withdrawal_timeout_task(
    repo: MeetingRepo,
    runtime: Any,
    session_uid: str,
    meeting_id: Any,
    workload_id: str,
) -> None:
    await asyncio.sleep(WITHDRAWAL_TIMEOUT_SECONDS)
    await enforce_withdrawal_timeout(
        repo,
        runtime,
        session_uid=session_uid,
        meeting_id=meeting_id,
        workload_id=workload_id,
    )


def _consume_timeout_task(task: "asyncio.Task[None]") -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as e:  # noqa: BLE001 — fail loud without an unhandled-task warning
        _log_stop_teardown_failed("unknown", "unknown", e)


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
