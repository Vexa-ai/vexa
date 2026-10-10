"""tool_access.py — a turn that outlives its tool access ends with a typed fault, never quietly (P18).

A turn's harness attaches the vexa MCP with the delegation token it found when the turn started, and
keeps that token for the whole turn: a running Claude CLI, Codex app server or OpenAI loop does not
re-read its attachment. agent-api replaces a live unit's token at half its life
(``control_plane.delegation_refresh``), so a turn has the other half — 900 s at the default — before
the token it holds expires. A turn that runs longer gets its vexa tool calls refused by the gateway
(identity answers an expired delegation with 401), and without this the model would see a tool error
and carry on without its tools.

:func:`watch` sits on the turn's event stream. It knows the ``exp`` of the token the turn attached
with. When a vexa MCP call (``mcp__vexa__…``) fails at or after that ``exp``, the token had expired
when the gateway answered — identity refuses every call an expired token makes — so the failure is
the turn's tool access, and the turn's ``done`` is marked failed with a typed fault::

    {"source": "vexa-tools", "kind": "access_expired", "status": 401,
     "detail": "...", "remedy": "..."}

The terminal renders it like every other fault (``clients/terminal/src/surfaces/faults.ts``), with a
Retry: the next turn attaches with the unit's current token.
"""
from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone
from typing import Callable, Iterable, Iterator, Optional

SOURCE = "vexa-tools"
ACCESS_EXPIRED = "access_expired"
#: The prefix every vexa MCP tool carries in a turn's events, on every harness.
TOOL_PREFIX = "mcp__vexa__"


def token_exp(token: str) -> Optional[int]:
    """The ``exp`` a delegation token states, read without its key: it is this worker's own token and
    the value only says when the gateway will stop accepting it. None for anything unreadable."""
    try:
        body = str(token or "").split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        exp = claims.get("exp") if isinstance(claims, dict) else None
        return int(exp) if isinstance(exp, int) and not isinstance(exp, bool) else None
    except (IndexError, ValueError, TypeError):
        return None


def fault(exp: int) -> dict:
    """The typed fault for a turn whose vexa tool calls were refused after its token expired."""
    at = datetime.fromtimestamp(exp, tz=timezone.utc).strftime("%H:%M:%S UTC")
    return {
        "source": SOURCE,
        "kind": ACCESS_EXPIRED,
        "status": 401,
        "detail": (f"This turn ran past its tool access, which expired at {at}; the Vexa tool "
                   "calls it made after that were refused, so its answer may be missing what they "
                   "would have returned."),
        "remedy": "Send it again: the next turn starts with fresh tool access.",
    }


def watch(events: Iterable[dict], exp: Optional[int], *,
          now: Callable[[], float] = time.time) -> Iterator[dict]:
    """``events`` unchanged, except that the turn's ``done`` carries :func:`fault` (and ``ok: False``)
    when a vexa MCP call failed at or after ``exp``. A ``done`` that already names a fault keeps it:
    the first failure is the one the person is told about."""
    if exp is None:
        yield from events
        return
    vexa_calls: set = set()
    refused = False
    for ev in events:
        kind = ev.get("type") if isinstance(ev, dict) else None
        if kind == "tool-call" and str(ev.get("tool") or "").startswith(TOOL_PREFIX):
            vexa_calls.add(ev.get("callId"))
        elif (kind == "tool-result" and ev.get("callId") in vexa_calls and ev.get("ok") is False
              and now() >= exp):
            refused = True
        elif kind == "done" and refused and not ev.get("fault"):
            ev = {**ev, "ok": False, "fault": fault(exp)}
        yield ev
