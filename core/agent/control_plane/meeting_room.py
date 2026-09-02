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
     flows/operator capability, not an end-user one. **Under the participant model this secret IS
     the trust boundary on WHO is in the room** — see the residual note below.
  2. **Entitlement** — reuses the EXISTING meeting access check (``api._meeting_owner_lookup`` →
     meeting-api ``GET /meetings/{id}``, which evaluates its own access union in SQL and 404s a row
     the caller may not read). No second authorisation rule is invented here.
  3. **Ownership** (``assert_owner``) — the row must be the caller's OWN meeting (``user_id ==
     requester`` and not ``shared``). A transcript-share RECIPIENT passes gate 2 but is refused here.
  4. **Membership** — the INVITE's participant list (ADDRESSES), sent by the trusted caller, each
     resolved to a subject through admin-api. Only a participant who ALREADY HAS a subject AND an
     existing desk is mountable; anyone else is skipped, with the reason logged. A desk is NEVER
     created from this path — ``drop_to_attendees`` creates it afterwards.

SPEAKING DOES NOT DECIDE MEMBERSHIP — IT ONLY DECIDES ORDER. This is the safety property of the
participant model and it is worth stating flatly: a speaker LABEL is a display name off a platform
tile, and matching a display name to a human is a guess; the wrong guess mounts the wrong person's
notes. So a name never ADMITS anybody. ``order_participants`` matches speaker labels to addresses
that were **already in the invite**, via the ICS ``CN=`` map the caller sends, and uses the match only
to sort: matched-and-spoke first (in the speaking order the caller supplies, descending by speaking
time), then everyone else in invite order, then the cap. A bad CN match costs ORDERING and nothing
else, and a total match failure degrades to the first N in invite order — never to an empty room.

THE RESIDUAL, STATED PLAINLY. Membership now comes from the CALLER's participant list rather than
from a roster the server holds, so **a trusted internal caller could name addresses that were not in
the meeting** and read those people's desks. The ``X-Internal-Secret`` gate IS the trust boundary on
that, and it is a deliberate trade, not an oversight: the alternative — the server's own
``data.transcript_viewers`` roster — is empty at post-meeting time (nobody has clicked their share
link yet), which made the whole feature inert on its normal path. Two properties survive the trade
and are what keep it bounded: an unprivileged caller cannot open a room AT ALL, and no path here
turns a NAME into a person.

