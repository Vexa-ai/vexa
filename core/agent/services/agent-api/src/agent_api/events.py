"""events.py — the event ingress mapper (event.v1 → unit.v1 Invocation).

Generic + tool-agnostic. An external event becomes a ``unit.v1`` Invocation and the SAME Dispatcher
fans it out — a new source is a new ``name``, not a new code path. agent-api knows NOTHING about
email / calendar / news / etc.: those are TOOLS the unit reaches through its toolbelt
(``unit.v1.tools`` → ``--allowedTools`` + MCP), never primitives here. The Event carries an OPAQUE
``source`` ref; the unit's plan (carried inline on the Event, or — later — supplied by a matching
event-routine) tells the unit which tool to use to RESOLVE that ref in-container. No domain bytes and
no domain code cross this seam.
"""
from __future__ import annotations

from . import contracts

# Which unit.v1 trigger each event name fires under (default: a generic ``event`` worker). The only
# non-default is post-meeting, which is a transcript beat.
_TRIGGER = {"meeting.completed": "transcription"}


def event_to_invocation(event: dict, *, workspace_repo_for, workspace_ref: str = "main") -> dict:
    """Map a validated ``event.v1`` Event to a tool-agnostic ``unit.v1`` Invocation. The plan must be
    supplied by the Event (inline) — agent-api does not know any tool's default behaviour. Raises
    ``ValidationError`` on a bad envelope, ``ValueError`` when no plan is carried."""
    contracts.validate_event(event)
    name = event["name"]
    subject = event["subject"]

    # Context: an opaque source ref, or a meeting ref — never resolved here (a tool does that).
    if event.get("meeting"):
        context = {"kind": "meeting", "meeting": dict(event["meeting"])}
    elif event.get("source"):
        context = {"kind": "generic", "ref": dict(event["source"])}
    else:
        context = {"kind": "generic"}

    plan = event.get("plan") or {}
    plan = {k: v for k, v in plan.items() if v is not None}
    if not (plan.get("prompt") or plan.get("ref")):
        raise ValueError(
            f"event {name!r} carries no plan; the event source (or a matching event-routine) must "
            "supply the plan — agent-api does not embed per-tool behaviour"
        )

    invocation = {
        "trigger": _TRIGGER.get(name, "event"),
        "subject": subject,
        "workspace_repo": workspace_repo_for(subject),
        "workspace_ref": workspace_ref,
        "context": context,
        "plan": plan,
        "lifecycle": "oneshot",
        "output": {"topic": f"unit:{name.replace('.', '-')}-{subject}:out", "modes": ["sse"]},
        "tools": [],
    }
    contracts.validate_unit_invocation(invocation)
    return invocation
