"""The capacity queue — a bot waits for a seat instead of being refused (founder, 4 Nov 2026).

When the cluster is full, a new bot is never refused: its Pod waits, and the API says so with the
status ``queued`` (between ``requested`` and ``joining``) and a ``status_reason``:

* ``cluster_at_capacity`` — the bot's Pod cannot be placed and the cluster cannot grow;
* ``will_join_late`` — it is still queued when its meeting starts (an ad-hoc bot's meeting is
  already under way, so its notice follows at once; a booked bot's comes at its ``scheduled_at``).

Each one is a ``meeting.status_change`` webhook. ``queued`` is a SERVER-SIDE status, like
``requested``: no bot has run, so the bot's own FSM never sees it (``machine`` rehydrates it to the
FSM's pre-``joining`` entry). The queue ends by itself when a seat frees — the Pod schedules, the bot
boots and sends its normal ``joining``. A queued bot counts against the account's concurrent-bot
cap like a ``requested`` one (founder, 4 Nov 2026: «for simplicity»).

The wait is bounded: after ``VEXA_QUEUE_MAX_WAIT_S`` (default 30 minutes) from the moment the bot is
late, a bot whose Pod still has no seat has its Pod deleted (the deletion confirmed by the runtime
first) and ends ``failed`` with ``completion_reason`` ``cluster_at_capacity``. It is never retried,
and since no bot was ever admitted its service provenance reads ``bot_outcome: never_admitted``,
which billing rates as not billable.

WHAT "the cluster cannot grow" means here. The runtime reports the Pod's placement facts
(runtime.v1 ``WorkloadStatus.pending``); this module holds the policy:

* the Pod is Unschedulable (``PodScheduled=False``, reason ``Unschedulable``) and the scheduler has
  not nominated a node for it (a nomination is a preemption in progress: a seat is being freed), and
* EITHER the cluster autoscaler's own latest verdict on the Pod is ``NotTriggerScaleUp`` (no node
  group can be grown for it: max node group size reached, a resource limit, or a scale-up in
  backoff), OR, where no autoscaler gave a verdict (a cluster without one), the Pod has been
  Unschedulable for longer than ``VEXA_QUEUE_SCALE_UP_WINDOW_S`` (default 300 s, about twice a new
  node's p90 of 150 s).

A Pod waiting for its image or its container to start is not queued, and neither is one the
autoscaler is adding a node for (``TriggeredScaleUp``): those are the normal start.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

QUEUED = "queued"
REASON_CLUSTER_AT_CAPACITY = "cluster_at_capacity"
REASON_WILL_JOIN_LATE = "will_join_late"
#: The api.v1 ``MeetingStatusReason`` values. A meeting carries a ``status_reason`` only while queued.
STATUS_REASONS = frozenset({REASON_CLUSTER_AT_CAPACITY, REASON_WILL_JOIN_LATE})
#: The lifecycle.v1 ``CompletionReason`` of a bot whose queue wait ran out.
COMPLETION_REASON = "cluster_at_capacity"

MAX_WAIT_ENV = "VEXA_QUEUE_MAX_WAIT_S"
SCALE_UP_WINDOW_ENV = "VEXA_QUEUE_SCALE_UP_WINDOW_S"
DEFAULT_MAX_WAIT_S = 1800.0
DEFAULT_SCALE_UP_WINDOW_S = 300.0

#: The statuses the queue sweep looks at: a bot whose Pod may still be waiting for a seat.
WATCHED_STATUSES = ("requested", QUEUED)
#: Workloads that are not meeting bots: a sign-in browser, a voice note, a call room's agent.
QUEUE_EXCLUDED_PLATFORMS = ("browser_session", "voice_note", "vexa_call")
#: runtime.v1 states in which the workload still exists.
_ALIVE_STATES = frozenset({"starting", "running"})
_DETAIL_MAX = 300


def _env_seconds(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def max_wait_s() -> float:
    """How long a queued bot waits for a seat (``VEXA_QUEUE_MAX_WAIT_S``, default 1800)."""
    return _env_seconds(MAX_WAIT_ENV, DEFAULT_MAX_WAIT_S)


def scale_up_window_s() -> float:
    """How long a Pod may stay Unschedulable, with no autoscaler verdict, before the cluster is taken
    to be unable to grow (``VEXA_QUEUE_SCALE_UP_WINDOW_S``, default 300)."""
    return _env_seconds(SCALE_UP_WINDOW_ENV, DEFAULT_SCALE_UP_WINDOW_S)


def _parse(value: Any) -> Optional[datetime]:
    """An ISO-8601 time (``Z``, an offset, or naive = UTC) or a datetime → aware UTC; else None."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def waits_for_capacity(pending: Any, *, now: datetime, scale_up_window: float) -> bool:
    """Is a workload with these runtime placement facts waiting for a seat the cluster cannot add?"""
    if not isinstance(pending, dict) or pending.get("reason") != "unschedulable":
        return False
    if pending.get("nominated"):
        return False                       # preemption in progress: a seat is being freed for it
    verdict = pending.get("autoscaler")
    if verdict == "no_scale_up":
        return True
    if verdict == "scale_up":
        return False                       # a node is on its way: the normal scale-up wait
    since = _parse(pending.get("since"))
    return since is not None and (now - since).total_seconds() >= scale_up_window


