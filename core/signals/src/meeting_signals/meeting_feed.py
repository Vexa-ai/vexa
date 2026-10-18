"""The meeting adapter of the source port: a live meeting's verified transcript feed.

DISCOVERY is the bots' shared ``transcription_segments`` stream, read with signals' own consumer
group. An entry there is a HINT and nothing more (the same rule as agent-api's transcription
watcher): it names a meeting row whose verified feed to read. Hints are acknowledged at once.

READING is the collector's per-meeting feed ``tc:meeting:{row}`` — meetings is its only writer; only
entries the collector admitted reach it. signals reads it with a consumer group ``signals``, created
from just before the first hint (a meeting signals joins late is not scored from its start), and
takes only CONFIRMED lines (``completed: true``, non-empty text). Entries another reader left pending
(a dead instance) are claimed first. ``session_end`` ends the source; ``retract`` withdraws lines.

THE OWNER: the hint's ``auth`` is the bot session's MeetingToken header.payload, whose claims name
the meeting's owner (``user_id``). It is read WITHOUT verification (signals does not hold the token
key) and only to ask agent-api: agent-api reads the row AS that subject, so a hint naming someone who
cannot read the meeting gets a 404 and signals listens to nothing. The first owner named for a row
is kept.

THE BRIEF AND THE POLICY come from agent-api: ``GET /internal/meetings/{row}/listen?subject=<owner>``
with the internal secret, cached 30 seconds. It answers the brief (the company default when the page
has none) and the company's signal policy. No answer is policy off.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from typing import Callable, Optional

import httpx

from . import brief as briefs
from . import policy as policies
from .model import Line
from .source import Activity, Item, Listening

log = logging.getLogger("signals.meeting_feed")

HINTS = "transcription_segments"
GROUP = "signals"
HINT_SKEW_MS = 2000
BRIEF_TTL_S = 30.0


def feed(row: str) -> str:
    return f"tc:meeting:{row}"


#: THE RULE TIER: Vexa named at the start of a clause ("Vexa, …", "ok vexa …", "… . Vexa can you"),
#: "@vexa", or the "To Vexa" tag. Heard on an interim it wakes at once; on a FINAL it is marked
#: addressed too, so being addressed by name never depends on the signal model (app.dev 2026-10-17,
#: meeting 153: every model call answered 402, and "Векса, привет" on a final was sent to it, refused,
#: and buffered unscored until the call ended). The name as a Russian transcript spells it — "Векса",
#: "Вексе" (the vocative the ASR writes), "Вэкса" — counts like the Latin one; the other case forms
#: ("у Вексы", "Вексой") are mentions, not an address, and stay with the model.
_NAME = r"(?:vexa\b(?!['’]s)|в[еэ]кс[аеэ]?(?!\w))"
_LEAD = r"(?:hey|hi|ok|okay|so|and|but|please|well|привет|эй|ну|ок|окей|слушай|скажи|пожалуйста)"
_ADDRESS = re.compile(rf"(?:^|[.!?;:,]\s*|(?<!\w){_LEAD}\s+){_NAME}"
                      r"|@vexa\b|\bto\s+vexa\b", re.IGNORECASE)


def addresses_vexa(text: str) -> bool:
    return bool(_ADDRESS.search(text or ""))


def parse(entry_id: str, fields: dict) -> Item:
    try:
        p = json.loads((fields or {}).get("payload") or "{}")
    except (TypeError, ValueError):
        return Item(entry_id, "other")
    if not isinstance(p, dict):
        return Item(entry_id, "other")
    kind = p.get("type")
    if kind == "session_end":
        return Item(entry_id, "end")
    if kind == "retract":
        return Item(entry_id, "retract", ids=tuple(str(i) for i in p.get("segment_ids") or []))
    if kind != "transcription":
        return Item(entry_id, "other")
    segs = p.get("segments") or []
    s = segs[0] if segs and isinstance(segs[0], dict) else {}
    text = str(s.get("text") or "").strip()
    if not text:
        return Item(entry_id, "other")
    interim = s.get("completed") is not True
    if interim and not addresses_vexa(text):
        return Item(entry_id, "other")
    try:
        start, end = float(s.get("start") or 0.0), float(s.get("end") or s.get("start") or 0.0)
    except (TypeError, ValueError):
        start = end = 0.0
    line = Line(str(s.get("segment_id") or entry_id), str(s.get("speaker") or "Unknown speaker"), text, start, end)
    if interim:
        return Item(entry_id, "line", line=line, mode="addressed", interim=True)
    if addresses_vexa(text):
        return Item(entry_id, "line", line=line, mode="addressed")
    return Item(entry_id, "line", line=line)


def owner_of(fields: dict) -> str:
    """The ``user_id`` the hint's MeetingToken claims name, unverified (see the module docstring)."""
    try:
        claims = str((fields or {}).get("auth") or "").split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))
    except (IndexError, ValueError, TypeError):
        return ""
    uid = data.get("user_id") if isinstance(data, dict) else None
    return str(uid) if isinstance(uid, (int, str)) and str(uid).isdigit() else ""


