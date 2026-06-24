"""units.py — construct the one canonical ``unit.v1`` DISPATCH envelope.

Every trigger source (chat ``/api/chat`` now-dispatch, the event ingress, the routine compiler, the
live-meeting trigger) funnels through ``make_dispatch`` so the envelope is built in ONE place and stays
conformant. ``trust`` + ``output`` are DERIVED here, never stored:

- the workspace **mode** follows input-trust (chat/schedule = ``rw``; external event/transcription =
  ``ro`` / propose-only — see [governance]);
- the output **Stream** is ``unit:<id>:out`` (and the interactive input is ``unit:<id>:in``);
- the **workload id** is stable per (subject, trigger) so chat keeps touching one warm unit and a live
  meeting keys on its session — continuity is the session file, not a warm container.
"""
from __future__ import annotations

import hashlib
import json

RUNNER = "claude-code"


def launcher_for(trigger: str, subject: str, *, ref: str | None = None) -> str:
    """Derive the launcher (who/what triggered the dispatch — it holds the delegation grant)."""
    if trigger == "message":
        return f"user:{subject}"
    if trigger == "scheduled":
        return f"schedule:{ref or subject}"
    if trigger == "transcription":
        return "integration:meetings"
    return f"integration:{ref or 'event'}"  # event


def mode_for(trigger: str) -> str:
    """Input-trust → write access (governance). Trusted triggers (you, in chat; your own schedule) write
    ``rw``; untrusted external input (event/web/transcription) is ``ro`` → propose-only."""
    return "rw" if trigger in ("message", "scheduled") else "ro"


def entrypoint(*, inline: str | None = None, path: str | None = None) -> dict:
    """A fresh ``start`` — an inline ask (chat / inline plan) or a workspace plan path."""
    if inline is not None:
        return {"entrypoint": {"inline": inline}}
    if path:
        return {"entrypoint": {"path": path}}
    raise ValueError("entrypoint needs inline or path")


def session_start(ref: str) -> dict:
    """A resumed ``start`` — a path to the agent's session file in the (rw) workspace."""
    return {"session": {"ref": ref}}


def make_dispatch(
    *,
    subject: str,
    trigger: str,
    start: dict,
    workspaces: list[dict] | None = None,
    tools: list[str] | tuple[str, ...] = (),
    context: dict | None = None,
    launcher: str | None = None,
    runner: str = RUNNER,
    token: str | None = None,
) -> dict:
    """Build a conformant ``unit.v1`` dispatch. Defaults the workspace list to the subject's personal
    workspace at the trust-derived mode; the caller may pass an explicit list (system ro + user rw …)."""
    inv: dict = {
        "identity": {"subject": subject, "launcher": launcher or launcher_for(trigger, subject)},
        "runner": runner,
        "workspaces": workspaces or [{"id": subject, "mode": mode_for(trigger)}],
        "trigger": trigger,
        "start": start,
    }
    if token is not None:
        inv["identity"]["token"] = token
    if tools:
        inv["tools"] = list(tools)
    if context is not None:
        inv["context"] = context
    return inv


def dispatch_id(inv: dict) -> str:
    """A stable workload id for the dispatch. A live meeting keys on its ``session_uid`` (one dispatch
    per meeting); chat on (subject) so every turn touches the one warm chat unit; others on a digest of
    (subject, trigger, start)."""
    ctx = inv.get("context") or {}
    meeting = ctx.get("meeting") if ctx.get("kind") == "meeting" else None
    if meeting and meeting.get("session_uid"):
        return f"agent-meet-{meeting['session_uid']}"
    subject = inv["identity"]["subject"]
    if inv["trigger"] == "message":
        return f"agent-{subject}-chat"  # one warm chat unit per person (TTL-reaped); continuity = session file
    digest = hashlib.sha1(
        f"{subject}|{inv['trigger']}|{json.dumps(inv['start'], sort_keys=True)}".encode()
    ).hexdigest()[:10]
    return f"agent-{subject}-{inv['trigger']}-{digest}"


def output_topic(unit_id: str) -> str:
    """The per-dispatch output Stream — derived, never stored (the Stream primitive)."""
    return f"unit:{unit_id}:out"


def input_topic(unit_id: str) -> str:
    """The per-dispatch input Stream — interactive messages to a live dispatch (the duplex evolution)."""
    return f"unit:{unit_id}:in"
