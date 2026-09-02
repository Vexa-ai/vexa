"""meeting_room.py — the post-meeting ROOM: the read-only mounts of a meeting's other attendees.

WHY THIS EXISTS. A person's ``personal`` workspace (working name **desk**) is NOT private: it is
company knowledge that happens to be held by one person, and the company's agents may read it for a
meeting that person was in. Only ``_system`` stays private. So the post-meeting run — the one agent
turn that writes ONE shared write-up for everybody who was in the room — needs a wider mount stack
than a normal chat turn: ``_global``, the group-or-organizer workspace read-write (the dispatch's own
subject/active set — this module does not choose it), and **other attendees' desks READ-ONLY**.

THE ONE CONSTRAINT THAT MATTERS. **Room membership is SERVER-VERIFIED, never caller-asserted.** A
caller that could hand ``/api/chat`` a list of workspaces to mount would be able to read any other
user's desk by naming it — the whole isolation model, defeated by a request body.

PROPOSE-AND-VERIFY. The caller names THE MEETING (``ChatBody.room_meeting_id``, a meetings-domain row
id) and MAY additionally PROPOSE a subject list — flows holds the transcript, so it is the only place
that knows who actually SPOKE and in what order of speaking time. The server resolves the meeting's
own roster from meeting-api and mounts **the intersection**: a proposal can therefore only ever
NARROW the room, never widen it. A buggy or compromised caller cannot name a subject that was not in
the room, and cannot lift the cap past the server's own ceiling. With no proposal the room is the
whole verified roster, still capped.

THE GATES, in order, each fail-CLOSED:

  1. **Caller tier** (in ``api._resolve_room``) — the internal-tier secret. The room is a
     flows/operator capability, not an end-user one.
  2. **Entitlement** — reuses the EXISTING meeting access check (``api._meeting_owner_lookup`` →
     meeting-api ``GET /meetings/{id}``, which evaluates its own access union in SQL and 404s a row
     the caller may not read). No second authorisation rule is invented here.
  3. **Ownership** (``verified_subjects``) — the row must be the caller's OWN meeting (``user_id ==
     requester`` and not ``shared``). A transcript-share RECIPIENT passes gate 2 but is refused here:
     the roster is the owner's to see (meeting-api strips ``transcript_viewers`` for everyone else),
     and a recipient opening a room would turn one share into a read of every other attendee's desk.
  4. **Membership** (``verified_subjects``) — the room's ceiling is ``data.transcript_viewers``, the
     meeting's reader roster as meeting-api maintains it: the user ids that redeemed this meeting's
     share links. They are already SUBJECTS, so nothing here guesses a person from a name or an
     email — a wrong guess would mount the wrong human's notes.
  5. **Narrowing** (``select_room``) — intersect with the caller's proposal when given, keep the
     caller's order (speaking time), then cap.

KNOWN NARROWNESS, stated rather than papered over: an attendee who has not redeemed their share link
is not in ``transcript_viewers`` and therefore not in the room's ceiling. Widening that ceiling to the
calendar attendee list (``GET /meetings/{platform}/{native}/participants``) needs an email→subject
resolver, and agent-api has none on any edge it can reach — see the note in ``api._resolve_room``.

WHAT A ROOM MOUNT IS. One entry per other-attendee workspace: ``role: "room"``, ``write: False`` (the
runtime binds ``:ro`` off that flag — ``runtime_kernel.mounts.workspace_binds``), ``primary: False``,
slug namespaced ``room:<subject>`` so it can never be mistaken for — or collide with — one of the
requester's own. Never their ``_system`` (which lives at ``<root>/.system/<subject>`` and is
unreachable from ``active_workspaces``, whose ``_safe_subject_dir`` refuses a dot-name), never their
SHARED memberships (``active_workspaces`` returns only the subject's own ``private`` mounts;
``shared_active_mounts`` is deliberately not called), and never a directory that does not already
exist — a room read NEVER creates another person's workspace.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Optional

from control_plane.workspace_attach import active_workspaces
from control_plane.workspace_purpose import read_purpose

logger = logging.getLogger("agent_api.meeting_room")

# The mount role the worker + the runtime see. Distinct from 'private'/'shared'/'global'/'system' so
# every consumer can tell "somebody else's desk, read-only" from "a workspace of mine".
ROOM_ROLE = "room"

# Slug namespace for a room mount, so a room entry can never shadow or be confused with one of the
# requester's own slugs in the harness preamble or the terminal.
ROOM_SLUG_PREFIX = "room:"

# How many other people's desks one turn mounts by default (the flow's ``room_read_max``). Founder
# bound: "need to make sure agent will not die if it has 200 folders in it" — the transcript already
# carries what everyone said, so the mounts are for the few whose own notes add something.
DEFAULT_ROOM_READ_MAX = 12

# The server's own ceiling on that cap. A caller may lower ``room_read_max``; it can never raise it
# past this, so a runaway roster can never become a container with hundreds of binds.
MAX_ROOM_READ = 25


class RoomRefused(Exception):
    """The caller may not open a room for this meeting. ``reason`` is safe to return to the caller —
    it names the failure class and never echoes a subject, a roster, or a workspace path."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _clean_subjects(values: Optional[Iterable], *, exclude: set[str]) -> list[str]:
    """Ids out of an untrusted-shaped list: strings/ints only (never bools), trimmed, de-duplicated,
    order preserved, minus ``exclude``. Shape-cleaning only — it authorises nothing."""
    out: list[str] = []
    seen = set(exclude)
    for v in values or []:
        if isinstance(v, bool) or not isinstance(v, (str, int)):
            continue
        s = str(v).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def verified_subjects(meeting_row: Optional[dict], *, requester: str) -> list[str]:
    """The room's CEILING: every OTHER subject meeting-api says can read this meeting, derived from
    the row it returned **for this requester**. Gates 3 and 4 of the module docstring; gate 2 already
    happened when the caller obtained ``meeting_row`` from the entitlement lookup (``None`` there ⇒
    refused here).

    Raises ``RoomRefused`` when the caller is not the meeting's owner. Returns ``[]`` — not an error —
    when the owner's meeting has no other readers yet: an empty room is a normal post-meeting state,
    and the turn should still run."""
    if not isinstance(meeting_row, dict):
        # The entitlement lookup refused (absent row, another tenant's row, meeting-api unreachable).
        raise RoomRefused("not authorized for this meeting")
    owner = str(meeting_row.get("user_id") or "").strip()
    # POSITIVE evidence of ownership (an absent user_id refuses), plus the row's own shared marker.
    # ``is True`` and not ``truthy``: an absent/unknown marker still has to clear the id check above.
    if not owner or owner != str(requester) or meeting_row.get("shared") is True:
        raise RoomRefused("only the meeting's owner may open its room")
    data = meeting_row.get("data")
    viewers = data.get("transcript_viewers") if isinstance(data, dict) else None
    if not isinstance(viewers, list):
        return []
    # The requester is excluded: their own workspaces are already in the stack, read-write.
    return _clean_subjects(viewers, exclude={str(requester)})


