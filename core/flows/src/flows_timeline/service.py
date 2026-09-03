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
                                  events_from_reaction, iso, loads, merge, to_epoch)

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


#: The operator projection's columns — what `GET /reactions` has always returned.
LIST_COLS = ("reaction_id", "flow", "flow_version", "step", "status", "attempt", "reason",
             "next_run_at")


def list_reactions(db, *, subject: str = "", status: str = "", limit: int = 100,
                   scan: int = SCAN_ROWS, identity: Optional[Callable] = None):
    """The operator projection, optionally SCOPED TO ONE PERSON. ``None`` when nobody answers to
    ``subject``.

    Scoping lives here rather than in the route because it is the part that can be wrong: the
    control MCP's `whats_waiting` and `reactions_list` were reading this table unscoped and
    reporting the whole instance's reactions — flow names, step names and failure reasons — as one
    person's queue, and handing out every reaction id along with them (R-D07, R-D12).

    It scopes on the uid AND the email, for the reason `model.concerns` documents: the invite
    lineage carries an organizer address and no uid, the completed lineage carries a uid and no
    address, and matching on one of them silently returns half the rows. `subject_refs` is a JSON
    blob with no index to push the predicate into, so the SCAN is bounded and the result is
    filtered — the same shape `read_flows` uses, and for the same reason.
    """
    where, params = "", {}
    if status and status.isalpha():
        where, params = " WHERE status = :st", {"st": status}
    if not str(subject or "").strip():
        return _rows(db, f"SELECT {', '.join(LIST_COLS)} FROM reaction{where} "
                         f"ORDER BY created_at DESC LIMIT {int(limit)}", params, LIST_COLS)
    uid, email = (identity or resolve_identity)(subject)
    if not uid and not email:
        return None
    cols = LIST_COLS + ("subject_refs",)
    rows = _rows(db, f"SELECT {', '.join(cols)} FROM reaction{where} "
                     f"ORDER BY created_at DESC LIMIT {int(scan)}", params, cols)
    mine = [r for r in rows if concerns(loads(r["subject_refs"]), uid, email)]
    return [{k: r[k] for k in LIST_COLS} for r in mine[:limit]]


#: What `reaction_concerns` answers. Three outcomes, not two: "there is no such reaction" and
#: "that one is not yours" have different fixes for the caller, and collapsing them into one
#: refusal is how a person retries forever against an id that never existed.
REACTION_FOUND, REACTION_MISSING, REACTION_NOT_YOURS = "ok", "not_found", "not_yours"


def reaction_concerns(db, reaction_id: str, *, subject: str,
                      identity: Optional[Callable] = None) -> str:
    """Does this ONE reaction concern ``subject``? — the ownership check behind the signal verbs.

    R-D07 was two holes, and only one of them is a read: `reactions_list` handed every id out
    instance-wide, and `reaction_signal` then posted with the lane's admin key without ever asking
    whether the reaction was the caller's. `cancel` on a stranger's scheduled join destroyed their
    pending work.

    The answer is OWNERSHIP, not authority. A person cancelling the join THEY scheduled is the
    product — `bot_schedule` mints that reaction and the same person has to be able to stop it — so
    an operator gate here would break the ordinary path to close an unusual one. This asks the
    narrower question instead, on the same `concerns` predicate `list_reactions` uses, and it is a
    DIRECT lookup rather than a scan: an old reaction outside the projection's window is still
    yours, and a scoping check with a horizon would start refusing it.
    """
    rows = _rows(db, "SELECT reaction_id, subject_refs FROM reaction WHERE reaction_id = :rid",
                 {"rid": str(reaction_id)}, ("reaction_id", "subject_refs"))
    if not rows:
        return REACTION_MISSING
    uid, email = (identity or resolve_identity)(subject)
    if not uid and not email:
        return REACTION_NOT_YOURS          # FAIL CLOSED: a subject nobody answers to owns nothing
    return (REACTION_FOUND if concerns(loads(rows[0]["subject_refs"]), uid, email)
            else REACTION_NOT_YOURS)


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


# ── friction (PRD 40.9 open-decision 8) ─────────────────────────────────────────────────────────
# THE READ HALF of the sink. `POST /friction` writes one `friction.reported` reaction per report
# (via `admit()`, through the one-step `friction_log` flow — see `flows_defs/production.py`'s
# `record_friction`); this is the read of it, scoped exactly like `list_reactions` above and for
# the same reason (`model.concerns` needs both identifiers). It is a SEPARATE query rather than a
# filter added to `read_flows`/`merge`: a friction report is not a moment on a person's calendar
# day — it carries no title, no meeting, nothing `Event` renders — so folding it into the timeline
# proper would mean inventing a description for a shape that already has its own reader.

_FRICTION_COLS = ("reaction_id", "subject_refs", "created_at")


def friction_for_subject(db, *, subject: str = "", since: float = 0.0, limit: int = 40,
                         scan: int = SCAN_ROWS,
                         identity: Optional[Callable] = None) -> Optional[list[dict]]:
    """Every `friction.reported` row at or after `since`, newest first. ``None`` when `subject`
    was given and nobody answers to it (mirrors `list_reactions`'s contract). `subject=""` reads
    every subject's reports — the whole-instance view stays behind the caller's own authorization
    (an operator, in `flows_api.friction_so_far`); this function does not gate it itself, the same
    division `list_reactions` already draws between scoping and authorizing.
    """
    rows = _rows(db, f"""SELECT {", ".join(_FRICTION_COLS)} FROM reaction
                         WHERE event_type = :et AND created_at >= :since
                         ORDER BY created_at DESC LIMIT {int(scan)}""",
                {"et": "friction.reported", "since": since}, _FRICTION_COLS)
    if str(subject or "").strip():
        uid, email = (identity or resolve_identity)(subject)
        if not uid and not email:
            return None
        rows = [r for r in rows if concerns(loads(r["subject_refs"]), uid, email)]
    out = []
    for r in rows[:max(1, int(limit))]:
        refs = loads(r["subject_refs"])
        at = to_epoch(r["created_at"]) or 0.0
        ctx = {k: refs[k] for k in ("tool", "meeting_id", "deployment", "worker_image", "kind")
              if refs.get(k)}
        out.append({
            "id": refs.get("friction_id") or r["reaction_id"],
            "at": iso(at), "at_epoch": round(at, 3),
            "subject": refs.get("uid", ""), "session": refs.get("session", ""),
            "severity": refs.get("severity", ""),
            "tried": refs.get("what_i_tried", ""), "happened": refs.get("what_happened", ""),
            "context": ctx,
        })
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
        from flows_steps.common import http, meetings_door
        # `meetings_door()` raises `MeetingsDomainAbsent` where the domain is not deployed, and it
        # lands in the same `except` as a gateway hiccup — deliberately one policy, not two: for a
        # READ whose contract is already "degrade, never empty", an absent domain and an
        # unreachable one produce the same honest answer (no meetings in the window). The
        # distinction that matters is on the WRITE side, where a step must not knock at all.
        _st, body = http("GET", f"{meetings_door()}/meetings", {"X-API-Key": _user_key(uid)})
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
