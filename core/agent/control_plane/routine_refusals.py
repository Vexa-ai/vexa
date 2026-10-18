"""routine_refusals.py — a scheduled routine refused the same way three runs in a row is paused.

A routine runs with nobody in the loop, so every mail, calendar and connection verb it reaches for is
refused (`human_session_required`). That cannot change between runs: a routine that is refused on
one run is refused on the next, every ten minutes, for as long as it stays on. This module stops
that loop. It does not change what is refused.

WHERE THE OUTCOME IS READ, and why there. The worker is the one writer of its unit's output stream
(``unit:<id>:out``), and its harness stamps each turn's ``done`` with ``refused: {tool, reason,
count}`` when the turn's tool calls were refused (`worker.engine`, `llm.refusal_guard`). A
scheduled routine's unit id is derived from the dispatch agent-api signed itself (`dispatch_id`), so
agent-api can attribute a stream to a routine without trusting anything the worker says about which
routine it is. So the check runs at the one door every scheduled run comes through, ``/invocations``,
just BEFORE the next run is dispatched: read the previous runs' ``done`` frames since the last
cursor, count, and pause instead of dispatching once the count reaches :data:`THRESHOLD`. Nothing
new is coupled to the worker and no new route exists.

ONE WRITER. The count lives in ``<store>/.routines/refusals/<subject>.json`` — beside the routine
approvals, outside every workspace mount, so no worker can write it — and only :func:`before_dispatch`
writes it. Per routine: ``reason`` (the last refused reason), ``count`` (consecutive runs refused
with it), ``cursor`` (the last stream entry read, so no run is counted twice).

WHAT A PAUSE DOES, in order: the routine file gets ``enabled: false`` and a ``paused_reason:`` line
(`workspace_routines.set_routine_file_enabled`, the write ``PATCH /api/routines/{name}/enabled``
makes, approval carried across exactly as that route carries it); a reconcile cancels its scheduler
job; one ``routine.paused`` fact goes to flows, which puts ONE card on the person's queue
(``whats_waiting``, flow ``routine_paused``); the count resets. A successful run resets the count
too. Only ``trigger: scheduled`` dispatches with launcher ``schedule:<routine_id>`` are counted —
a chat, an event and a meeting run never are.

LIMITS. A routine created through ``POST /api/routines`` has no file to hold a paused state: it gets
the queue card once, and keeps its schedule (cancelling it would delete it). The count is per
agent-api process's view of a shared file; two replicas handling the same routine's fires at once
can count one run late, never early. The pause lands when the fourth fire arrives, because the
third run's outcome is read then.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Callable, Optional

from control_plane import publish as publish_mod
from control_plane import workspace_routines as wr
from shared.units import dispatch_id

log = logging.getLogger(__name__)

#: Consecutive runs refused for the same reason before a routine is paused.
THRESHOLD = 3
STATE_DIR = "refusals"
LAUNCHER_PREFIX = "schedule:"
_LOCK = threading.Lock()


def _state_file(workspaces_dir, subject: str) -> Path:
    wr._safe_workspace_dir(workspaces_dir, subject)   # the same subject check every path here makes
    return Path(workspaces_dir) / wr.STATE_DIR / STATE_DIR / f"{subject}.json"


def load_state(workspaces_dir, subject: str) -> dict:
    try:
        data = json.loads(_state_file(workspaces_dir, subject).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(workspaces_dir, subject: str, data: dict) -> None:
    path = _state_file(workspaces_dir, subject)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, sort_keys=True) + "\n")
    tmp.replace(path)


def routine_of(invocation: dict) -> Optional[tuple[str, str]]:
    """``(subject, routine_id)`` for a dispatch the scheduler fired for a routine; None for every
    other dispatch (a chat, an event, a meeting run, an internal-tier dispatch of any other kind)."""
    if str(invocation.get("trigger") or "") != "scheduled":
        return None
    identity = invocation.get("identity") or {}
    launcher = str(identity.get("launcher") or "")
    subject = str(identity.get("subject") or "")
    if not launcher.startswith(LAUNCHER_PREFIX) or not subject:
        return None
    rid = launcher[len(LAUNCHER_PREFIX):]
    return (subject, rid) if rid else None


def count_runs(entry: dict, events: list) -> dict:
    """Fold the ``done`` frames in ``events`` (``[(stream id, event)]``, oldest first) into one
    routine's ``entry``. Pure."""
    out = {"reason": str(entry.get("reason") or ""), "count": int(entry.get("count") or 0),
           "cursor": str(entry.get("cursor") or "")}
    for eid, ev in events:
        out["cursor"] = str(eid)
        if not isinstance(ev, dict) or ev.get("type") != "done":
            continue
        reason = str((ev.get("refused") or {}).get("reason") or "")
        if not reason:
            out["reason"], out["count"] = "", 0          # a run that was not refused resets it
        elif reason == out["reason"]:
            out["count"] += 1
        else:
            out["reason"], out["count"] = reason, 1
    return out