class BriefReader:
    def __init__(self, agent_url: str, secret: str, *, transport=None, now: Callable[[], float] = time.monotonic):
        self.base = str(agent_url or "").rstrip("/")
        self.secret, self.transport, self.now = secret, transport, now
        self._cache: dict = {}
        self._said: set = set()

    def _say(self, line: str) -> None:
        if line not in self._said:
            self._said.add(line)
            log.warning("%s", line)

    async def get(self, row: str, subject: str) -> Listening:
        hit = self._cache.get(row)
        if hit and self.now() - hit[1] < BRIEF_TTL_S:
            return hit[0]
        value = Listening(hit[0].brief if hit else briefs.DEFAULT, policies.OFF, subject)
        if not subject:
            self._say(f"meeting {row}: no owner known from its hints — not listening")
        elif self.base:
            try:
                async with httpx.AsyncClient(transport=self.transport, timeout=3.0) as client:
                    r = await client.get(f"{self.base}/internal/meetings/{row}/listen", params={"subject": subject},
                                         headers={"X-Internal-Secret": self.secret, "X-User-Id": "signals"})
                if r.status_code == 200:
                    body = r.json()
                    extra = {k: body.get(k) for k in ("workspace", "page_path", "chat_session") if body.get(k)}
                    raw = dict(body.get("brief") or {})
                    raw.setdefault("version", body.get("version") or 0)
                    policy, problems = policies.resolve(body.get("policy"))
                    for p in problems:
                        self._say(f"policy: {p}")
                    value = Listening(briefs.normalize(raw, extra=extra), policy, subject)
                else:
                    self._say(f"brief for meeting {row}: HTTP {r.status_code} — not listening")
            except (httpx.HTTPError, ValueError) as exc:
                self._say(f"brief for meeting {row} unreadable ({type(exc).__name__}) — not listening")
        self._cache[row] = (value, self.now())
        return value


class MeetingFeed:
    kind = "meeting"
    event_type = "meeting.signal"

    def __init__(self, redis, briefs_reader: BriefReader, *, consumer: str):
        self.r, self.briefs, self.consumer = redis, briefs_reader, consumer
        self._group_ready = False

    async def _ensure_hint_group(self) -> None:
        if self._group_ready:
            return
        try:
            await self.r.xgroup_create(HINTS, GROUP, id="$", mkstream=True)
        except Exception as exc:  # noqa: BLE001 — BUSYGROUP: another instance made it
            if "BUSYGROUP" not in str(exc):
                raise
        self._group_ready = True

    async def discover(self) -> list:
        await self._ensure_hint_group()
        resp = await self.r.xreadgroup(GROUP, self.consumer, {HINTS: ">"}, count=200, block=1000)
        out: dict = {}
        owners: dict = {}
        for _stream, entries in resp or []:
            for entry_id, fields in entries:
                await self.r.xack(HINTS, GROUP, entry_id)
                try:
                    p = json.loads((fields or {}).get("payload") or "{}")
                except (TypeError, ValueError):
                    continue
                mid = str(p.get("meeting_id") or "") if isinstance(p, dict) else ""
                if mid.isdigit():
                    ms = int(str(entry_id).split("-")[0] or 0)
                    out[mid] = max(out.get(mid, 0), ms)
                    owners.setdefault(mid, owner_of(fields))
        return [Activity(k, v, {"subject": owners.get(k, "")} if owners.get(k) else {}) for k, v in out.items()]

    async def open(self, sid: str, from_ms: int) -> bool:
        if not await self.r.exists(feed(sid)):
            return False          # nothing admitted yet; never create the collector's carrier
        try:
            await self.r.xgroup_create(feed(sid), GROUP, id=f"{max(0, int(from_ms) - HINT_SKEW_MS)}-0")
        except Exception as exc:  # noqa: BLE001
            if "BUSYGROUP" not in str(exc):
                raise
        return True

    async def read(self, sid: str, consumer: str, after: str) -> list:
        claimed = await self.r.xautoclaim(feed(sid), GROUP, consumer, min_idle_time=0, start_id="0-0", count=100)
        entries = claimed[1] if claimed else []
        entries = [e for e in entries if e and e[1] is not None]
        if not entries:
            resp = await self.r.xreadgroup(GROUP, consumer, {feed(sid): ">"}, count=16, block=1000)
            entries = [e for _s, es in (resp or []) for e in es]
        return [parse(str(eid), fields) for eid, fields in entries]

    async def ack(self, sid: str, entry_ids: list) -> None:
        if entry_ids:
            await self.r.xack(feed(sid), GROUP, *entry_ids)

    async def context(self, sid: str, before: str, n: int) -> list:
        rows = await self.r.xrevrange(feed(sid), max=f"({before}", count=n * 3) if before else []
        lines = [it.line for it in (parse(str(e), f) for e, f in rows) if it.kind == "line"]
        return list(reversed(lines[:n]))

    def wake_target(self, sid: str) -> dict:
        return {"meeting_row": sid}

    def wake_chat(self, sid: str, brief) -> str:
        """The chat this meeting's wake lands in (agent-api's answer), for coalescing."""
        return str((getattr(brief, "extra", None) or {}).get("chat_session") or "")

    async def listening(self, sid: str, meta: dict) -> Listening:
        return await self.briefs.get(sid, str((meta or {}).get("subject") or ""))
