"""notifications.py — what happened to a person that they did not do themselves (#1783).

Founder, 2026-10-13: *"we need notifications popup in UI when people invite each other etc, and if a
chat session is shared it should appear in their chats as shared"*.

ONE STORE. Rows live in the session store's database (``agent_session_notification``), reached
through the store's own connection, lock and least-privilege role (ADR-0048 decision 2). Every kind
below is performed by an agent-api route, so agent-api is the one writer; the flows timeline is a
different domain's read-only history, with no per-person read state, and is not a fit.

ONE ROW PER RECIPIENT. A notification is keyed by who receives it, and every read and every
mark-read is ``WHERE recipient = ?``: one person can never list, count or mark another's. Isolation
is a key, not a filter applied after the fact.

ONE WRITER PER KIND. The route that performs the act emits it, and nothing else does:

=================== =========================================== ===================================
kind                act (the emitting route)                    recipient
=================== =========================================== ===================================
chat_added          add a person to a shared chat               the person added
chat_shared         share a private chat (it forks, ADR-0048)   the person added
chat_mentioned      a message @-mentions a participant          each mentioned participant
workspace_invited   invite an address that has an account       the invitee
workspace_added     add an account to a workspace               the person added
invite_accepted     redeem an invite                            whoever minted it
workspace_joined_   join a workspace by its link                whoever made the link
via_link
invite_declined     decline an invite                           whoever minted it
routine_posted      a routine's run lands in a shared chat      every other person in the chat
call_started        bind a call to a chat                       every other person in the chat
meeting_shared      flows mails a meeting's share link          the account behind the address
bot_scheduled       flows sets a bot to join a calendar meeting the meeting's owner
bot_joined          flows hears the bot went live in a meeting  the meeting's owner
access_requested    ask for access through a reference (refs)   the object's owner
=================== =========================================== ===================================

The three meeting kinds are facts another domain holds. Flows is their producer and hands each one
to ``POST /internal/notifications`` (internal tier only); agent-api still writes every row, so the
store keeps one writer and one leak rule.

THE LEAK RULE. The text is composed HERE from two names and nothing else — the actor's and the
object's (a chat's title, a workspace's name, a routine's name) — so no caller can put a message, a
page or a transcript line into it: there is no parameter that would carry one. A chat's title is
named only to people who can read the chat (the emitter checks); a workspace's name is what an
invite's own preview shows its holder.

DELIVERY. Each new row is published to the recipient's own channel ``u:{recipient}:notifications``;
the gateway's ``/ws`` fans that channel in for every socket the recipient has open (one per tab), so
every tab sees it at once. On load a client reads the unread rows from the store. A publish that does
not land loses nothing: the row is already stored. ``rail`` frames (a chat joined or left) are
published the same way and are not stored — they only tell the recipient's rail to read again.

FAIL-SOFT. A notification never fails the act it reports: ``emit`` logs and returns ``[]``.
"""
from __future__ import annotations

from control_plane import display_name
import json
import logging
import time
import uuid
from typing import Callable, Iterable, Optional

logger = logging.getLogger("agent_api.notifications")

KINDS = ("chat_added", "chat_shared", "chat_mentioned", "workspace_invited", "workspace_added",
         "invite_accepted", "invite_declined", "workspace_joined_via_link", "routine_posted",
         "call_started", "agent", "meeting_shared", "bot_scheduled", "bot_joined", "access_requested", "budget", "llm_budget",
         "folders_migrated")