def paused_reason(reason: str, count: int) -> str:
    if reason == "human_session_required":
        return (f"paused after {count} runs in a row were refused: it needs you present "
                "(mail, calendar or a connection). Ask in chat instead, or edit it and switch it on.")
    return f"paused after {count} runs in a row were refused ({reason})."


def before_dispatch(invocation: dict, *, read_since: Callable, scheduler, invocations_url: str,
                    workspaces_dir, signing_secret: str = "",
                    publish: Optional[Callable] = None) -> Optional[dict]:
    """Count the routine's previous runs; pause it at :data:`THRESHOLD`. Returns
    ``{"paused": name, "routine_id", "reason"}`` when the routine was paused (the caller must not
    dispatch), else None. Never raises: a failure here logs and lets the run go ahead."""
    which = routine_of(invocation)
    if which is None:
        return None
    subject, rid = which
    publish = publish or publish_mod.publish_routine_paused
    try:
        with _LOCK:
            return _before_dispatch(invocation, subject, rid, read_since=read_since,
                                    scheduler=scheduler, invocations_url=invocations_url,
                                    workspaces_dir=workspaces_dir, signing_secret=signing_secret,
                                    publish=publish)
    except Exception:  # noqa: BLE001 — counting refusals is never worth a lost run
        log.exception("routine %s/%s: refusal count failed; dispatching as before", subject, rid)
        return None


def _before_dispatch(invocation, subject, rid, *, read_since, scheduler, invocations_url,
                     workspaces_dir, signing_secret, publish) -> Optional[dict]:
    state = load_state(workspaces_dir, subject)
    entry = state.get(rid) if isinstance(state.get(rid), dict) else {}
    events = read_since(dispatch_id(invocation), str(entry.get("cursor") or ""))
    if events is None:
        return None                                       # unreadable: count nothing, change nothing
    entry = {**entry, **count_runs(entry, events)}
    result = None
    if entry["count"] >= THRESHOLD:
        reason, count, at = entry["reason"], entry["count"], entry["cursor"]
        name = wr.routine_name_for_id(subject, rid, workspaces_dir=workspaces_dir)
        if name:
            path = wr._safe_routine_path(workspaces_dir, subject, name)
            approved = wr.routine_file_approved(path, subject=subject, workspaces_dir=workspaces_dir)
            wr.set_routine_file_enabled(subject, name, enabled=False, workspaces_dir=workspaces_dir,
                                        paused_reason=paused_reason(reason, count))
            if approved:
                wr.approve_routine_file(subject, name, workspaces_dir=workspaces_dir)
            wr.reconcile_workspace_routines(subject, scheduler=scheduler,
                                            invocations_url=invocations_url,
                                            workspaces_dir=workspaces_dir,
                                            signing_secret=signing_secret)
            publish(subject, rid, name, reason, at)
            log.warning("routine %s/%s paused after %d runs refused (%s)", subject, name, count, reason)
            entry.update({"reason": "", "count": 0})
            result = {"paused": name, "routine_id": rid, "reason": reason}
        elif not entry.get("flagged"):
            # No file to pause (an API-created routine): the card once, the schedule untouched.
            publish(subject, rid, rid, reason, at)
            entry["flagged"] = True
    elif entry["count"] == 0:
        entry.pop("flagged", None)
    state[rid] = entry
    _save_state(workspaces_dir, subject, state)
    return result
