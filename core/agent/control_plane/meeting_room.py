"""meeting_room.py — the post-meeting ROOM: what the one shared write-up may read and write.

DESKS ARE PERSONAL (founder decision 3, 2026-10-17). The post-meeting run writes ONE shared write-up
for everybody who was in the meeting. It reads three things and nothing else:

  * **the transcript** — what was said, which every attendee was present for;
  * **the room's workspace** — the meeting's GROUP DESK, when the meeting is bound to a shared
    workspace (``data.workspace_id``), mounted read-write for a contributor or owner;
  * **explicit shares** — what someone chose to put in front of the meeting (a page in that
    workspace, a transcript share), through the normal sharing paths, never inferred.

It does **not** read another attendee's desk. Until this decision it did: up to twelve attendees'
whole personal desks, chosen by the calendar invite's participant list, mounted read-only into the
run — so being invited to a meeting exposed a person's notes (and, before D6, their held private
writes) to someone else's agent (review BL-2, deny test 9). ``room_mounts`` and ``resolve_desks``
are gone, and ``dispatch.build_mount_set`` adds no ``role: "room"`` entry.

THE GATES that still apply before a room exists, each fail-CLOSED:

  1. **Caller tier** (``api._resolve_room``) — the internal-tier secret; the room is a
     flows/operator capability, not an end-user one.
  2. **Entitlement** — the existing meeting access check (``api._meeting_owner_lookup`` →
     meeting-api ``GET /meetings/{id}``, which 404s a row the caller may not read).
  3. **Ownership** (``assert_owner``) — the row must be the caller's OWN meeting; a transcript-share
     recipient passes gate 2 and is refused here.

DECISION 22 — WHAT THE RUN MAY WRITE. The artefact's canonical home is the MEETING ROW; flows
distributes it into every attendee's desk afterwards (``drop_to_attendees``). So the run writes no
desk, the dispatch subject's own included: in room mode the subject's own desks are demoted to
read-only (``dispatch.build_mount_set``). The one writable workspace is the GROUP DESK, resolved by
``group_desk_mount`` through the existing Lane-A seam (``workspace_attach.shared_active_mounts``),
which re-reads the subject's role from the workspace's own member list and grants write only to a
contributor or owner. A non-member, an unmaterialized workspace, a reserved slug or one the subject
switched off all resolve to no mount — never to a widening.
"""
from __future__ import annotations

import logging
from typing import Optional

from control_plane.workspace_attach import shared_active_mounts
from control_plane.workspace_purpose import read_purpose

logger = logging.getLogger("agent_api.meeting_room")

class RoomRefused(Exception):
    """The caller may not open a room for this meeting. ``reason`` is safe to return to the caller —
    it names the failure class and never echoes a subject, a roster, or a workspace path."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


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