#: The kinds another domain may hand in over the internal tier — meeting facts only. A chat or
#: workspace notice is written by the agent-api route that performs the act, never from outside.
MEETING_KINDS = ("meeting_shared", "bot_scheduled", "bot_joined")
OBJECT_KINDS = ("chat", "workspace", "meeting", "record", "invite", "page", "none")
#: The words, by kind: ``{actor}`` and ``{object}`` are the only two holes.
_TEXT = {
    "chat_added": "{actor} added you to the chat “{object}”",
    # the company's token budget (#1783, `budgets`): the object IS the sentence, written by agent-api
    "budget": "{object}",
    "chat_shared": "{actor} shared the chat “{object}” with you",
    "chat_mentioned": "{actor} mentioned you in “{object}”",
    "workspace_invited": "{actor} invited you to the workspace “{object}”",
    "workspace_added": "{actor} added you to the workspace “{object}”",
    "invite_accepted": "{actor} accepted your invite to “{object}”",
    "invite_declined": "{actor} declined your invite to “{object}”",
    "workspace_joined_via_link": "{actor} joined “{object}” via your link",
    "access_requested": "{actor} asks for access to “{object}”",
    "routine_posted": "The routine {actor} posted a run in “{object}”",
    "call_started": "{actor} started a call in “{object}”",
    "meeting_shared": "{actor} shared the meeting “{object}” with you",
    "bot_scheduled": "Vexa will join “{object}” when it starts",
    "bot_joined": "Vexa joined “{object}”",
    # the LLM spend cap (llm_budget.py): to the people `llm_budget_notify` names, at 80% and 100%
    "llm_budget": "AI usage reached {object}",
    # the folders migration (rooms, R1): the object IS the sentence, written by agent-api
    "folders_migrated": "{object}",
}
#: THE AGENT'S OWN NOTICE (`notify`, founder: "agents need tooling to use that"). Its text is the
#: agent's one line — the one kind whose words are not composed here — so it is bounded and posted
#: only to the agent's owner or to people in the same chat, whose turn it is reading for (ADR-0048:
#: a shared chat's turn reads only what every participant can read).
AGENT_TEXT_MAX = 200
AGENT_PER_HOUR = 20
#: A routine's run posts at most one: the second post from the same run unit inside this window is
#: refused. Runs are minutes long and scheduled far apart, so one window is one run.
RUN_WINDOW_SEC = 900
NAME_MAX = 80
#: The most rows one person keeps; older READ rows are dropped past it (unread ones never are).
KEEP = 500
#: A second identical event (same kind, actor and object) while the first is still unread within
#: this window refreshes that row instead of stacking a duplicate — ten mentions are one line.
COALESCE_SEC = 600
LIST_MAX = 200


def channel(recipient: str) -> str:
    return f"u:{recipient}:notifications"


def _name(value: str, fallback: str) -> str:
    n = " ".join(str(value or "").split())
    if not n:
        n = fallback
    return n if len(n) <= NAME_MAX else n[: NAME_MAX - 1].rstrip() + "…"


def text_for(kind: str, actor_name: str, object_name: str) -> str:
    """The notification's sentence — the actor and the object, nothing else."""
    # NEVER AN ID IN A SENTENCE (UI decisions 14): "Vexa joined “call-0c90e2a5fcff”" reads "Vexa
    # joined your call"; a name stays a quoted name
    obj = _name(object_name, "untitled")
    if kind in MEETING_KINDS and display_name.looks_raw(obj):
        return _TEXT[kind].replace("“{object}”", "{object}").format(
            actor=_name(actor_name, "Someone"), object=display_name.readable_object(obj))
    return _TEXT[kind].format(actor=_name(actor_name, "Someone"), object=obj)


def _public(r) -> dict:
    return {"id": r[0], "kind": r[2], "actor": {"id": r[3], "name": r[4]},
            "object": {"kind": r[5], "id": r[6], "name": r[7], **({"url": r[8]} if r[8] else {})},
            "text": r[9], "created": float(r[10]), "read": r[11] is not None,
            "actions": actions_for(r[2], r[12], bool(r[8])), **({"state": r[12]} if r[12] else {})}


#: WHAT A NOTICE LETS ITS RECIPIENT DO, inline in the toast and the list (founder 2026-10-13: an
#: invite he could not accept from its notification). `open` goes to the object; the others are acts.
ACTIONS = {"workspace_invited": ("accept", "decline"), "call_started": ("join", "decline")}