def select_room(verified: list[str], *, proposed: Optional[Iterable] = None,
                cap: Optional[int] = None) -> tuple[list[str], list[str]]:
    """Narrow the verified ceiling to the subjects this turn actually mounts.

    ``proposed`` is the CALLER's list — flows holds the transcript, so it is the only party that knows
    who SPOKE and in what order of speaking time. It is used for INTERSECTION AND ORDER ONLY: a
    proposed subject that is not in ``verified`` is dropped, so the proposal can only ever narrow the
    room. ``None`` ⇒ the whole verified ceiling, in meeting-api's own order.

    ``cap`` is the flow's ``room_read_max`` (default :data:`DEFAULT_ROOM_READ_MAX`), clamped to
    :data:`MAX_ROOM_READ` — a caller may lower it, never raise it past the server's ceiling.

    Returns ``(selected, rejected)``; ``rejected`` is the proposed-but-unverified ids, returned so the
    caller can LOG them. A rejection is a finding — a caller naming subjects that were not in the
    meeting is either a bug or an attempt — and it must never be silent."""
    limit = DEFAULT_ROOM_READ_MAX if cap is None else int(cap)
    limit = max(0, min(limit, MAX_ROOM_READ))
    allowed = set(verified)
    if proposed is None:
        return verified[:limit], []
    asked = _clean_subjects(proposed, exclude=set())
    selected = [s for s in asked if s in allowed]
    rejected = [s for s in asked if s not in allowed]
    return selected[:limit], rejected


def room_mounts(root: str, subjects: Iterable[str], *, meeting_id: str,
                taken_paths: Optional[set[str]] = None) -> list[dict]:
    """The READ-ONLY mount entries for ``subjects`` — one per other-attendee workspace that already
    exists on disk. ``taken_paths`` is the set of container paths the requester's OWN stack already
    holds; a room mount never shadows or duplicates one of them (and the set is extended in place, so
    two attendees sharing a path yield one entry).

    Fails SOFT per subject: a store hiccup on one attendee drops THAT attendee from the room and logs
    it — a post-meeting turn with a partial room is better than no turn, and the log says which."""
    taken = taken_paths if taken_paths is not None else set()
    mounts: list[dict] = []
    for subject in subjects:
        try:
            actives = active_workspaces(root, subject)
        except Exception:  # noqa: BLE001 — one attendee's store must never break the dispatch
            logger.warning("room: cannot resolve workspaces for subject=%s (meeting=%s) — omitted "
                           "from the room", subject, meeting_id, exc_info=True)
            continue
        for m in actives or []:
            # active_workspaces returns ONLY the subject's own workspaces (role 'private'); the guard
            # is belt-and-braces so a future role added there cannot silently widen the room.
            if m.role != "private" or not m.path:
                continue
            if m.path in taken or not Path(m.path).is_dir():
                # not-a-dir covers the never-seeded attendee: a room read NEVER creates a workspace.
                continue
            taken.add(m.path)
            slug = f"{ROOM_SLUG_PREFIX}{subject}" if m.primary else f"{ROOM_SLUG_PREFIX}{subject}/{m.slug}"
            mounts.append({
                "slug": slug,
                "path": m.path,
                "role": ROOM_ROLE,
                "write": False,          # the runtime binds this :ro — a room mount is NEVER writable
                "primary": False,
                "purpose": read_purpose(m.path),
                "room": {"meeting_id": str(meeting_id), "subject": str(subject)},
            })
    return mounts
