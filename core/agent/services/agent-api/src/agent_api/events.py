"""events.py — the event ingress mapper (event.v1 → unit.v1 DISPATCH).

Generic + tool-agnostic. An external event becomes a ``unit.v1`` dispatch and the SAME Dispatcher fans
it out — a new source is a new ``name``, not a new code path. agent-api knows NOTHING about
email / calendar / news: those are TOOLS the unit reaches through its toolbelt, never primitives here.
The Event carries an OPAQUE ``source`` ref; the plan (carried inline on the Event, or — later — by a
matching event-routine) tells the unit which tool to RESOLVE it with. No domain bytes cross this seam.

An event is **untrusted** input (attacker-controllable) ⇒ the dispatch mounts the workspace ``ro`` and
the agent proposes (governance derives this from the trigger via ``units.mode_for``).
"""
from __future__ import annotations

from . import contracts
from .units import entrypoint, make_dispatch

# Which unit.v1 trigger each event name fires under (default: a generic ``event`` worker). The only
# non-default is post-meeting, which is a transcript beat.
_TRIGGER = {"meeting.completed": "transcription"}


def event_to_invocation(event: dict, *, workspace_ref: str = "main") -> dict:
    """Map a validated ``event.v1`` Event to a tool-agnostic ``unit.v1`` dispatch. The plan must be
    supplied by the Event (inline) — agent-api embeds no per-tool default. Raises ``ValidationError`` on
    a bad envelope, ``ValueError`` when no plan is carried."""
    contracts.validate_event(event)
    name = event["name"]
    subject = event["subject"]
    trigger = _TRIGGER.get(name, "event")

    # Context: a meeting ref, or an opaque source ref — never resolved here (a tool does that).
    if event.get("meeting"):
        context = {"kind": "meeting", "meeting": dict(event["meeting"])}
        launcher = "integration:meetings"
    elif event.get("source"):
        context = {"kind": "generic", "ref": dict(event["source"])}
        launcher = f"integration:{name.split('.')[0]}"
    else:
        context = {"kind": "generic"}
        launcher = f"integration:{name.split('.')[0]}"

    plan = {k: v for k, v in (event.get("plan") or {}).items() if v is not None}
    if not (plan.get("prompt") or plan.get("ref")):
        raise ValueError(
            f"event {name!r} carries no plan; the event source (or a matching event-routine) must "
            "supply the plan — agent-api does not embed per-tool behaviour"
        )
    start = entrypoint(inline=plan["prompt"]) if plan.get("prompt") else entrypoint(path=plan["ref"])

    invocation = make_dispatch(
        subject=subject, trigger=trigger, start=start, context=context, launcher=launcher,
    )
    contracts.validate_unit_invocation(invocation)
    return invocation