def status_reason_of(status: Any, data: Any) -> Optional[str]:
    """The api.v1 ``status_reason`` of a meeting row: set only while it is ``queued``."""
    if status != QUEUED or not isinstance(data, dict):
        return None
    queue = data.get("queue")
    reason = queue.get("status_reason") if isinstance(queue, dict) else None
    return reason if reason in STATUS_REASONS else None


def late_at(queue: dict, scheduled_at: Any) -> Optional[datetime]:
    """When a queued bot becomes late: its booked start, or (an ad-hoc bot, or a booked one queued
    after its start) the moment it was queued."""
    queued_at = _parse((queue or {}).get("queued_at"))
    if queued_at is None:
        return None
    start = _parse(scheduled_at)
    return start if start is not None and start > queued_at else queued_at


def queue_deadline(queue: dict, scheduled_at: Any, *, max_wait: float) -> Optional[datetime]:
    """When a queued bot's wait runs out: ``max_wait`` after it became late."""
    late = late_at(queue, scheduled_at)
    return None if late is None else late + timedelta(seconds=max_wait)


def build_queue_trail_entry(
    old_status: Optional[str], status_reason: str, *, now: Optional[datetime] = None
) -> dict:
    """The ``status_transition[]`` entry of a queue transition (same keys as an FSM entry)."""
    when = now or datetime.now(timezone.utc)
    return {
        "from": old_status,
        "to": QUEUED,
        "timestamp": _iso(when),
        "timestamp_source": "receiver",
        "source": "capacity_queue",
        "reason": status_reason,
    }


async def capacity_queue_sweep(
    repo: Any,
    runtime: Any,
    *,
    enter: Callable[..., Awaitable[Optional[dict]]],
    time_out: Callable[[dict], Awaitable[Any]],
    log: Any,
    now: Optional[datetime] = None,
    max_wait: Optional[float] = None,
    scale_up_window: Optional[float] = None,
) -> dict:
    """ONE pass over the bots whose Pod may be waiting for a seat. Best-effort per meeting — never
    raises. Returns counts ``{queued, late, timed_out}``.

    ``enter`` is the conditional server-side transition (``app.state.apply_queue_transition``: it
    lands only while the row is still in the expected status, then emits the webhook and the ws
    frames). ``time_out`` drives the terminal ``failed`` through the lifecycle entry, after this sweep
    has CONFIRMED the Pod's deletion with the runtime.
    """
    from ..bot_spawn.ports import WorkloadUnknown

    counts = {"queued": 0, "late": 0, "timed_out": 0}
    if repo is None or runtime is None or not hasattr(repo, "list_queue_candidates"):
        return counts
    now = now or datetime.now(timezone.utc)
    max_wait = max_wait_s() if max_wait is None else max_wait
    window = scale_up_window_s() if scale_up_window is None else scale_up_window
    try:
        rows = await repo.list_queue_candidates()
    except Exception:  # noqa: BLE001
        log.exception("capacity-queue: list_queue_candidates failed")
        return counts
    for row in rows:
        try:
            await _sweep_one(row, repo, runtime, enter=enter, time_out=time_out, log=log, now=now,
                             max_wait=max_wait, window=window, counts=counts,
                             workload_unknown=WorkloadUnknown)
        except Exception:  # noqa: BLE001 — one meeting never stops the pass
            log.exception("capacity-queue: meeting %s failed", row.get("meeting_id"))
    return counts


def timeout_reason(max_wait: float, detail: Any = None) -> str:
    reason = (f"no seat for the bot within {int(round(max_wait / 60))} min: "
              "the cluster stayed at capacity")
    return f"{reason} ({detail})" if detail else reason


