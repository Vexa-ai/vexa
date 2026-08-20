"""The user-stop HTTP route — ``DELETE /bots/{platform}/{native_meeting_id}`` (api.v1).

The stop *logic* lives in ``stop.py`` (``request_stop`` → publish a ``leave`` command + mark
``stop_requested``); this is its HTTP wrapper, a mountable ``APIRouter`` (the modular-monolith
composition, P2), behaviour-matched to the parent ``meetings.stop_bot``:

  1. Resolve the caller (``x-user-id`` the gateway injects after it validates ``x-api-key``).
  2. ``find_active_rows`` — EVERY non-terminal meeting the user has for ``(platform, native_id)``,
     404 if none. Not one row: *"explicit stop must be stop, evict"* (founder ruling 2026-08-17).
     The user pressed stop once and means the MEETING, so a sibling row still waiting in the lobby
     is taken down with it. Stopping only the newest let that sibling walk in after the user said no.
  2b. Split the rows by whether a bot EXISTS for them. A PLANNED row (``scheduled``/``idle``) has no
     bot, no workload and no session — a stop on it is a CANCELLATION, and it is terminalized right
     here (``failed``/``stopped``) rather than flagged and left ``scheduled``. Leaving it flagged was
     F1: the auto-join sweep's due-filter is a RATE rule and ``lifecycle.occurrence``'s eligibility
     rule only classifies TERMINAL rows, so a flagged-but-scheduled row fell between the two and was
     dispatched anyway (rev 193 row 26306 — stop accepted, pod spawned 02:52:16, bot joined).
  3. For the rows that DO have a bot: mark them ``stopping`` + ``stop_requested`` (so each exit is
     later attributed to a user stop, never a silent failure — and so ``lifecycle.occurrence`` never
     lets the calendar re-dispatch the occurrence), then PUBLISH ``bot_commands:meeting:{id}``
     ``{"action":"leave"}`` per row.
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

# PLANNED statuses: a row that has a TIME but no bot — no workload, no session, nothing listening on
# a command channel. A stop here is a CANCELLATION, and it must land the row on a terminal state
# immediately rather than leaving a `scheduled` row wearing a `stop_requested` flag: the auto-join
# sweep's due-filter is a rate rule, `lifecycle.occurrence`'s eligibility rule only classifies
# TERMINAL rows, and between the two a flagged-but-scheduled row was due exactly as before. That
# zombie is F1 (stage rev 193 row 26306: stop accepted 200, pod spawned 02:52:16, bot joined).
_PLANNED_STATUSES = {"scheduled", "idle"}

# The terminal a cancelled plan lands on. `failed`, never `completed`: the FSM admits COMPLETED only
# from ACTIVE (``machine.LEGAL_TRANSITIONS``), and every reader treats a `completed` row as positive
# evidence the bot reached the room (``occurrence`` rule 2) — writing it for a bot that never existed
# is the #807 laundering, one stage earlier. `failed`/`stopped` is what a pre-active user stop
# already resolves to (``stop.classify_user_stop``), so this introduces no new vocabulary: no
# "cancelled" status, which would need an api.v1 bump plus every reader that switches on status.
# ``occurrence.disposition`` rule 1 catches it as USER_STOPPED before any failure classification
# runs, so the calendar never re-arms the occurrence.
_CANCELLED_REASON = "stopped"

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
        # EVERY non-terminal row for this room, not just the newest. "The user pressed stop once
        # and means the meeting, not one container" (founder ruling 2026-08-17). `find_active`
        # returns one row — enough for the POST dedup, wrong here: a sibling row still waiting in
        # the lobby survived the stop and walked in afterwards. The shapes that produce a sibling
        # are known and live (a manual "Send bot now" plus a calendar import that failed to adopt
        # it — rows 26237 + 26251 on one native id, 2026-08-17).
        rows = await _active_rows(repo, user_id, platform, native_meeting_id)
        if not rows:
            raise HTTPException(status_code=404, detail="No active meeting for this bot")
        # The stop trigger is ONE-SHOT. Redelivering DELETE must not re-publish a leave command or
        # re-tear-down a workload, so the guard is the user-intent flag itself rather than a status
        # side-effect: `stop_requested` is set by the first stop and is true regardless of which
        # status the meeting was stopped at. (Reading it off the status cannot work — the SQL
        # adapter's active set contains `stopping`, so a second DELETE still FINDS the row.)
        #
        # Applied PER ROW, not to the response as a whole: a second DELETE that finds every row
        # already stop-requested is the redelivery this guards (404), but one that finds a NEW
        # sibling — spawned after the first stop — must still take it down.
        pending = [row for row in rows if not (row.get("data") or {}).get("stop_requested")]
        if not pending:
            raise HTTPException(status_code=404, detail="No active meeting for this bot")
        meeting = pending[0]
        meeting_id = meeting["id"]

        # A PLANNED row has no bot to ask, so it is CANCELLED here and now — terminal, attributed,
        # done — instead of being flagged and left `scheduled` for a sweep that reads a different
        # rule (F1). Split out first so the live path below never has to special-case it.
        planned = [row for row in pending if row.get("status") in _PLANNED_STATUSES]
        live = [row for row in pending if row.get("status") not in _PLANNED_STATUSES]
        # Reported separately from `cancelled`: `also_stopped` means "another RUNNING bot was taken
        # down too", which is the operational surprise worth surfacing. A cancelled plan is not one.
        siblings = [row for row in pending[1:] if row not in planned]
        for row in planned:
            await _cancel_planned(repo, row)

        # Mark EVERY pending LIVE row stop-requested BEFORE publishing anything, and before reading
        # anything back. The flag is the durable record of user intent: the exit classifier reads it
        # to attribute the terminal to a user stop, and `lifecycle.occurrence` reads it to keep the
        # calendar from ever re-dispatching this occurrence. Persisting all of them first means a
        # Redis outage below (503) still leaves every row correctly attributed for the reconcile
        # sweep to converge — AND it is the write half of the spawn interlock (see the re-read below
        # and ``bot_spawn.service``'s post-spawn check).
        for row in live:
            await _mark_stop_requested(repo, row)

        # Publish the leave command — an ACTIVE (listening) bot honours it, leaves, emits its terminal
        # event. One command per row: each bot listens on its OWN meeting-scoped channel, so evicting a
        # sibling means addressing it by its own id, never the primary's.
        # #809: this is a GENUINELY Redis-dependent path (pub/sub is the only delivery). During a Redis
        # outage it must fail NARROWLY per-request (503, retryable) — not as an opaque 500 stack trace,
        # and never process-wide. The `stopping` marks above are already persisted to Postgres, so the
        # stop-reconcile sweep still converges the meetings when Redis returns; a 503 tells the caller to
        # retry the leave once the cache is back.
        try:
            for row in live:
                await publisher.publish(
                    leave_command_channel(row["id"]),
                    json.dumps(leave_command_payload(row["id"])),
                )
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 — Redis unreachable → narrow, retryable failure
            raise HTTPException(
                status_code=503,
                detail="stop command bus (redis) unavailable; the stop is recorded and will "
                       "reconcile when redis returns — retry to re-issue the leave",
            ) from e

        # GUARANTEE no orphan: a stop must not rely solely on a fire-and-forget command the bot may never
        # receive. A BOOTING bot (status in _BOOTING_STATUSES) has likely not subscribed yet → directly
        # tear its workload down (it has nothing to finalize). Best-effort: logged, never fails the stop.
        # A waiting SIBLING is almost always in exactly that state, which is why it outlived the stop.
        #
        # RE-READ the row before deciding. `rows` above is a snapshot taken BEFORE this request
        # wrote anything, and a spawn racing this stop writes `bot_container_id` after it — so the
        # snapshot's `bot_container_id=None` is exactly what let rev 193's pod survive (F2). Reading
        # it fresh, AFTER the `stop_requested` write is committed, is the read half of the interlock:
        #
        #     stop:   write stop_requested → read bot_container_id   (here)
        #     spawn:  write bot_container_id → read stop_requested   (bot_spawn.service)
        #
        # Each side publishes before it reads, so no interleaving lets both miss.
        if runtime is not None:
            for row in live:
                fresh = await _reread(repo, row)
                container = fresh.get("bot_container_id")
                if container and fresh.get("status") in _BOOTING_STATUSES:
                    try:
                        await runtime.delete_workload(container)
                    except Exception as e:  # noqa: BLE001 — best-effort; reconcile backstops
                        _log_stop_teardown_failed(row["id"], container, e)

        if siblings:
            _log_stop_evicted(meeting_id, [row["id"] for row in siblings], native_meeting_id)
        return {
            "status": "stopping",
            "meeting_id": meeting_id,
            "native_meeting_id": native_meeting_id,
            # Named so the caller can see the stop took the ROOM, not one container. Present
            # always (empty in the ordinary one-row case) — an optional key would make "no
            # siblings" and "an older build" indistinguishable.
            "also_stopped": [row["id"] for row in siblings],
            # Rows CANCELLED outright rather than asked to leave: planned occurrences whose bot had
            # not been dispatched. Same always-present rule as `also_stopped`. `status` stays
            # "stopping" whatever the mix — it describes the request, and the api.v1 client codes
            # against it; the truthful per-row fate is on the rows themselves.
            "cancelled": [row["id"] for row in planned],
        }

    return router


async def _active_rows(repo, user_id: int, platform: str, native_meeting_id: str) -> list:
    """Every non-terminal row for the room, newest first — via ``find_active_rows`` when the repo
    offers it, else the single-row ``find_active`` it supersedes.

    The fallback is not decoration: ``MeetingRepo`` is a Protocol with several implementations, and
    a stop that raised AttributeError against an older one would be a worse failure than a stop that
    covers one row. Where the port is present the eviction is complete."""
    lister = getattr(repo, "find_active_rows", None)
    if lister is not None:
        return list(await lister(user_id, platform, native_meeting_id) or ())
    single = await repo.find_active(user_id, platform, native_meeting_id)
    return [single] if single else []


async def _reread(repo, row: dict) -> dict:
    """This row as the store has it NOW — never the request's opening snapshot.

    Falls back to the snapshot against a repo without ``get_meeting`` (``MeetingRepo`` has several
    implementations): degrading to the previous, narrower behaviour beats raising AttributeError
    through a user's stop."""
    getter = getattr(repo, "get_meeting", None)
    if getter is None:
        return row
    try:
        fresh = await getter(row["id"])
    except Exception:  # noqa: BLE001 — a read failure must not fail the stop
        return row
    return fresh if isinstance(fresh, dict) else row