DECISION 22 — WHAT THE RUN MAY WRITE. The room run reads desks and writes ONE shared artefact; the
artefact's canonical home is the MEETING ROW / its transcript store, and flows distributes it into
every attendee's desk afterwards (``drop_to_attendees``) — organizer included, nobody special. So the
run does **not** write into any desk, THE DISPATCH SUBJECT'S OWN INCLUDED. In room mode the subject's
own desks are therefore demoted to READ-ONLY in the stack (``dispatch.build_mount_set``), which the
runtime turns into a ``:ro`` bind. The one exception is the GROUP DESK: when the meeting is bound to
a shared workspace (``data.workspace_id``, set by meeting-api's ``POST .../workspace``), that desk is
mounted READ-WRITE and the run actively maintains it — the group's people, decisions, open items,
README. Pure addition: a meeting with no group simply has no writable desk in the stack.

``group_desk_mount`` does not invent a membership rule for that. It hands the bound id to the
EXISTING Lane-A seam (``workspace_attach.shared_active_mounts``), which re-reads the subject's role
authoritatively from the workspace's own ``policy/members.json`` and grants write only to a
contributor/owner. A non-member, a viewer, an unmaterialized workspace, a reserved/dot slug or one the
subject switched OFF all resolve to no mount — never to a widening.

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

from control_plane.workspace_attach import active_workspaces, shared_active_mounts
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


# Why each participant is where it is in the room — stamped on every audit row so anyone can later
# ask "which desks could that run read, and why those?" and get an answer instead of a guess.
WHY_SPOKE = "matched-and-spoke"
WHY_INVITE = "unmatched-invite-order"
WHY_NO_SUBJECT = "skipped-no-subject"
WHY_NO_DESK = "skipped-no-desk"
WHY_OVER_CAP = "skipped-over-cap"


def _clean_addresses(values: Optional[Iterable]) -> list[str]:
    """Addresses out of an untrusted-shaped list: strings only, trimmed + case-folded, de-duplicated,
    ORDER PRESERVED (invite order is load-bearing). Shape-cleaning only — it authorises nothing."""
    out: list[str] = []
    seen: set[str] = set()
    for v in values or []:
        if isinstance(v, bool) or not isinstance(v, str):
            continue
        a = v.strip().lower()
        if not a or a in seen:
            continue
        seen.add(a)
        out.append(a)
    return out


def _norm_name(name: object) -> str:
    """A display name reduced for comparison: case-folded, whitespace collapsed. Deliberately crude —
    it only ever REORDERS addresses that are already in the room, so a miss costs position, never
    admission, and a cleverer matcher would buy nothing that matters."""
    if not isinstance(name, str):
        return ""
    return " ".join(name.split()).casefold()


def assert_owner(meeting_row: Optional[dict], *, requester: str) -> None:
    """Gate 3: the meeting must be the requester's OWN. Raises ``RoomRefused`` otherwise.

    Gate 2 already happened when the caller obtained ``meeting_row`` from the entitlement lookup
    (``None`` there ⇒ refused here). Ownership needs POSITIVE evidence — an absent ``user_id``
    refuses rather than defaulting to "probably theirs"."""
    if not isinstance(meeting_row, dict):
        # The entitlement lookup refused (absent row, another tenant's row, meeting-api unreachable).
        raise RoomRefused("not authorized for this meeting")
    owner = str(meeting_row.get("user_id") or "").strip()
    # ``is True`` and not ``truthy``: an absent/unknown marker still has to clear the id check above.
    if not owner or owner != str(requester) or meeting_row.get("shared") is True:
        raise RoomRefused("only the meeting's owner may open its room")


def order_participants(participants: Optional[Iterable], *,
                       names: Optional[dict] = None,
                       speakers: Optional[Iterable] = None) -> list[tuple[str, str]]:
    """Order the invite's participant ADDRESSES for reading: who spoke, first.

    ``names`` is the ICS ``CN=`` map (address → display name) the caller parsed off the invite;
    ``speakers`` is the transcript's speaker labels ALREADY ordered by speaking time descending (the
    caller holds the transcript, so it does that arithmetic). A speaker label is matched to an
    address through ``names`` and used ONLY to sort — it can never add an address that was not in
    ``participants``, which is the property that makes a bad name match cheap.

    Returns ``[(address, why)]``: matched-and-spoke first in speaking order, then everyone else in
    invite order. No match anywhere ⇒ the whole invite list, in invite order — never empty."""
    ordered_addrs = _clean_addresses(participants)
    if not ordered_addrs:
        return []
    by_name: dict[str, str] = {}
    for addr, cn in (names or {}).items():
        key = _norm_name(cn)
        a = addr.strip().lower() if isinstance(addr, str) else ""
        # First writer wins: two invitees sharing a display name is exactly the ambiguity we refuse
        # to resolve, so neither steals the other's slot — they both fall to invite order.
        if key and a in ordered_addrs and key not in by_name:
            by_name[key] = a
    out: list[tuple[str, str]] = []
    taken: set[str] = set()
    for label in speakers or []:
        addr = by_name.get(_norm_name(label))
        if addr and addr not in taken:
            taken.add(addr)
            out.append((addr, WHY_SPOKE))
    out.extend((a, WHY_INVITE) for a in ordered_addrs if a not in taken)
    return out


def resolve_desks(root: str, ordered: list, *, lookup, meeting_id: str,
                  cap: Optional[int] = None,
                  taken_paths: Optional[set] = None) -> tuple[list[dict], list[dict]]:
    """Turn ordered participant ADDRESSES into read-only desk mounts, and return the audit beside them.

    ``lookup`` is ``(address) -> subject | None`` — the admin-api email→subject resolver (injected, so
    this stays offline-testable and so a resolver that is not configured degrades to an empty room
    rather than to a guess). A participant with no subject, or with a subject whose desk does not
    exist on disk, is SKIPPED: their desk is created later by ``drop_to_attendees``, never here.

    The cap is applied to DESKS ACTUALLY MOUNTED, not to the pre-resolution list — the bound exists so
    the agent does not drown in folders ("will not die if it has 200 folders"), and capping before
    resolution would silently under-fill the room whenever early participants happen to have no
    account. Everything past the cap is still audited, as ``skipped-over-cap``.

    Returns ``(mounts, audit)`` where each audit row is ``{address, subject, why}``. Nothing here
    logs; the caller owns the log line, because the caller knows the meeting it belongs to."""
    limit = DEFAULT_ROOM_READ_MAX if cap is None else int(cap)
    limit = max(0, min(limit, MAX_ROOM_READ))
    taken = taken_paths if taken_paths is not None else set()
    mounts: list[dict] = []
    audit: list[dict] = []
    for address, why in ordered:
        if len(mounts) >= limit:
            audit.append({"address": address, "subject": None, "why": WHY_OVER_CAP})
            continue
        try:
            subject = lookup(address)
        except Exception:  # noqa: BLE001 — a resolver hiccup drops ONE person, never the turn
            logger.warning("room: email→subject lookup failed for a participant of meeting=%s — "
                           "skipped", meeting_id, exc_info=True)
            subject = None
        subject = str(subject).strip() if subject not in (None, "") else ""
        if not subject:
            audit.append({"address": address, "subject": None, "why": WHY_NO_SUBJECT})
            continue
        desks = room_mounts(root, [subject], meeting_id=meeting_id, taken_paths=taken)
        if not desks:
            audit.append({"address": address, "subject": subject, "why": WHY_NO_DESK})
            continue
        for d in desks:
            d["room"]["address"] = address
            d["room"]["why"] = why
        mounts.extend(desks)
        audit.append({"address": address, "subject": subject, "why": why})
    return mounts, audit


def group_workspace_id(meeting_row: Optional[dict]) -> str:
    """The shared workspace this meeting is BOUND to (``data.workspace_id``), or ``""``.

    Server-derived like everything else here: meeting-api owns the binding (``POST
    /meetings/{platform}/{native}/workspace``, owner-scoped), so the caller cannot name a group.
    Returning the id is not a grant — ``group_desk_mount`` still asks the authoritative member list
    whether THIS subject may write it."""
    data = meeting_row.get("data") if isinstance(meeting_row, dict) else None
    ws = data.get("workspace_id") if isinstance(data, dict) else None
    return str(ws).strip() if isinstance(ws, (str, int)) and not isinstance(ws, bool) else ""


def group_desk_mount(root: str, subject: str, workspace_id: str) -> Optional[dict]:
    """The meeting's GROUP DESK as a READ-WRITE mount for ``subject``, or ``None``.

    Deliberately a thin call into ``shared_active_mounts`` rather than a second membership rule: that
    function re-reads the role from the workspace's own ``policy/members.json`` (never the index
    copy), refuses a traversal, refuses a reserved/dot slug, refuses an unmaterialized workspace, and
    grants write only to contributor/owner. A viewer therefore gets a READ-ONLY group mount, which is
    the correct answer rather than a refusal — they may still read the group's memory.

    Fails SOFT: any error resolving it means no group desk and a logged line, never a dead dispatch."""
    if not workspace_id:
        return None
    try:
        found = shared_active_mounts(root, subject, [{"workspace_id": workspace_id}])
    except Exception:  # noqa: BLE001 — a group-desk hiccup must never break the post-meeting turn
        logger.warning("room: cannot resolve group desk %s for subject=%s — running without it",
                       workspace_id, subject, exc_info=True)
        return None
    m = next((x for x in found if x.slug == workspace_id), None)
    if m is None:
        # Not a member, not materialized here, or the subject switched this shared workspace OFF in
        # their own switcher. Said out loud: a post-meeting run that was supposed to maintain the
        # group's memory and quietly maintained nothing is the failure this line exists to surface.
        logger.warning("room: meeting is bound to group desk %s but subject=%s has no writable mount "
                       "for it (not a member / not materialized / switched off)", workspace_id, subject)
        return None
    return {"slug": m.slug, "path": m.path, "role": m.role, "write": m.write, "primary": m.write,
            "purpose": read_purpose(m.path), "group": {"workspace_id": workspace_id}}


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