async def _sweep_one(row, repo, runtime, *, enter, time_out, log, now, max_wait, window, counts,
                     workload_unknown) -> None:
    status = row.get("status")
    session_uid = row.get("session_uid")
    workload_id = row.get("bot_container_id")
    if status not in WATCHED_STATUSES or not session_uid or not workload_id:
        return
    if row.get("stop_requested"):
        return                             # the stop path tears it down; nothing to announce
    try:
        info = await runtime.get_workload(workload_id)
    except Exception:  # noqa: BLE001 — no answer is no evidence: leave the meeting as it is
        log.warning("capacity-queue: get_workload(%s) failed; leaving meeting %s",
                    workload_id, row.get("meeting_id"))
        return
    queue = dict(row.get("queue") or {}) if isinstance(row.get("queue"), dict) else {}
    wait = row.get("queue_wait_s")
    max_wait = float(wait) if isinstance(wait, (int, float)) and not isinstance(wait, bool) and wait > 0 \
        else max_wait
    if not isinstance(info, dict) or info.get("state") not in _ALIVE_STATES:
        if status == QUEUED and queue.get("timing_out") and isinstance(info, dict):
            # Its Pod is gone and we were the ones deleting it: finish what the last pass began
            # (a restart between the delete and the terminal write).
            await time_out({"connection_id": session_uid, "status": "failed",
                            "completion_reason": COMPLETION_REASON,
                            "reason": timeout_reason(max_wait, queue.get("detail"))})
            counts["timed_out"] += 1
        return                             # untracked or gone: the reconcile sweep owns it
    pending = info.get("pending")

    if status == "requested":
        if not waits_for_capacity(pending, now=now, scale_up_window=window):
            return
        queue = {"queued_at": _iso(now), "status_reason": REASON_CLUSTER_AT_CAPACITY}
        detail = (pending or {}).get("detail")
        if isinstance(detail, str) and detail:
            queue["detail"] = detail[:_DETAIL_MAX]
        moved = await enter(session_uid=session_uid, expected=("requested",),
                            status_reason=REASON_CLUSTER_AT_CAPACITY, queue_patch=queue)
        if not isinstance(moved, dict):
            return                         # the bot sent `joining`, or a stop landed, meanwhile
        counts["queued"] += 1

    deadline = queue_deadline(queue, row.get("scheduled_at"), max_wait=max_wait)
    if deadline is None:
        return
    if now >= deadline:
        if not (isinstance(pending, dict) and pending.get("reason") == "unschedulable"
                and not pending.get("nominated") and pending.get("autoscaler") != "scale_up"):
            return                         # a seat is coming (placed, preempting, a node on its way)
        # Stamp the timeout BEFORE the delete: the runtime reports the destroyed Pod to
        # /runtime/callback while the delete is still in flight, and that callback (or a restart
        # between the delete and the write below) must end the run as this timeout, not as a crash.
        if hasattr(repo, "merge_meeting_data") and row.get("meeting_id") is not None:
            await repo.merge_meeting_data(row["meeting_id"], {"queue": {**queue, "timing_out": _iso(now)}})
        try:
            await runtime.delete_workload(workload_id)
        except workload_unknown:
            log.error("capacity-queue: runtime does not know workload %s of meeting %s; not "
                      "failing it on amnesia", workload_id, row.get("meeting_id"))
            return
        except Exception:  # noqa: BLE001 — teardown unconfirmed: retried on the next pass
            log.warning("capacity-queue: deleting workload %s failed; retried next pass", workload_id)
            return
        minutes = int(round(max_wait / 60))
        reason = timeout_reason(max_wait, queue.get("detail"))
        await time_out({
            "connection_id": session_uid,
            "status": "failed",
            "completion_reason": COMPLETION_REASON,
            "reason": reason,
        })
        counts["timed_out"] += 1
        log.info("capacity-queue: meeting %s failed %s after %s min queued",
                 row.get("meeting_id"), COMPLETION_REASON, minutes)
        return
    late = late_at(queue, row.get("scheduled_at"))
    if late is not None and now >= late and queue.get("status_reason") != REASON_WILL_JOIN_LATE:
        moved = await enter(session_uid=session_uid, expected=(QUEUED,),
                            status_reason=REASON_WILL_JOIN_LATE,
                            queue_patch={"status_reason": REASON_WILL_JOIN_LATE, "late_at": _iso(now)})
        if isinstance(moved, dict):
            counts["late"] += 1
