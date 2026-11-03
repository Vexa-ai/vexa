"""signal_scope.py — what a meeting signal's turn may write: the meeting's page, and nothing else.

A `meeting.signal` wakes the meeting's agent in the meeting's chat (transcript signals, 2026-10-15).
What woke it is a buffer of things people SAID in a call, and anyone in a call can speak an
instruction. So the turn is untrusted (unit.v1 trigger `signal`): its mounts are read-only, its
token's ceiling names no workspace, and agent-api lets its tools write exactly two things — the
meeting's page (`PUT /api/workspace/file` on that one path) and the page's `listen:` brief
(`meeting_listen`). Every other write is refused here, before its route runs, with an instruction to
propose it instead.

ONE WRITER, ONE READER. The dispatcher records the page against the unit it is dispatching
(`record`), the only writer; the routes read it back by the unit id the caller's signed delegation
token carries (`x-user-delegation-unit`). The unit id is a correlation on its own — the grant is
agent-api's record of what it dispatched, never anything the caller asserts.

FAILS CLOSED. A signal unit with no recorded page (lost, expired, a store that cannot answer) may
write nothing; a request whose matched route cannot be read is treated as a write.

A ROOM'S SIGNAL TURN IS HELD THE SAME WAY (D27, H1, 2026-10-17). It used to be exempt — the room
agent writing W under the room's grant — and anyone audible on a call linked to the room, member or
not, could speak instructions into a turn that wrote the workspace. Now a room's signal turn writes
only its call's note (the page `ingress` records for it) and proposes everything else.

AN INBOUND MAIL'S TURN TOO (D25). A turn an email started (its unit `…-mail`) writes one page, the
thread's reply file `mail_outbox/<session>.md` that flows mails back, and proposes everything else.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Callable, Optional

from fastapi import Request

from control_plane.ceiling import refusal, refused
from control_plane.identity_token import DELEGATION_HEADERS
from shared import units

logger = logging.getLogger("agent_api.signal_scope")

UNIT_HEADER = DELEGATION_HEADERS["unit"]
#: How long a recorded page outlives its dispatch: a warm signal unit is re-recorded on every turn,
#: so this only has to outlive one turn and its warm window.
GRANT_TTL_S = 6 * 3600

#: The writes a signal's turn may reach at all; each still checks the page itself.
PAGE_WRITE = ("PUT", "/api/workspace/file")
LISTEN_WRITE = ("POST", "/api/meeting/listen")
#: …and the proposal door: what a signal's turn may not do, it proposes (`propose`).
PROPOSE = ("POST", "/api/claims")
#: THE ONE ALLOW-LIST, by the verb a worker calls (friction 5, 2026-10-16: the refusal named
#: meeting_listen while the check refused it). The gate, the per-door checks and the refusal's own
#: words are all read off this table and `allowed_verbs`, so they cannot disagree again.
DOORS = {"workspace_write": PAGE_WRITE, "meeting_listen": LISTEN_WRITE, "propose": PROPOSE}
ALLOWED_WRITES = frozenset(DOORS.values())
_VERB_OF = {door: verb for verb, door in DOORS.items()}
READS = frozenset({"GET", "HEAD", "OPTIONS"})


def allowed_verbs(grant: Optional[dict]) -> tuple:
    """The write verbs THIS signal turn may call, given its recorded grant: its meeting's page
    (`workspace_write`) when a page was found, its meeting's brief (`meeting_listen`) whenever the
    turn belongs to a meeting, and always `propose`. A turn with no grant (a room's sensor, a lost
    record) may only propose."""
    g = grant or {}
    out = []
    if g.get("path"):
        out.append("workspace_write")
    if g.get("meeting"):
        out.append("meeting_listen")
    out.append("propose")
    return tuple(out)


def refusal_for(grant: Optional[dict]) -> dict:
    """The refusal a signal turn reads, naming exactly the verbs `allowed_verbs` admits for it."""
    verbs = allowed_verbs(grant)
    g = grant or {}
    may = []
    if "workspace_write" in verbs:
        if g.get("meeting"):
            may.append(f"meeting {g['meeting']}'s page `{g['path']}` (workspace_write, that path only)")
        else:   # an inbound mail's turn (D25): its one page is the thread's reply file
            may.append(f"the email reply file `{g['path']}` (workspace_write, that path only)")
    if "meeting_listen" in verbs:
        may.append(f"meeting {g['meeting']}'s listening brief (meeting_listen, meeting {g['meeting']})")
    if may:
        source = "what people said in a meeting" if g.get("meeting") else "an email"
        head = (f"This turn was started by {source}, outside your person's own chat, so it may write only "
                + " and ".join(may) + ". ")
    else:
        head = "This turn was started by what people said, so it writes nothing itself. "
    return refusal("signal_scope", head + "Propose anything else to the person with `propose`, in "
                   "one line, and stop.")


#: The refusal of a turn with no recorded grant (kept for callers that name it).
REFUSAL = refusal_for(None)

_LOCAL: dict = {}
_LOCK = threading.Lock()


def grant_key(unit_id: str) -> str:
    return f"unit:{unit_id}:signal-page"


def _clean(page: Optional[dict]) -> Optional[dict]:
    if not isinstance(page, dict):
        return None
    path = str(page.get("path") or "").strip()
    meeting = str(page.get("meeting") or "").strip()
    if not path and not meeting:
        return None
    # A meeting with no page found yet still grants its brief (`meeting_listen`), never a page write.
    return {"meeting": meeting, "path": path, "workspace": str(page.get("workspace") or "").strip()}


def record(r, unit_id: str, page: Optional[dict]) -> None:
    """Record (or, with no page, clear) the one page ``unit_id`` may write. Never raises."""
    grant = _clean(page)
    with _LOCK:
        if grant is None:
            _LOCAL.pop(unit_id, None)
        else:
            _LOCAL[unit_id] = grant
    if r is None:
        return
    try:
        if grant is None:
            r.delete(grant_key(unit_id))
        else:
            r.set(grant_key(unit_id), json.dumps(grant), ex=GRANT_TTL_S)
    except Exception as exc:  # noqa: BLE001 — the in-process copy still answers on this replica
        logger.warning("signal page for %s not recorded (%s)", unit_id, type(exc).__name__)


def reader(redis_url: Optional[str]) -> Callable[[str], Optional[dict]]:
    """``read(unit_id) -> grant | None`` over the service redis (the in-process copy without one)."""
    client = None
    if redis_url:
        try:
            import redis  # noqa: PLC0415

            client = redis.from_url(redis_url, decode_responses=True)
        except Exception:  # noqa: BLE001
            client = None

    def read(unit_id: str) -> Optional[dict]:
        if client is not None:
            try:
                raw = client.get(grant_key(unit_id))
                if raw:
                    return _clean(json.loads(raw))
            except Exception as exc:  # noqa: BLE001 — fall back to this replica's copy
                logger.warning("signal page for %s unreadable (%s)", unit_id, type(exc).__name__)
        with _LOCK:
            return dict(_LOCAL[unit_id]) if unit_id in _LOCAL else None

    return read


def signal_unit(request: Request) -> str:
    """The caller's unit id when it is an untrusted turn held to one page — a meeting signal's
    (a room's included, D27) or an inbound mail's (D25) — else ``""``."""
    unit = (request.headers.get(UNIT_HEADER) or "").strip()
    return unit if (units.is_signal_unit(unit) or units.is_mail_unit(unit)) else ""


def _matched(request: Request):
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return (request.method.upper(), path) if path else None


def gate(read_grant: Callable[[str], Optional[dict]]):
    """The app-level dependency: a signal's turn reads anything its ceiling allows and writes only
    through the doors above — the page and the brief, each of which checks it is this meeting's
    (`require_page`, `require_meeting`), and a proposal."""
    def signal_gate(request: Request) -> None:
        unit = signal_unit(request)
        if not unit or request.method.upper() in READS:
            return
        grant = read_grant(unit)
        verb = _VERB_OF.get(_matched(request))
        if verb is not None and verb in allowed_verbs(grant):
            return
        raise refused(request, 403, refusal_for(grant), reason="signal_scope", unit=unit)
    return signal_gate


def _same_workspace(granted: str, slug: Optional[str], subject: str) -> bool:
    """The page's workspace against the one a write resolved to. ``""``/``None`` and the person's own
    id are both the person's home."""
    a = str(granted or "").strip()
    b = str(slug or "").strip()
    home = {"", str(subject or "")}
    return (a in home and b in home) or a == b


def require_page(request: Request, read_grant, *, slug: Optional[str], path: str, subject: str) -> None:
    """Refuse (403 `signal_scope`) a signal turn's page write anywhere but its meeting's page."""
    unit = signal_unit(request)
    if not unit:
        return
    grant = read_grant(unit)
    if (grant is None or not grant["path"] or str(path or "").strip() != grant["path"]
            or not _same_workspace(grant["workspace"], slug, subject)):
        raise refused(request, 403, refusal_for(grant), reason="signal_scope", unit=unit,
                      path=str(path or ""))


def require_meeting(request: Request, read_grant, meeting: str) -> None:
    """Refuse (403 `signal_scope`) a signal turn's brief write for any meeting but its own."""
    unit = signal_unit(request)
    if not unit:
        return
    grant = read_grant(unit)
    if grant is None or not grant["meeting"] or grant["meeting"] != str(meeting or "").strip():
        raise refused(request, 403, refusal_for(grant), reason="signal_scope", unit=unit,
                      meeting=str(meeting))
