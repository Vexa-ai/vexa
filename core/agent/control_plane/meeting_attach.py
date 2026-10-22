"""meeting_attach.py — the one chat a live call wakes: the meeting's ATTACHMENT.

Every live meeting row has at most one attachment: the person whose chat listens to the call and
that chat. A meeting signal's turn runs there and nowhere else, so what the call says to Vexa lands
in a chat a person reads — never in the meeting's hidden ``meet-<row>`` session.

    agent:meeting-attach:<row>            hash  subject, session, since, by, how, speak, post_chat
    agent:meeting-attached:<subject>:<session>   the row that chat listens to (the reverse pointer)

``how`` says where it came from: ``sent`` (the chat that sent the bot, or the chat holding the
call), ``auto`` (the owner's most recently active private chat, for a bot nobody sent from a chat —
the calendar's), ``chose`` (a person pressed «Слушать здесь»). The last writer wins.

``speak`` and ``post_chat`` are the call's opt-ins, set by the meeting's owner only and off by
default: may a signal turn answer out loud in the meeting, may it post in the meeting's chat. A
signal turn is told them (`optins_line`). They belong to the call, so moving the attachment keeps
them.

Attaching also writes the person's meeting→chat binding (`_Sessions.bind_meeting_chat`), so what
addresses the meeting by its own name afterwards — the after-call note — lands in the last attached
chat.

Backed by the session index's redis client, with an in-memory fallback so the unit tests need no
redis.
"""
from __future__ import annotations

import time
from typing import Optional

HOWS = ("sent", "auto", "chose")
OPT_INS = ("speak", "post_chat")
#: The meeting statuses during which a call is live and gets a default attachment.
LIVE_STATUSES = frozenset({"requested", "joining", "awaiting_admission", "active", "needs_help"})
PRIVATE_PREFIX = "pchat-"


def is_live(meeting_row) -> bool:
    return isinstance(meeting_row, dict) and str(meeting_row.get("status") or "") in LIVE_STATUSES


class MeetingAttachments:
    def __init__(self, redis_client=None, sessions=None) -> None:
        self._redis = redis_client
        self._sessions = sessions
        self._mem: dict[str, dict] = {}
        self._back: dict[tuple[str, str], str] = {}

    @staticmethod
    def _key(row: str) -> str:
        return f"agent:meeting-attach:{row}"

    @staticmethod
    def _back_key(subject: str, session: str) -> str:
        return f"agent:meeting-attached:{subject}:{session}"

    @staticmethod
    def _view(raw: dict) -> "dict | None":
        if not raw or not raw.get("subject") or not raw.get("session"):
            return None
        try:
            since = float(raw.get("since") or 0)
        except ValueError:
            since = 0.0
        return {"subject": str(raw["subject"]), "session": str(raw["session"]), "since": since,
                "by": str(raw.get("by") or raw["subject"]),
                "how": str(raw.get("how") or "auto") if raw.get("how") in HOWS else "auto",
                "speak": str(raw.get("speak") or "0") == "1",
                "post_chat": str(raw.get("post_chat") or "0") == "1"}

    def get(self, row: str) -> "dict | None":
        """The attachment of meeting ``row``, or None when no chat listens to it yet."""
        row = str(row or "").strip()
        if not row:
            return None
        if self._redis is not None:
            return self._view(dict(self._redis.hgetall(self._key(row)) or {}))
        return self._view(dict(self._mem.get(row) or {}))

    def attach(self, row: str, subject: str, session: str, *, by: str = "", how: str = "auto") -> dict:
        """Move meeting ``row``'s attachment to ``subject``'s chat ``session``; the opt-ins stay."""
        row, subject, session = str(row).strip(), str(subject).strip(), str(session).strip()
        if not row or not subject or not session:
            raise ValueError("an attachment names a meeting row, a subject and a session")
        prior = self.get(row)
        fields = {"subject": subject, "session": session, "since": str(time.time()),
                  "by": str(by or subject), "how": how if how in HOWS else "auto",
                  "speak": "1" if prior and prior["speak"] else "0",
                  "post_chat": "1" if prior and prior["post_chat"] else "0"}
        if self._redis is not None:
            if prior is not None and (prior["subject"], prior["session"]) != (subject, session):
                back = self._back_key(prior["subject"], prior["session"])
                if str(self._redis.get(back) or "") == row:
                    self._redis.delete(back)
            self._redis.hset(self._key(row), mapping=fields)
            self._redis.set(self._back_key(subject, session), row)
        else:
            if prior is not None:
                if self._back.get((prior["subject"], prior["session"])) == row:
                    self._back.pop((prior["subject"], prior["session"]), None)
            self._mem[row] = fields
            self._back[(subject, session)] = row
        if self._sessions is not None:
            self._sessions.bind_meeting_chat(subject, row, session)
        return self._view(fields)

    def set_optins(self, row: str, *, speak: Optional[bool] = None,
                   post_chat: Optional[bool] = None) -> "dict | None":
        """Set the call's opt-ins; None leaves one as it is. None when the meeting has no attachment."""
        current = self.get(row)
        if current is None:
            return None
        fields = {}
        if speak is not None:
            fields["speak"] = "1" if speak else "0"
        if post_chat is not None:
            fields["post_chat"] = "1" if post_chat else "0"
        if fields:
            if self._redis is not None:
                self._redis.hset(self._key(str(row)), mapping=fields)
            else:
                self._mem[str(row)].update(fields)
        return self.get(row)

    def attached_row(self, subject: str, session: str) -> str:
        """The meeting row this chat listens to, or ""."""
        if self._redis is not None:
            try:
                row = str(self._redis.get(self._back_key(subject, session)) or "")
            except Exception:  # noqa: BLE001 — no answer: the chat listens to nothing
                return ""
        else:
            row = self._back.get((str(subject), str(session)), "")
        att = self.get(row) if row else None
        return row if att and (att["subject"], att["session"]) == (str(subject), str(session)) else ""

    def resolve_default(self, owner: str, row: str, *, held: str = "") -> "tuple[str, str] | None":
        """``(session, how)`` the owner's live call attaches to when nobody chose one: the chat that
        sent the bot, else the chat holding the call (``held``), else the owner's most recently
        active private chat. None when the owner has no chat at all (the caller keeps
        ``meet-<row>``)."""
        sess = self._sessions
        if sess is None:
            return None
        try:
            sent = sess.meeting_chat(owner, str(row))
        except Exception:  # noqa: BLE001 — no binding read: look further
            sent = ""
        if sent:
            return sent, "sent"
        if held:
            return held, "sent"
        try:
            rows = sess.list(owner)
        except Exception:  # noqa: BLE001 — no rail: no default
            return None
        for r in rows:
            sid = str(r.get("session") or "")
            if sid.startswith(PRIVATE_PREFIX) and r.get("touched", True) and not r.get("routine"):
                return sid, "auto"
        return None


def optins_line(att: "dict | None") -> str:
    """What the call's owner allows this turn to do in the meeting itself, said to the turn."""
    speak = bool(att and att.get("speak"))
    post = bool(att and att.get("post_chat"))
    return ("THIS CALL'S OPT-INS (set by the meeting's owner): "
            + ("you may answer out loud in the meeting with `speak_in_meeting`, only when someone in "
               "the call asked you something; " if speak else
               "you may NOT speak in the meeting; ")
            + ("you may post in the meeting's chat. " if post else
               "you may NOT post in the meeting's chat. ")
            + "Answer here, in this chat.")