def actions_for(kind: str, acted, has_url: bool) -> list:
    """The acts a row offers now: none of the kind's own once it was acted on (an accepted invite
    is only `open` — "Joined · Open"), and an invite whose link is gone offers nothing to accept."""
    if acted:
        return ["open"] if acted in ("joined", "accepted") else []
    acts = list(ACTIONS.get(kind, ()))
    if kind == "workspace_invited" and not has_url:
        acts = []
    return acts + ["open"]


_COLS = "id, recipient, kind, actor, actor_name, object_kind, object_id, object_name, url, text, created, read_at, acted"


class AgentRefused(Exception):
    """A refused `notify`: ``reason`` is typed, ``status`` the HTTP answer."""

    def __init__(self, reason: str, status: int, message: str) -> None:
        super().__init__(message)
        self.reason, self.status = reason, status


def agent_text(text: str) -> str:
    t = " ".join(str(text or "").split())
    if not t or len(t) > AGENT_TEXT_MAX:
        raise AgentRefused("bad_text", 422, f"a notification is one line of 1-{AGENT_TEXT_MAX} characters")
    return t

Publish = Optional[Callable[[str, str], None]]


class Notifier:
    """The store's notification rows plus the live channel. ``publish(channel, payload)`` is a redis
    PUBLISH in production; absent, rows are stored and read on load only."""

    def __init__(self, store, publish: Publish = None, clock: Callable[[], float] = time.time) -> None:
        self._store, self._publish, self._clock = store, publish, clock

    # ── writing ──────────────────────────────────────────────────────────────────────────────
    def emit(self, recipients: Iterable[str], *, kind: str, actor: str, actor_name: str = "",
             object_kind: str, object_id: str, object_name: str = "", url: Optional[str] = None) -> list[dict]:
        """Store one row per recipient (never the actor themself) and publish it to them. Returns the
        rows written. Never raises: the act this reports has already happened."""
        try:
            if kind not in KINDS or object_kind not in OBJECT_KINDS or not object_id:
                raise ValueError(f"not a notification: {kind}/{object_kind}")
            text = text_for(kind, actor_name, object_name)
            who = [str(r) for r in dict.fromkeys(recipients or []) if r and str(r) != str(actor)]
            out = [self._write(r, kind, str(actor), _name(actor_name, ""), object_kind, str(object_id),
                               _name(object_name, ""), url, text) for r in who]
        except Exception as exc:  # noqa: BLE001 — a notification never fails its act
            logger.warning("notification %s not recorded (%s)", kind, type(exc).__name__)
            return []
        for r, row in zip(who, out):
            self._send(r, {"type": "notification", "notification": row})
        return out

    def _write(self, recipient, kind, actor, actor_name, object_kind, object_id, object_name, url, text) -> dict:
        s, now = self._store, self._clock()
        with s._tx():
            same = s._x(f"SELECT {_COLS} FROM agent_session_notification WHERE recipient = ? AND kind = ? "
                        "AND actor = ? AND object_id = ? AND read_at IS NULL AND created >= ? "
                        "ORDER BY created DESC", (recipient, kind, actor, object_id, now - COALESCE_SEC)).fetchone()
            if same is not None:
                s._x("UPDATE agent_session_notification SET created = ?, text = ?, object_name = ?, actor_name = ? "
                     "WHERE id = ?", (now, text, object_name, actor_name, same[0]))
                nid = same[0]
            else:
                nid = f"n-{uuid.uuid4().hex[:16]}"
                s._x(f"INSERT INTO agent_session_notification ({_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                     (nid, recipient, kind, actor, actor_name, object_kind, object_id, object_name, url, text, now))
                old = s._x("SELECT id FROM agent_session_notification WHERE recipient = ? AND read_at IS NOT NULL "
                           "ORDER BY created DESC", (recipient,)).fetchall()
                total = s._x("SELECT COUNT(*) FROM agent_session_notification WHERE recipient = ?",
                             (recipient,)).fetchone()[0]
                over = int(total) - KEEP
                for (oid,) in reversed(old):
                    if over <= 0:
                        break
                    s._x("DELETE FROM agent_session_notification WHERE id = ?", (oid,))
                    over -= 1
            row = s._x(f"SELECT {_COLS} FROM agent_session_notification WHERE id = ?", (nid,)).fetchone()
        return _public(row)

    def agent_post(self, owner: str, recipients: list, *, text: str, source: str, source_name: str,
                   object_kind: str, object_id: str, object_name: str = "", url: Optional[str] = None,
                   run_unit: str = "") -> dict:
        """The agent's own notice (`notify`), as ``owner``'s agent. The caller has checked who may
        receive it; this enforces the budget: AGENT_PER_HOUR per owner, one per routine run
        (``run_unit``), and identical text to one person within COALESCE_SEC posted once.
        Returns ``{to: [posted], deduped: [skipped]}``; raises ``AgentRefused``."""
        text = agent_text(text)
        s, now = self._store, self._clock()
        actor = f"agent:{owner}"
        posted, deduped, rows = [], [], []
        with s._tx():
            used = int(s._x("SELECT COUNT(*) FROM agent_session_notification WHERE kind = 'agent' AND actor = ? "
                            "AND created >= ?", (actor, now - 3600)).fetchone()[0])
            if run_unit and s._x("SELECT 1 FROM agent_session_notification WHERE kind = 'agent' AND actor = ? "
                                 "AND source = ? AND created >= ?", (actor, run_unit, now - RUN_WINDOW_SEC)).fetchone():
                raise AgentRefused("one_per_run", 429, "a routine's run posts one notification; this run already has")
            for r in dict.fromkeys(str(x) for x in recipients if x):
                if s._x("SELECT 1 FROM agent_session_notification WHERE recipient = ? AND kind = 'agent' AND text = ? "
                        "AND created >= ?", (r, text, now - COALESCE_SEC)).fetchone():
                    deduped.append(r)
                    continue
                if used >= AGENT_PER_HOUR:
                    raise AgentRefused("rate_limited", 429, f"at most {AGENT_PER_HOUR} agent notifications an hour")
                nid = f"n-{uuid.uuid4().hex[:16]}"
                s._x(f"INSERT INTO agent_session_notification ({_COLS}, source) "
                     "VALUES (?, ?, 'agent', ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)",
                     (nid, r, actor, _name(source_name, "Your agent"), object_kind, str(object_id),
                      _name(object_name, ""), url, text, now, run_unit or source))
                used += 1
                posted.append(r)
                rows.append((r, _public(s._x(f"SELECT {_COLS} FROM agent_session_notification WHERE id = ?",
                                             (nid,)).fetchone())))
        for r, row in rows:
            self._send(r, {"type": "notification", "notification": row})
        return {"to": posted, "deduped": deduped}

    def rail(self, recipients: Iterable[str], *, session_id: str, change: str) -> None:
        """Tell each recipient's rail that a chat joined (``added``) or left (``removed``) it. Not
        stored: the rail reads its chats from the server, this only says *read again now*."""
        for r in dict.fromkeys(str(x) for x in (recipients or []) if x):
            self._send(r, {"type": "rail", "change": change, "session": session_id})

    def send_frame(self, recipient: str, frame: dict) -> None:
        """A live frame on the recipient's own channel that is not a notification and is not stored
        (the home's item patches, #1783). The caller composes ids and states only."""
        self._send(str(recipient), dict(frame))

    def _send(self, recipient: str, frame: dict) -> None:
        if self._publish is None:
            return
        try:
            self._publish(channel(recipient), json.dumps(frame))
        except Exception as exc:  # noqa: BLE001 — the row is stored; the next load shows it
            logger.warning("notification publish failed (%s)", type(exc).__name__)

    # ── reading (always the caller's own rows) ───────────────────────────────────────────────
    def get(self, recipient: str, nid: str) -> Optional[dict]:
        """One of the recipient's own rows with its stored link (``_url``), or None — never another's."""
        s = self._store
        with s._lock:
            r = s._x(f"SELECT {_COLS} FROM agent_session_notification WHERE id = ? AND recipient = ?",
                     (str(nid), str(recipient))).fetchone()
        return None if r is None else {**_public(r), "_url": r[8]}

    def settle(self, recipient: str, nid: str, acted: Optional[str]) -> dict:
        """Mark the recipient's row read and record what they did with it (``accepted``,
        ``declined``, ``joined``); every other tab of theirs is told. Returns the public row."""
        s, now = self._store, self._clock()
        with s._tx():
            s._x("UPDATE agent_session_notification SET read_at = COALESCE(read_at, ?), acted = COALESCE(?, acted) "
                 "WHERE id = ? AND recipient = ?", (now, acted, str(nid), str(recipient)))
            r = s._x(f"SELECT {_COLS} FROM agent_session_notification WHERE id = ? AND recipient = ?",
                     (str(nid), str(recipient))).fetchone()
        row = _public(r)
        self._send(str(recipient), {"type": "notification.update", "notification": row,
                                    "unread": self.unread_count(recipient)})
        return row

    def unread_count(self, recipient: str) -> int:
        s = self._store
        with s._lock:
            return int(s._x("SELECT COUNT(*) FROM agent_session_notification WHERE recipient = ? AND read_at IS NULL",
                            (str(recipient),)).fetchone()[0])

    def listing(self, recipient: str, *, unread_only: bool = False, limit: int = 50,
                before: Optional[float] = None) -> dict:
        """``{notifications: [...newest first], unread: n}`` — the caller's own rows only."""
        s = self._store
        limit = max(1, min(int(limit or 50), LIST_MAX))
        sql = f"SELECT {_COLS} FROM agent_session_notification WHERE recipient = ?"
        args: list = [str(recipient)]
        if unread_only:
            sql += " AND read_at IS NULL"
        if before is not None:
            sql += " AND created < ?"
            args.append(float(before))
        sql += " ORDER BY created DESC LIMIT ?"
        args.append(limit)
        with s._lock:
            rows = s._x(sql, args).fetchall()
        return {"notifications": [_public(r) for r in rows], "unread": self.unread_count(recipient)}

    def mark_read(self, recipient: str, ids: Optional[Iterable[str]] = None, *, every: bool = False) -> dict:
        """Mark the caller's own rows read: the named ``ids`` (an id that is not theirs is not
        touched and not reported), or ``every`` unread row. Returns ``{marked, unread}``."""
        s, now = self._store, self._clock()
        marked = 0
        with s._tx():
            if every:
                marked = int(s._x("SELECT COUNT(*) FROM agent_session_notification WHERE recipient = ? "
                                  "AND read_at IS NULL", (str(recipient),)).fetchone()[0])
                s._x("UPDATE agent_session_notification SET read_at = ? WHERE recipient = ? AND read_at IS NULL",
                     (now, str(recipient)))
            else:
                for nid in dict.fromkeys(str(i) for i in (ids or []) if i):
                    hit = s._x("SELECT 1 FROM agent_session_notification WHERE id = ? AND recipient = ? "
                               "AND read_at IS NULL", (nid, str(recipient))).fetchone()
                    if hit is None:
                        continue
                    s._x("UPDATE agent_session_notification SET read_at = ? WHERE id = ? AND recipient = ?",
                         (now, nid, str(recipient)))
                    marked += 1
        unread = self.unread_count(recipient)
        if marked:
            self._send(str(recipient), {"type": "notification.read", "unread": unread})
        return {"marked": marked, "unread": unread}


def agent_notifications_on(global_root) -> bool:
    """`_global/POLICIES.md` front matter ``agent_notifications: on|off`` — on unless it says off."""
    if global_root is None:
        return True
    try:
        from pathlib import Path  # noqa: PLC0415

        from control_plane.session_access import _front_matter  # noqa: PLC0415
        fm = _front_matter(Path(global_root))
    except Exception:  # noqa: BLE001 — an unreadable policy page is the default
        return True
    return str(fm.get("agent_notifications", "on")).strip().lower() != "off"


def redis_publisher(redis_url: Optional[str]) -> Publish:
    """A ``publish(channel, payload)`` over redis, or None when the deployment has no redis."""
    if not redis_url:
        return None
    try:
        import redis as _redis  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None
    client = _redis.from_url(redis_url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
    return lambda ch, payload: client.publish(ch, payload)
