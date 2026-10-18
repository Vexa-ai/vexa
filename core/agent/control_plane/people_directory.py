"""people_directory.py — who a person may @-mention, and so add to a chat (ADR-0048).

A person sees, and may add, exactly the people they already share a workspace with: every member of
every group workspace they are a member of themselves, as that workspace's authoritative
``policy/members.json`` lists them. Nobody else is ever named — not the organisation's whole user
list, not an address typed by hand. ``_global`` and ``_system`` are not group workspaces (the
membership enumeration skips reserved slugs), so being in the same organisation reveals nobody.

The same answer is the gate on the add: a person added to a chat by id must be in the adder's set.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

from control_plane import workspace_membership as membership_mod

#: The agents a chat may address by name. ``vexa`` is the deployment's own agent.
AGENTS = ("vexa",)


def _label(record: dict) -> str:
    name = str(record.get("name") or "").strip()
    if name:
        return name
    email = str(record.get("email") or "").strip()
    return email.split("@", 1)[0] if email else ""


def mentionable(root: Path, subject: str, index_list: Callable[[str], Iterable]) -> list[dict]:
    """People ``subject`` shares a group workspace with, one row each: ``subject`` (the stable id),
    ``name``, ``email`` and the shared ``workspaces``. ``subject`` themself is never in it."""
    root = Path(root)
    rows, _ = membership_mod.reconciled_memberships(root, subject, lambda s: list(index_list(s) or []))
    people: dict[str, dict] = {}
    for row in rows:
        ws = str((row or {}).get("workspace_id") or "")
        if not ws:
            continue
        try:
            # authoritative, per workspace: the index proposes, members.json decides
            if membership_mod.is_member(root, ws, subject) is None:
                continue
            members = membership_mod.read_members(root, ws)
        except Exception:  # noqa: BLE001 — an unreadable roster names nobody
            continue
        for m in members:
            who = str(m.get("subject") or "")
            if not who or who == subject:
                continue
            p = people.setdefault(who, {"subject": who, "name": "", "email": "", "workspaces": []})
            p["name"] = p["name"] or _label(m)
            p["email"] = p["email"] or str(m.get("email") or "")
            if ws not in p["workspaces"]:
                p["workspaces"].append(ws)
            # their role in each workspace you share (the People list shows it per workspace)
            p.setdefault("roles", {})[ws] = str(m.get("role") or "member")
            if m.get("discoverable") is False:     # never suggested to others (people discovery)
                p["discoverable"] = False
    return sorted(people.values(), key=lambda p: (p["name"].lower(), p["subject"]))


def sees_addresses(root: Path, subject: str, workspaces: Iterable[str]) -> bool:
    """May ``subject`` see a co-member's ADDRESS (security review M1)? Only as an owner or contributor
    of one of the ``workspaces`` they share; a reader sees names. Unreadable means no."""
    for ws in workspaces or ():
        try:
            if membership_mod.normalize_role(membership_mod.is_member(Path(root), str(ws), subject) or "viewer") \
                    in ("owner", "contributor"):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def may_mention(root: Path, subject: str, other: str, index_list: Callable[[str], Iterable]) -> bool:
    """Does ``subject`` share a group workspace with ``other``?"""
    return any(p["subject"] == other for p in mentionable(root, subject, index_list))


def mention_block(people: list[dict], mentioned: Iterable[str], *, in_chat: Iterable[str] = ()) -> str:
    """What the agent is told about the people a message @-mentions: each one's account id, so a
    share it is asked to make names exactly that account. Ids the speaker shares no workspace with
    are dropped (the composer never offers them; a hand-made request gains nothing)."""
    by_id = {p["subject"]: p for p in people}
    here = set(in_chat)
    rows = []
    for who in dict.fromkeys(str(m) for m in (mentioned or [])):
        p = by_id.get(who)
        if p is None:
            continue
        label = p.get("name") or p.get("email") or who
        where = "already in this chat" if who in here else "not in this chat"
        email = f" ({p['email']})" if p.get("email") else ""
        rows.append(f"- @{label}{email}: account id `{who}`; {where}.")
    if not rows:
        return ""
    return ("## People this message mentions\n\n" + "\n".join(rows) + "\n"
            "If the message asks you to share this chat or a workspace with them, do it with "
            "`chat_participant_add` (subject = the account id; skip it for anyone already in this chat) "
            "or `workspace_member_add` (person = the account id), then say in one line what you did. "
            "\"Share this chat\" shares it with its history: before the add, say in one line what in "
            "this chat's history is sensitive (figures, mail, internal documents) and proceed on the "
            "owner's yes; `history: none` starts a new chat with them instead, `from:<turn>` one with "
            "what was said from that turn on. "
            "Grant a workspace only when the message asks for it. Never look a person up by name.\n\n")


def name_of(root: Path, subject: str, index_list: Callable[[str], Iterable], email: str = "") -> str:
    """How ``subject`` is named to the people they share a workspace with — their own roster entry's
    name in any group workspace they are in — else their address's local part, else "". Used for the
    actor of a notification, so it names a person the way the @-mention picker does."""
    root = Path(root)
    try:
        rows, _ = membership_mod.reconciled_memberships(root, subject, lambda s: list(index_list(s) or []))
    except Exception:  # noqa: BLE001 — an unreadable index names nobody
        rows = []
    for row in rows:
        ws = str((row or {}).get("workspace_id") or "")
        if not ws:
            continue
        try:
            members = membership_mod.read_members(root, ws)
        except Exception:  # noqa: BLE001
            continue
        for m in members:
            if str(m.get("subject") or "") == str(subject):
                name = str(m.get("name") or "").strip()
                if name:
                    return name
                email = email or str(m.get("email") or "")
    email = str(email or "").strip()
    return email.split("@", 1)[0] if email else ""
