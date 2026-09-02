"""THE READING half of the timeline (PRD decision 31) — rows in, `model.Event`s out.

Everything that can only be wrong at runtime lives here: which rows to scan, who the subject is,
and where the meetings table is. `model.py` holds the part the tests can pin.

Two scoping identifiers, always. See `model.concerns` for why one is never enough; this module is
what turns the single `subject` a caller passes into the pair.
"""
from __future__ import annotations

import os
import time
from typing import Callable, Optional

from flows_timeline.model import (Event, concerns, event_from_meeting, event_from_receipt,
                                  events_from_reaction, loads, merge, to_epoch)

# How many reaction rows one call may look at. A person's own share of them is small, but the table
# is org-wide, so the scan is bounded rather than the result: an unbounded ORDER BY over a table a
# simulator can fill with a hundred thousand rows is the shape of a route that works until the day
# it matters. Raise it with the window, never with the limit.
SCAN_ROWS = int(os.environ.get("VEXA_TIMELINE_SCAN_ROWS", "2000"))

DEFAULT_BACK_S = 14 * 86400
DEFAULT_AHEAD_S = 30 * 86400

_REACTION_COLS = ("reaction_id", "source_event_id", "event_type", "subject_refs", "flow",
                  "flow_version", "step", "status", "reason", "created_at", "updated_at")
_RECEIPT_COLS = ("effect_key", "reaction_id", "step", "state", "provider_ref", "result",
                 "attempted_at", "confirmed_at")


def _rows(db, sql: str, params: dict, cols: tuple) -> list[dict]:
    return [dict(zip(cols, r)) for r in db.execute(sql, params)]


def window(since=None, until=None, now: Optional[float] = None) -> tuple[float, float]:
    """The `[since, until]` a caller asked for, or the default one that straddles now.

    Straddling is the point: a timeline whose default window ends at `now` cannot answer the half
    of decision 31 that says *the next scheduled meetings with times*, and that half is the one the
    person cannot get from anywhere else.
    """
    now = time.time() if now is None else float(now)
    s = to_epoch(since)
    u = to_epoch(until)
    return (now - DEFAULT_BACK_S if s is None else s,
            now + DEFAULT_AHEAD_S if u is None else u)


def read_flows(db, *, uid: str = "", email: str = "", since: float, until: float,
               scan: int = SCAN_ROWS) -> list[Event]:
    """Every reaction and receipt in the window that CONCERNS this person.

    The reaction scan filters on `updated_at`, not `created_at`, and that is load-bearing: a
    reaction admitted a week ago whose minutes mail went out an hour ago has an old `created_at`
    and a fresh `updated_at`, and scanning on the former would drop the one event the person is
    most likely to be asking about. Every step run touches `updated_at`, so it is an upper bound on
    the receipts that hang off the row.
    """
    reactions = _rows(db, f"""SELECT {", ".join(_REACTION_COLS)} FROM reaction
                              WHERE updated_at >= :since AND created_at <= :until
                              ORDER BY updated_at DESC LIMIT {int(scan)}""",
                      {"since": since, "until": until}, _REACTION_COLS)
    mine = [r for r in reactions if concerns(loads(r["subject_refs"]), uid, email)]
    out: list[Event] = []
    for r in mine:
        out.extend(events_from_reaction(r))
    if not mine:
        return out
    ids = {r["reaction_id"]: r for r in mine}
    keys = {f"r{i}": rid for i, rid in enumerate(ids)}
    placeholders = ", ".join(f":{k}" for k in keys)
    receipts = _rows(db, f"""SELECT {", ".join(_RECEIPT_COLS)} FROM effect_receipt
                             WHERE reaction_id IN ({placeholders})""",
                     {k: v for k, v in keys.items()}, _RECEIPT_COLS)
    for rc in receipts:
        parent = ids.get(rc["reaction_id"]) or {}
        ev = event_from_receipt(rc, loads(parent.get("subject_refs")),
                                flow=str(parent.get("flow") or ""))
        if ev is not None:
            out.append(ev)
    return out