async def _cancel_planned(repo, row: dict) -> None:
    """Terminalize a PLANNED occurrence the user stopped — ``failed`` / ``stopped``, immediately.

    There is no bot: nothing to publish a leave to, no workload to tear down, no session to key a
    status write by. The only correct act is to record that this occurrence is over and WHY, so that
    every downstream rule that asks "may this be dispatched?" gets its answer from a terminal row
    (``lifecycle.occurrence``) instead of from a rate rule that has no way to say never.

    See ``_CANCELLED_REASON`` above for why the terminal is ``failed`` and not a new ``cancelled``
    state. ``failure_stage`` is deliberately absent: no bot ever ran, so there is no stage to
    attribute, and stamping ``requested`` would claim a spawn that never happened."""
    await repo.fail_meeting(
        meeting_id=row["id"],
        reason="cancelled by the user before the bot was dispatched",
        failure_stage=None,
        completion_reason=_CANCELLED_REASON,
        data={"stop_requested": True},
    )
    try:
        from ..obs import log_event

        log_event(
            "stop_cancelled_planned", audience="user", span="bots.stop",
            user_id=row.get("user_id"), meeting_id=str(row["id"]),
            fields={"was_status": row.get("status"),
                    "native_meeting_id": row.get("native_meeting_id")},
        )
    except Exception:  # noqa: BLE001 — logging never fails a stop
        pass


