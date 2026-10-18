"""routine_flow_refusals.py — a routine refused the same way three runs in a row is paused.

A routine runs with nobody in the loop, so a verb its instruction reaches for that needs a person,
or that company policy keeps from unattended runs, is refused — and it is refused again on the next
run, and the one after, for as long as the routine stays on. This module stops that loop for
routines that are flows (ADR-0046). It changes nothing about what is refused.

WHERE THE OUTCOME IS READ. A routine's run is a turn flows posts into the routine's chat thread
(`agent_run` → `/api/chat/submit`, flow-marked over the internal tier); it runs on the thread's
`-auto` sibling unit, whose output stream (``unit:<id>:out``) the worker alone writes. Its harness
stamps each turn's ``done`` with ``refused: {tool, reason, count}`` when the turn's tool calls were
refused. So just BEFORE the next run is accepted, agent-api reads that unit's ``done`` frames since
its cursor and counts consecutive refusals with one reason. A run that was not refused resets it.

AT THREE: the flow is paused (flows-api, the person's own scope, operator key), the reason is
written for the Routines board (`routine_flows.set_paused_reason`) and posted into the routine's
own chat (a session note), and this run is refused with ``423 routine_paused`` — flows' `agent_run`
treats it as final. Resuming the routine clears the reason.

ONE WRITER: the count lives in ``<store>/.routines/flows/<subject>.json``, outside every workspace
mount, written only here and by `routine_flows`. Never raises into the run: a failure to count
logs and lets the run go ahead.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Callable, Optional

from control_plane import routine_flows as rf

log = logging.getLogger(__name__)

#: Consecutive runs refused for the same reason before a routine is paused.
THRESHOLD = 3
_LOCK = threading.Lock()


def count_runs(entry: dict, events: list) -> dict:
    """Fold the ``done`` frames in ``events`` (``[(stream id, event)]``, oldest first) into one
    routine's ``entry`` (``reason``, ``count``, ``cursor``). Pure."""
    out = {"reason": str(entry.get("reason") or ""), "count": int(entry.get("count") or 0),
           "cursor": str(entry.get("cursor") or "")}
    for eid, ev in events:
        out["cursor"] = str(eid)
        if not isinstance(ev, dict) or ev.get("type") != "done":
            continue
        reason = str((ev.get("refused") or {}).get("reason") or "")
        if not reason:
            out["reason"], out["count"] = "", 0
        elif reason == out["reason"]:
            out["count"] += 1
        else:
            out["reason"], out["count"] = reason, 1
    return out


def paused_reason(reason: str, count: int) -> str:
    if reason == "human_session_required":
        return (f"paused after {count} runs in a row were refused: what it asks for needs you "
                "present (mail, calendar or a connection), and a routine runs with nobody there. "
                "Ask in chat instead, or change the routine and resume it.")
    return (f"paused after {count} runs in a row were refused ({reason}). Change the routine or "
            "what it may use, then resume it.")


def redis_reader(redis_url: Optional[str]) -> Callable:
    """``read(unit_id, cursor) -> [(id, event)] | None`` over the unit's output stream, read-only."""
    def read(unit_id: str, cursor: str):
        if not redis_url:
            return None
        try:
            import redis

            from shared import units

            r = redis.from_url(redis_url, decode_responses=True)
            rows = r.xrevrange(units.output_topic(unit_id), max="+",
                               min=f"({cursor}" if cursor else "-", count=500)
        except Exception:  # noqa: BLE001 — an unread stream counts nothing
            log.warning("routine refusal count: a unit's output stream could not be read")
            return None
        out = []
        for entry_id, fields in reversed(rows or []):
            try:
                out.append((entry_id, json.loads((fields or {}).get("event") or "{}")))
            except ValueError:
                continue
        return out
    return read


def before_run(subject: str, flow_turn: dict, unit_id: str, *, read: Callable, workspaces_dir,
               client=None, store=None, session: Optional[str] = None) -> Optional[dict]:
    """Count this routine's previous runs; pause it at :data:`THRESHOLD`. Returns the refusal detail
    when the routine was paused (the caller must not run it), else None. Never raises."""
    if (flow_turn or {}).get("step") != "agent_run":
        return None
    scope = str(flow_turn.get("scope") or "")
    name = str(flow_turn.get("flow") or "")
    if scope != f"user:{subject}" or not name:
        return None                     # only a person's own routine; instance flows are the operator's
    try:
        with _LOCK:
            return _before_run(subject, name, unit_id, read=read, workspaces_dir=workspaces_dir,
                               client=client, store=store, session=session)
    except Exception:  # noqa: BLE001 — counting refusals is never worth a lost run
        log.exception("routine refusal count failed; the run goes ahead")
        return None


def _before_run(subject, name, unit_id, *, read, workspaces_dir, client, store,
                session=None) -> Optional[dict]:
    state = rf.load_flow_state(workspaces_dir, subject)
    entry = dict(state.get(name) or {})
    events = read(unit_id, str(entry.get("cursor") or ""))
    if events is None:
        return None
    entry.update(count_runs(entry, events))
    result = None
    if entry["count"] >= THRESHOLD:
        reason, count = entry["reason"], entry["count"]
        text = paused_reason(reason, count)
        client = client or rf.FlowsClient()
        flow = next((f for f in client.routines(subject) if f["name"] == name), None)
        if flow is not None:
            client.set_status(subject, flow["name"], flow["version"], "pause")
        entry.update({"reason": "", "count": 0, "paused_reason": text})
        rf.post_to_routine_chat(store, subject, name, f"This routine is {text}", session=session)
        log.warning("a routine was paused after %d runs refused (%s)", count, reason)
        result = {"refused": "routine_paused", "reason": reason, "note": text}
    state[name] = entry
    rf.save_flow_state(workspaces_dir, subject, state)
    return result