# ── the meetings half ────────────────────────────────────────────────────────────────────────────
#
# The meetings table lives in another service's database, so it is reached the way every step
# reaches it: over HTTP, with the person's own key. The key is CACHED per process — the preamble
# asks for a timeline on every dispatch (behind its own 60 s cache) and minting a fresh API token
# per ask would leave a token per person per minute in the identity database forever.

_KEY_CACHE: dict[str, str] = {}


def _user_key(uid: str) -> str:
    if uid not in _KEY_CACHE:
        from flows_steps.common import user_api_key
        _KEY_CACHE[uid] = user_api_key(str(uid))
    return _KEY_CACHE[uid]


def fetch_meetings(uid: str) -> list[dict]:
    """This person's meeting rows, or `[]`. NEVER raises: the flows half is the half that matters,
    and a gateway hiccup must degrade the timeline, not empty it."""
    uid = str(uid or "").strip()
    if not uid:
        return []
    try:
        from flows_steps.common import GATEWAY, http
        _st, body = http("GET", f"{GATEWAY}/meetings", {"X-API-Key": _user_key(uid)})
    except Exception:  # noqa: BLE001 — see docstring
        _KEY_CACHE.pop(uid, None)          # a rejected key is the likeliest cause; re-mint next call
        return []
    if isinstance(body, dict):
        rows = body.get("meetings", [])
    elif isinstance(body, list):
        rows = body
    else:
        rows = []
    return [m for m in rows if isinstance(m, dict)]


# ── identity ─────────────────────────────────────────────────────────────────────────────────────

def resolve_identity(subject: str, lookup: Optional[Callable[[str], dict]] = None) -> tuple[str, str]:
    """`(uid, email)` from either one. Fails SOFT: what could not be resolved comes back empty.

    A subject of all digits is a platform id; anything with an `@` is an address; anything else is
    refused by the caller. When admin-api cannot be reached the caller still gets the identifier it
    was given, so a timeline scoped by email keeps working while identity is down — half a scope is
    a smaller lie than no timeline.
    """
    subject = str(subject or "").strip()
    if not subject:
        return "", ""
    uid = subject if subject.isdigit() else ""
    email = subject.lower() if "@" in subject else ""
    if lookup is None:
        def lookup(path: str) -> dict:
            from flows_steps.common import ADMIN_API, http, require_admin_key
            _st, body = http("GET", f"{ADMIN_API}{path}",
                             {"X-Admin-API-Key": require_admin_key()})
            return body if isinstance(body, dict) else {}
    try:
        if uid and not email:
            email = str((lookup(f"/admin/users/{uid}") or {}).get("email") or "").lower()
        elif email and not uid:
            got = (lookup(f"/admin/users/email/{email}") or {}).get("id")
            uid = str(got) if got not in (None, "") else ""
    except Exception:  # noqa: BLE001 — see docstring
        pass
    return uid, email


# ── the whole thing ──────────────────────────────────────────────────────────────────────────────

def build_timeline(db, subject: str, *, since=None, until=None, limit: int = 20,
                   now: Optional[float] = None,
                   meetings: Optional[Callable[[str], list]] = fetch_meetings,
                   identity: Optional[Callable[[str], tuple]] = None) -> dict:
    """The route's answer: `now`, the resolved subject, and the merged events oldest-first.

    `meetings` and `identity` are injected so the whole thing can be exercised — and PROVEN against
    a real database — without a gateway, an identity service or a network. Pass `meetings=None` for
    the flows half alone.
    """
    now = time.time() if now is None else float(now)
    s, u = window(since, until, now=now)
    uid, email = (identity or resolve_identity)(subject)
    if not uid and not email:
        return {"now": None, "subject": subject, "uid": "", "email": "", "events": [],
                "unresolved": True}
    events = read_flows(db, uid=uid, email=email, since=s, until=u)
    if meetings is not None and uid:
        for row in meetings(uid):
            ev = event_from_meeting(row)
            if ev is not None:
                events.append(ev)
    ordered = merge(events, since=s, until=u, limit=limit)
    from flows_timeline.model import iso
    return {
        "now": iso(now),
        "now_epoch": round(now, 3),
        "subject": subject,
        "uid": uid,
        "email": email,
        "since": iso(s),
        "until": iso(u),
        "count": len(ordered),
        "events": [e.as_dict() for e in ordered],
    }
