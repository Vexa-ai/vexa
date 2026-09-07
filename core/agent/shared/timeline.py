"""TEMPORAL AWARENESS IN CONTEXT, every turn — PRD decision 31 §1.

Founder, 2026-09-02 15:5xZ: *"does the agent have temporal awareness of the last events and future
events? scheduled meetings, the things that actually get logged in the flows data"*. It did not: it
could look things up when asked, and had no sense of now, of recent, or of next. An agent with no
now answers "this morning" out of the training data.

This is the TEMPORAL half of the per-dispatch fact block, kept as its own function on purpose. The
other half — which chat, which meeting, which page is open (decision 30 §2) — is composed
elsewhere and by someone else; a block with two authors is a block that loses one author's edits,
which is the one invariant `graph/sg/Operating-Loops.md` states in a single line. Whoever composes
the human-surface block calls `timeline_preamble()` and appends what it returns.

WHERE THE WORK HAPPENS. Nothing here reads a database, and nothing here formats a time. The route
(`flows-api GET /timeline?format=preamble`) does both, because the person's zone lives in their
`.settings.json` — which flows already reads — and because the control-MCP `timeline` tool renders
the same payload through the same function. Two renderers is how a chat and a machinery note end up
disagreeing about when a meeting was.

COST. One HTTP call per subject per minute, three-second timeout, and a FAILURE IS CACHED TOO: a
flows-api that is down must cost this worker three seconds an hour, not three seconds a turn.

CREDENTIAL. `VEXA_FLOWS_TIMELINE_KEY` is a read-only key that opens exactly this route (see
`flows_api._timeline_key`). The operator key also works, and a deployment that puts THAT in a
worker container has handed every worker the ability to submit flows — so the narrow one is the one
to set. No key ⇒ no block, silently: a worker that cannot see the timeline behaves exactly as it
did before the timeline existed.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

CACHE_TTL_S = float(os.environ.get("VEXA_TIMELINE_CACHE_S", "60"))
TIMEOUT_S = float(os.environ.get("VEXA_TIMELINE_TIMEOUT_S", "3"))
BACK = int(os.environ.get("VEXA_TIMELINE_BACK", "5"))
AHEAD = int(os.environ.get("VEXA_TIMELINE_AHEAD", "5"))

# subject -> (expires_at, rendered block). "" is a legitimate cached value: see COST above.
_CACHE: dict[str, tuple[float, str]] = {}


def _api() -> str:
    return (os.environ.get("VEXA_FLOWS_API_URL") or "").rstrip("/")


def _key() -> str:
    return ((os.environ.get("VEXA_FLOWS_TIMELINE_KEY") or "").strip()
            or (os.environ.get("VEXA_FLOWS_API_KEY") or "").strip())


def subject() -> str:
    """WHO this dispatch acts for. `VEXA_OWNER` is the dispatcher's own word for it (dispatch.py:
    *"Quota keys on the PERSON (VEXA_OWNER = subject)"*); the principal's address is the fallback
    for a dispatch that predates it, and the route accepts either form."""
    return ((os.environ.get("VEXA_OWNER") or "").strip()
            or (os.environ.get("VEXA_PRINCIPAL_EMAIL") or "").strip())


def fetch(uid: str, *, back: int = BACK, ahead: int = AHEAD, timeout: float = TIMEOUT_S) -> str:
    """The rendered block for one person, or "". NEVER raises — see the module docstring."""
    api, key = _api(), _key()
    if not api or not key or not uid:
        return ""
    # The window is the route's default (14 back, 30 forward); `limit` is what the block can hold.
    url = f"{api}/timeline?subject={urllib.parse.quote(str(uid))}&format=preamble&limit={back + ahead}"
    req = urllib.request.Request(url, method="GET", headers={"X-Flows-Operator-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode() or "{}")
    except Exception as e:  # noqa: BLE001 — a missing timeline degrades the turn, never fails it
        log.debug("timeline unavailable for %s: %s: %s", uid, type(e).__name__, e)
        return ""
    text = body.get("text") if isinstance(body, dict) else None
    return text if isinstance(text, str) else ""


def timeline_preamble(uid: str = "", *, now: float | None = None) -> str:
    """`now` in the person's zone, their last few events and their next few — for the turn prompt.

    Cached per subject for `CACHE_TTL_S` (60 s): the block is refreshed often enough that a meeting
    that just ended is in the next turn's context, and rarely enough that a person typing three
    messages in a row does not pay for three round-trips.
    """
    uid = str(uid or subject() or "").strip()
    if not uid:
        return ""
    now = time.time() if now is None else float(now)
    hit = _CACHE.get(uid)
    if hit and hit[0] > now:
        return hit[1]
    block = fetch(uid)
    _CACHE[uid] = (now + CACHE_TTL_S, block)
    return block


def invalidate(uid: str = "") -> None:
    """Forget one subject's cached block, or all of them. For tests and for a caller that has just
    made the timeline wrong (a meeting booked, a report sent) and wants the next turn to see it."""
    if uid:
        _CACHE.pop(str(uid), None)
    else:
        _CACHE.clear()