async def _mark_stop_requested(repo, row: dict) -> None:
    """Persist the user-stop intent on one row, keyed by its latest session.

    `stopping` is written ONLY over a status where the bot actually reached the meeting. It means
    "a live bot is being asked to leave", and the whole terminal chain reads it that way:
    `reconcile._WAS_ACTIVE_STATUSES` completes it, and `machine._PERSISTED_STATUS_TO_BOTSTATUS`
    rehydrates it as ACTIVE. Writing it over a PRE-ACTIVE status (a bot still in the waiting room)
    destroyed the only record of the stage the bot died in, and every downstream reader then
    concluded the bot had been live — so a bot that was never admitted was persisted as
    `completed` with zero transcript, via an `awaiting_admission → completed` edge the state
    machine does not even consider legal (#807). A pre-active bot has nothing to leave: its
    workload is torn down directly by the caller, and the terminal is attributed to the stage it
    really reached.

    A row with NO session yet (spawned so recently that the session write has not landed) still has
    to carry the flag — it is the only durable evidence the user said no, and without it the
    calendar would recreate this occurrence and send another bot. Fall back to a direct data merge.
    """
    status = row.get("status")
    sessions = await repo.list_sessions(meeting_id=row["id"])
    if sessions:
        await repo.update_meeting_status(
            session_uid=sessions[-1],
            status=status if status in _BOOTING_STATUSES else "stopping",
            data={"stop_requested": True},
        )
        return
    merge = getattr(repo, "merge_meeting_data", None)
    if merge is not None:
        await merge(row["id"], {"stop_requested": True})


def _log_stop_evicted(meeting_id, sibling_ids, native_meeting_id) -> None:
    """A stop that took down MORE than the row the caller named is a fact worth a line: it means a
    duplicate bot existed for that room, and the count is the only cheap signal of how often."""
    try:
        from ..obs import log_event

        log_event(
            "stop_evicted_siblings", audience="operator", level="warning", span="bots.stop",
            fields={"meeting_id": meeting_id, "evicted": sibling_ids,
                    "native_meeting_id": native_meeting_id},
        )
    except Exception:
        pass


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
