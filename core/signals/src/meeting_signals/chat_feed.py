"""The chat adapter of the source port: a room's messages (a chat with two or more people).

INPUT is the room's message stream ``room:{session}:msgs`` — agent-api is its only writer; signals
reads it with its consumer group ``signals`` and never writes it. One entry per message, a ``payload``
field holding JSON::

    {"message_id": "m-17", "session": "pchat-…", "subject": "<the room owner's uid>",
     "author": {"kind": "person" | "agent", "id": "…", "name": "…"},
     "text": "…", "at": 1760000000.0, "agent_name": "Vexa",
     "reply_to": {"message_id": "m-12", "author_kind": "agent"}}       # reply_to optional

DISCOVERY: every few seconds the room streams are listed (``SCAN room:*:msgs``) and a room whose
newest entry is new since the last look is reported as active.

THE TIERED SENSOR. Each person's message is, in order:

1. **the fixed rule tier** (free, instant) — ``@vexa`` or ``@<agent name>``, the agent's name as a
   word, a reply to one of the agent's messages, or a question right after an agent message: the line
   is ``addressed`` and the agent wakes at once (``reason: addressed``), no model call;
2. **the agent's own rules** from the room brief (``rules.py``, applied by the listener);
3. **the cheap model**, scoring the message against the room's brief and ``known:``.

The agent's OWN messages are never buffered and never scored: they are context only (and they are
what makes "a question right after an agent message" checkable).

THE BRIEF AND THE POLICY come from agent-api ``GET /internal/chats/{session}/listen?subject=<owner>``
(internal secret), the same answer shape as a meeting's. No answer, or no endpoint yet, is policy off:
the room is not listened to.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Callable

import httpx

from . import brief as briefs
from . import policy as policies
from .model import Line
from .source import Activity, Item, Listening

log = logging.getLogger("signals.chat_feed")

GROUP = "signals"
PATTERN = "room:*:msgs"
SCAN_EVERY_S = 5.0
BRIEF_TTL_S = 30.0
_QUESTION_START = re.compile(r"^\s*(what|how|why|when|where|who|which|can|could|would|should|will|is|are|do|"
                             r"does|did|any|shall)\b", re.IGNORECASE)


def stream(session: str) -> str:
    return f"room:{session}:msgs"


def _payload(fields: dict):
    try:
        p = json.loads((fields or {}).get("payload") or "{}")
    except (TypeError, ValueError):
        return None
    return p if isinstance(p, dict) else None


def is_question(text: str) -> bool:
    return "?" in text or bool(_QUESTION_START.match(text))


def names_agent(text: str, agent_name: str) -> bool:
    names = {"vexa"} | ({agent_name.strip().casefold()} if agent_name.strip() else set())
    folded = text.casefold()
    for n in names:
        if "@" + n in folded or re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", folded):
            return True
    return False


def parse(entry_id: str, fields: dict, prev: dict | None) -> Item:
    """One room message → an Item. ``prev`` is the payload of the message before it, if any."""
    p = _payload(fields)
    if p is None:
        return Item(entry_id, "other")
    text = str(p.get("text") or "").strip()
    author = p.get("author") if isinstance(p.get("author"), dict) else {}
    if not text:
        return Item(entry_id, "other")
    try:
        at = float(p.get("at") or 0.0)
    except (TypeError, ValueError):
        at = 0.0
    line = Line(str(p.get("message_id") or entry_id), str(author.get("name") or "Someone"), text[:4000], at, at)
    if author.get("kind") == "agent":
        return Item(entry_id, "line", line=line, mode="context")
    reply = p.get("reply_to") if isinstance(p.get("reply_to"), dict) else {}
    prev_author = ((prev or {}).get("author") or {}) if isinstance((prev or {}).get("author"), dict) else {}
    addressed = (names_agent(text, str(p.get("agent_name") or ""))
                 or reply.get("author_kind") == "agent"
                 or (prev_author.get("kind") == "agent" and is_question(text)))
    return Item(entry_id, "line", line=line, mode="addressed" if addressed else "score")


class ChatBriefReader:
    def __init__(self, agent_url: str, secret: str, *, transport=None, now: Callable[[], float] = time.monotonic):
        self.base = str(agent_url or "").rstrip("/")
        self.secret, self.transport, self.now = secret, transport, now
        self._cache: dict = {}
        self._said: set = set()

    def _say(self, line: str) -> None:
        if line not in self._said:
            self._said.add(line)
            log.warning("%s", line)

    async def get(self, session: str, subject: str) -> Listening:
        hit = self._cache.get(session)
        if hit and self.now() - hit[1] < BRIEF_TTL_S:
            return hit[0]
        value = Listening(hit[0].brief if hit else briefs.DEFAULT, policies.OFF, subject)
        if subject and self.base:
            try:
                async with httpx.AsyncClient(transport=self.transport, timeout=3.0) as client:
                    r = await client.get(f"{self.base}/internal/chats/{session}/listen", params={"subject": subject},
                                         headers={"X-Internal-Secret": self.secret, "X-User-Id": "signals"})
                if r.status_code == 200:
                    body = r.json()
                    raw = dict(body.get("brief") or {})
                    raw.setdefault("version", body.get("version") or 0)
                    policy, problems = policies.resolve(body.get("policy"))
                    for p in problems:
                        self._say(f"policy: {p}")
                    value = Listening(briefs.normalize(raw), policy, subject)
                else:
                    self._say(f"room {session}: brief HTTP {r.status_code} — not listening")
            except (httpx.HTTPError, ValueError) as exc:
                self._say(f"room {session}: brief unreadable ({type(exc).__name__}) — not listening")
        self._cache[session] = (value, self.now())
        return value


class ChatFeed:
    kind = "chat"
    event_type = "meeting.signal"         # the same wake event; `source.kind` says it is a room

    def __init__(self, redis, briefs_reader: ChatBriefReader, *, consumer: str,
                 now: Callable[[], float] = time.monotonic):
        self.r, self.briefs, self.consumer, self.now = redis, briefs_reader, consumer, now
        self._last: dict = {}
        self._scanned_at = None

    async def discover(self) -> list:
        if self._scanned_at is not None and self.now() - self._scanned_at < SCAN_EVERY_S:
            return []
        self._scanned_at = self.now()
        out = []
        async for key in self.r.scan_iter(match=PATTERN, count=500):
            session = str(key)[len("room:"):-len(":msgs")]
            if not session:
                continue
            last = await self.r.xrevrange(key, count=1)
            if not last:
                continue
            entry_id, fields = last[0]
            if self._last.get(session) == entry_id:
                continue
            self._last[session] = entry_id
            p = _payload(fields) or {}
            subject = str(p.get("subject") or "")
            out.append(Activity(session, int(str(entry_id).split("-")[0] or 0),
                                {"subject": subject} if subject else {}))
        return out

    async def open(self, sid: str, from_ms: int) -> bool:
        if not await self.r.exists(stream(sid)):
            return False
        try:
            await self.r.xgroup_create(stream(sid), GROUP, id=f"{max(0, int(from_ms) - 2000)}-0")
        except Exception as exc:  # noqa: BLE001
            if "BUSYGROUP" not in str(exc):
                raise
        return True

    async def _prev(self, sid: str, entry_id: str):
        rows = await self.r.xrevrange(stream(sid), max=f"({entry_id}", count=1)
        return _payload(rows[0][1]) if rows else None

    async def read(self, sid: str, consumer: str, after: str) -> list:
        claimed = await self.r.xautoclaim(stream(sid), GROUP, consumer, min_idle_time=0, start_id="0-0", count=100)
        entries = [e for e in (claimed[1] if claimed else []) if e and e[1] is not None]
        if not entries:
            resp = await self.r.xreadgroup(GROUP, consumer, {stream(sid): ">"}, count=16, block=1000)
            entries = [e for _s, es in (resp or []) for e in es]
        out, prev = [], None
        for i, (eid, fields) in enumerate(entries):
            if i == 0:
                prev = await self._prev(sid, str(eid))
            out.append(parse(str(eid), fields, prev))
            prev = _payload(fields)
        return out

    async def ack(self, sid: str, entry_ids: list) -> None:
        if entry_ids:
            await self.r.xack(stream(sid), GROUP, *entry_ids)

    async def context(self, sid: str, before: str, n: int) -> list:
        rows = await self.r.xrevrange(stream(sid), max=f"({before}", count=n) if before else []
        lines = [it.line for it in (parse(str(e), f, None) for e, f in rows) if it.kind == "line"]
        return list(reversed(lines))

    def wake_target(self, sid: str) -> dict:
        return {"session": sid}

    def wake_chat(self, sid: str, brief) -> str:
        return sid

    async def listening(self, sid: str, meta: dict) -> Listening:
        return await self.briefs.get(sid, str((meta or {}).get("subject") or ""))
