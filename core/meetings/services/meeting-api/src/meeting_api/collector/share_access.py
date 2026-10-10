"""share_access.py — who a meeting is shared with, and the owner's three edits to that list.

A meeting is shared through the transcript-share grants this module's callers already mint
(``data.share_grants[]``, hash-at-rest) and redeem (``data.transcript_viewers[]``, the user ids the
access union reads). What was missing was the OWNER's side of that capability: seeing who holds
it, and taking it back. These are pure functions over one meeting row's ``data`` dict, so the
SQLAlchemy store and the in-memory fake run the SAME rule inside their own row lock — two copies of
an access rule are two places to get it wrong.

The carriers, all written only by meeting-api (P23), all on ``meeting.data``:

* ``share_grants[]``      — each grant gains ``created_at`` (when it was minted). Unchanged otherwise.
* ``transcript_viewers[]`` — unchanged: the user ids the access union admits. Removing a person
  removes their id here, which is what every read path (REST, the agent-api SSE, the gateway
  ``/ws`` re-authorization) consults.
* ``share_viewers[]``     — the roster behind ``transcript_viewers``: ``{user_id, email, grant_id,
  since}`` per redeemer, so the owner sees a person rather than an integer. Owner-only on every
  response edge (``projection.OWNER_ONLY_KEYS``).
* ``share_removed[]``     — ``{user_id, at}``: a person the owner removed cannot re-redeem a grant
  minted BEFORE the removal (the link they still hold). A NEW invite, minted after, admits them.
  Owner-only.
* ``share_settings``      — ``{"recording": bool}``: whether people the meeting is shared with may
  play and download its recording. Absent means NO (default-deny, P20).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _expired(grant: dict) -> bool:
    exp = _parse(grant.get("expires_at"))
    return bool(exp and exp < datetime.now(timezone.utc))


def redeem_refusal(data: dict, user_id: int, grant: dict) -> Optional[str]:
    """``"revoked"`` when the owner removed this person after this grant was minted, else None.

    A grant with no ``created_at`` predates this module and therefore predates any removal."""
    for r in data.get("share_removed") or []:
        if r.get("user_id") != user_id:
            continue
        removed_at = _parse(r.get("at"))
        minted_at = _parse(grant.get("created_at"))
        if removed_at is None or minted_at is None or minted_at <= removed_at:
            return "revoked"
    return None


def record_redeem(data: dict, user_id: int, email: Optional[str], grant: dict) -> None:
    """Admit ``user_id`` (``transcript_viewers``) and record who they are (``share_viewers``)."""
    viewers = list(data.get("transcript_viewers") or [])
    if user_id not in viewers:
        viewers.append(user_id)
    data["transcript_viewers"] = viewers
    roster = [v for v in (data.get("share_viewers") or []) if v.get("user_id") != user_id]
    roster.append({"user_id": user_id, "email": (email or "").lower() or None,
                   "grant_id": grant.get("id"), "since": _now_iso()})
    data["share_viewers"] = roster
    # A fresh, valid redeem after a removal means the owner re-invited them: the removal is spent.
    data["share_removed"] = [r for r in (data.get("share_removed") or []) if r.get("user_id") != user_id]


def access_view(data: dict, *, meeting_id: int, owner_id: int) -> dict:
    """The owner's view of who can read this meeting. Never carries a secret or a hash."""
    roster = {v.get("user_id"): v for v in (data.get("share_viewers") or [])}
    people = []
    for uid in data.get("transcript_viewers") or []:
        if uid == owner_id:
            continue
        v = roster.get(uid) or {}
        people.append({"user_id": uid, "email": v.get("email"), "role": "viewer",
                       "grant_id": v.get("grant_id"), "since": v.get("since")})
    joined_emails = {p["email"] for p in people if p.get("email")}
    invites, links = [], []
    for g in data.get("share_grants") or []:
        if g.get("revoked") or _expired(g):
            continue
        public = {"id": g.get("id"), "mode": g.get("mode"), "created_at": g.get("created_at"),
                  "expires_at": g.get("expires_at")}
        if g.get("mode") == "restricted":
            pending = [e for e in (g.get("allowed_emails") or []) if str(e).lower() not in joined_emails]
            if pending:
                invites.append({**public, "emails": pending})
        else:
            links.append({**public, "joined": sum(1 for p in people if p.get("grant_id") == g.get("id"))})
    settings = data.get("share_settings") if isinstance(data.get("share_settings"), dict) else {}
    return {
        "meeting_id": meeting_id,
        "people": people,
        "invites": invites,
        "links": links,
        "workspace_id": data.get("workspace_id") or None,
        "recording": bool(settings.get("recording")),
    }


def revoke_grant(data: dict, grant_id: str) -> bool:
    """Revoke one grant, and drop every person who got in through it. False if no such grant.

    Turning a link off takes back what it gave: people who joined through that link lose access
    now, not when they next happen to redeem."""
    found = False
    grants = []
    for g in data.get("share_grants") or []:
        if g.get("id") == grant_id:
            g = {**g, "revoked": True}
            found = True
        grants.append(g)
    if not found:
        return False
    data["share_grants"] = grants
    gone = {v.get("user_id") for v in (data.get("share_viewers") or []) if v.get("grant_id") == grant_id}
    if gone:
        data["transcript_viewers"] = [u for u in (data.get("transcript_viewers") or []) if u not in gone]
        data["share_viewers"] = [v for v in (data.get("share_viewers") or []) if v.get("user_id") not in gone]
    return True


def remove_viewer(data: dict, viewer_id: int) -> bool:
    """Remove one person: their id leaves the access union, their pending single-person invites
    are revoked, and grants minted before now no longer admit them. False if they had no access."""
    viewers = list(data.get("transcript_viewers") or [])
    roster = list(data.get("share_viewers") or [])
    if viewer_id not in viewers and not any(v.get("user_id") == viewer_id for v in roster):
        return False
    email = next((v.get("email") for v in roster if v.get("user_id") == viewer_id), None)
    data["transcript_viewers"] = [u for u in viewers if u != viewer_id]
    data["share_viewers"] = [v for v in roster if v.get("user_id") != viewer_id]
    if email:
        grants = []
        for g in data.get("share_grants") or []:
            only_them = [str(e).lower() for e in (g.get("allowed_emails") or [])] == [email]
            if g.get("mode") == "restricted" and only_them:
                g = {**g, "revoked": True}
            grants.append(g)
        data["share_grants"] = grants
    removed = [r for r in (data.get("share_removed") or []) if r.get("user_id") != viewer_id]
    removed.append({"user_id": viewer_id, "at": _now_iso()})
    data["share_removed"] = removed
    return True


def set_settings(data: dict, *, recording: bool) -> dict:
    data["share_settings"] = {**(data.get("share_settings") or {}), "recording": bool(recording)}
    return data["share_settings"]


def recording_shared(data: dict) -> bool:
    """May a NON-owner who can read this meeting play its recording? Default no."""
    s = data.get("share_settings")
    return bool(isinstance(s, dict) and s.get("recording"))
