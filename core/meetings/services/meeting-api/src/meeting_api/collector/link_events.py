"""link_events.py — GET /link/events: everything about the person's calls, as one stream for vexa-link.

vexa-link is the background script a person's own Claude runs for the length of a chat. It holds
this one long read (≤25 s, then again) and hands each event to the plugin beside it, whose filter
decides what wakes Claude. Two kinds come from here: `system` — a call of theirs changed state
(requested, waiting to be admitted, live, ended) — and `transcript` — each new line said in a call
of theirs that is live. Presses in the web panel travel on the Claude relay, not here.

"Theirs" is every call they may read (policy P12, design mvp1-shared-listen): calls they own, calls
shared with them (a redeemed grant), and calls bound to a workspace they are a member of — the one
rule finished transcripts are read by (``get_transcript_by_id``), so live and finished answer alike.
Each event's ``meeting`` says which (``access``: owner | viewer | workspace). A call they may no
longer read (the owner removed them, revoked the link, deleted the transcript) leaves the stream with
one ``{"type": "system", "event": "access_ended"}``; the lines already delivered stay delivered.

The cursor is opaque to the caller: where each call's transcript was read to and the state each call
was last seen in. The first read, with no cursor, reports the calls running now and starts their
transcripts at the end, so a script started mid-call is not handed the whole call again. Each read
also stamps the live calls the person OWNS as listened to (`listener_*`, `listener_mode: link`): the
panel's «Listening»; a call shared with them is never stamped. Deterministic, no model.

Load (design §3): each tick is ONE narrow read (``store.listen_calls``: owner, room mark, viewers,
workspace, deletion, status — no blob) of the running calls and the held ones, and a call's
transcript is read only when its feed moved since the cursor (``mark``).

The cursor is the caller's to forge (unsigned on purpose: no key to keep). Nothing in it is trusted:
every id it holds is re-checked by the same access rule each tick, a call the caller cannot read
(or that does not exist) answers only ``access_ended`` with its id, and the store's held read hands
back no column of such a row but its id (code review H1). The cursor never outgrows the 4 KB query
cap: past ``CURSOR_BUDGET`` each call remembers fewer recent lines (``w``), so it degrades instead of
failing (M1). One pod's pool is shared: at most ``LINK_PER_USER`` open reads per person (429 beyond,
counted in Redis, refused when Redis cannot count) and ``LINK_DB_CONCURRENCY`` reads' database work
at a time (M2).
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
import weakref
import zlib
from typing import Any, Callable, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from ..regime import shared_meeting_readable
from . import share_access

#: The longest one read may block — under the gateway's buffered forward (30 s).
LINK_WAIT_MAX_S = 25
#: How often a waiting read looks again.
LINK_POLL_S = 2.0
#: The states a call is running in: its transcript is followed and it is stamped as listened to.
RUNNING = ("requested", "joining", "awaiting_admission", "active", "needs_help", "needs_human_help", "stopping")
#: The cursor's one-letter access marks (it stays well under the 4 KB query cap).
_SHORT = {share_access.OWNER: "o", share_access.VIEWER: "v", share_access.WORKSPACE: "w"}
_LONG = {v: k for k, v in _SHORT.items()}
#: The most events one answer carries; the rest come in the next.
MAX_EVENTS = 200
#: How many of a call's newest finished lines the cursor remembers, by segment id and a hash of their
#: words: a line finished late, or re-worded after it went out, is sent (again) while it is among them.
#: The cursor stays well under the 4 KB query cap (two 8-character hashes per line).
RECENT_FINALS = 24
#: The longest cursor this route answers: under the 4096-character `cursor` query cap, with room.
CURSOR_BUDGET = 3800
#: The most held calls one cursor may name; the rest of a forged list is ignored.
MAX_HELD = 32
#: Open /link/events reads one person may hold at once (one vexa-link, plus one restarting).
LINK_PER_USER = 2
#: How long an open-read count outlives a process that died holding it.
LINK_OPEN_TTL_S = 60
#: Reads whose database work runs at once in this process. Per pod: prod runs 3 meeting-api replicas
#: with a pool of 5 + 10 each (45 connections), so listening holds at most 3 x 6 = 18 of them at once
#: and the rest of meeting-api always keeps 27.
LINK_DB_CONCURRENCY = 6

_gates: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_local_open: dict = {}


def _db_gate() -> asyncio.Semaphore:
    """This event loop's gate on tick database work (one per loop: a semaphore binds to its loop)."""
    loop = asyncio.get_running_loop()
    gate = _gates.get(loop)
    if gate is None:
        gate = _gates[loop] = asyncio.Semaphore(LINK_DB_CONCURRENCY)
    return gate


def fit_cursor(state: dict, budget: int = None) -> str:
    """The cursor for ``state``, no longer than ``budget``: while it is too long, every call remembers
    fewer of its recent lines (its ``h``, newest kept, and its window ``w``), down to none. A shorter
    window only narrows how far back a late-finished or re-worded line is still sent."""
    budget = CURSOR_BUDGET if budget is None else budget
    out = encode_cursor(state)
    marks = [m for m in (state.get("m") or {}).values() if isinstance(m, dict)]
    while len(out) > budget:
        widest = max((int(m.get("w", RECENT_FINALS)) for m in marks), default=0)
        if widest <= 0:
            break
        keep = widest // 2
        for m in marks:
            if int(m.get("w", RECENT_FINALS)) > keep:
                items = list((m.get("h") or {}).items())
                m["h"] = dict(items[-keep:]) if keep else {}
                m["w"] = keep
        out = encode_cursor(state)
    return out


def encode_cursor(state: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(state, separators=(",", ":")).encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> Optional[dict]:
    if not cursor:
        return None
    try:
        state = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        return state if isinstance(state, dict) else None
    except ValueError:
        return None


def _finals(segments: list) -> list:
    """A call's finished lines, in transcript order. A live draft (`completed` false) is not yet what
    was said: it is re-worded and merged as it settles, so sending it once by position sent first
    drafts and never the words (call 284, 3 Nov: «Обновить ноут с этого звонка» reached the listener
    as «Слышишь, можно...»)."""
    return [s for s in segments if s.get("completed") is not False]


def _h(value: Any) -> str:
    return format(zlib.crc32(str(value).encode()), "08x")


def _line_key(seg: dict) -> str:
    return _h(seg.get("segment_id") or seg.get("start"))


def _meeting(row: dict, access: Optional[str] = None) -> dict:
    """What a listener learns of a call: its id, platform, native id, title, and how they read it.
    Nothing else of the owner's (no roster, grants, settings or bot config)."""
    return {"id": row.get("id"), "platform": row.get("platform"),
            "native_id": row.get("native_meeting_id"), "title": row.get("title") or "",
            "access": access}


def _ended(mid: str) -> dict:
    """The one event a call that can no longer be read leaves with: its id and nothing else, the same
    for a call that was revoked, deleted, never theirs or never existed (a forged cursor learns
    nothing from it)."""
    return {"type": "system", "event": "access_ended", "meeting": {"id": int(mid) if mid.isdigit() else mid}}


class _Busy(Exception):
    pass


async def _open(counter: Any, user_id: int) -> str:
    """Count one open read for ``user_id``; refuse past ``LINK_PER_USER``. Counted in Redis so every
    pod sees it; a Redis that does not answer refuses (fails closed). Without a Redis (the in-memory
    stores) it is counted in this process."""
    key = f"link:open:{user_id}"
    if counter is None:
        if _local_open.get(key, 0) >= LINK_PER_USER:
            raise _Busy()
        _local_open[key] = _local_open.get(key, 0) + 1
        return key
    try:
        n = await counter.incr(key)
        await counter.expire(key, LINK_OPEN_TTL_S)
    except Exception as e:  # noqa: BLE001 — cannot count: refuse
        raise _Busy() from e
    if int(n) > LINK_PER_USER:
        await _close(counter, key)
        raise _Busy()
    return key


async def _close(counter: Any, key: str) -> None:
    if counter is None:
        _local_open[key] = max(0, _local_open.get(key, 0) - 1)
        return
    try:
        await counter.decr(key)
    except Exception:  # noqa: BLE001 — the TTL ends a count no one could take back
        pass


def install(router: APIRouter, *, store: Any, resolve_user: Callable[[Optional[str]], int],
            log_event: Callable[..., Any], counter: Any = None) -> None:
    @router.get("/link/events")
    async def link_events(
        request: Request,
        cursor: str = Query("", max_length=4096, description=(
            "The `cursor` line of the previous answer; empty to start.")),
        timeout_sec: int = Query(LINK_WAIT_MAX_S, ge=1, le=LINK_WAIT_MAX_S, description=(
            f"How long to wait for something new (1-{LINK_WAIT_MAX_S} s).")),
        x_user_id: Optional[str] = Header(default=None),
        x_user_workspaces: Optional[str] = Header(default=None),
    ):
        """The person's calls as events, one JSON object per line after a first line `cursor <c>`:
        `{"type": "system", "event": "status", "meeting": {...}, "status", "previous"}` when a call
        changes state, `{"type": "transcript", "meeting": {...}, "index", "segment_id", "start",
        "absolute_start_time", "speaker", "text"}` per line a running call finished (`index` counts
        finished lines; a line re-worded after it went out comes again under its segment_id), and
        `{"type": "system", "event": "access_ended", "meeting": {"id"}}` once when a call they followed
        can no longer be read by them. `meeting.access` is `owner`, `viewer` (shared with them) or
        `workspace` (bound to a workspace of theirs). Blocks up to `timeout_sec` until there is
        something; pass the cursor back each time."""
        user_id = resolve_user(x_user_id)
        try:
            slot = await _open(counter, user_id)
        except _Busy:
            log_event("link_events_busy", audience="system", level="warning", span="link.events",
                      user_id=user_id)
            return PlainTextResponse("too many open reads\n", status_code=429,
                                     headers={"Retry-After": str(int(LINK_POLL_S) + 1)})
        try:
            return await _serve(request, cursor, timeout_sec, user_id, x_user_workspaces)
        finally:
            await _close(counter, slot)

    async def _serve(request, cursor, timeout_sec, user_id, x_user_workspaces):
        # Workspace membership is the header set when this read opened (the gateway's); a member
        # removed meanwhile still reads until this read ends (≤ LINK_WAIT_MAX_S, design §4).
        member_workspaces = {w.strip() for w in (x_user_workspaces or "").split(",") if w.strip()}
        shared_turn = bool(getattr(request.state, "shared_turn", False))

        def admitted(row: dict) -> Optional[str]:
            """The access label, narrowed to the turn's ceiling for a turn other people read (only a
            call bound to a workspace inside it, `regime.shared_meeting_readable`)."""
            access = row.get("access")
            if access and shared_turn:
                try:
                    shared_meeting_readable(request, {"data": {"workspace_id": row.get("workspace_id")}})
                except HTTPException:
                    return None
            return access

        def audit_end(mid: str, mark: Any, reason: str, owner_id: Any = None) -> None:
            if not str(mid).isdigit():
                return
            if isinstance(mark, dict) and mark.get("a") in ("v", "w"):
                log_event("listen_shared_end", audience="system", span="link.events", user_id=user_id,
                          meeting_id=mid, fields={"reason": reason, "access": _LONG[mark["a"]],
                                                  "owner_id": owner_id})

        held = decode_cursor(cursor)
        first = held is None
        read_to: dict = (held or {}).get("m") if isinstance((held or {}).get("m"), dict) else {}
        seen: dict = (held or {}).get("s") if isinstance((held or {}).get("s"), dict) else {}
        # Only plain ids, at most MAX_HELD: the rest of a forged cursor is dropped, not answered.
        ids = sorted({k for k in set(seen) | set(read_to) if str(k).isdigit()}, key=int)[:MAX_HELD]
        seen = {k: v for k, v in seen.items() if k in ids}
        read_to = {k: v for k, v in read_to.items() if k in ids}
        deadline = time.monotonic() + timeout_sec
        events: list = []
        live: list = []
        while True:
            held_ids = [int(k) for k in set(seen) | set(read_to)]
            async with _db_gate():
                rows = await store.listen_calls(user_id, member_workspaces=member_workspaces,
                                                held_ids=held_ids, statuses=RUNNING)
            by_id = {str(r.get("id")): r for r in rows}
            # A call followed until now that this caller can no longer read: one event, then gone.
            for mid in sorted(set(seen) | set(read_to)):
                row = by_id.get(mid)
                if row is not None and admitted(row):
                    continue
                events.append(_ended(mid))
                audit_end(mid, read_to.get(mid), "revoked")
                seen.pop(mid, None)
                read_to.pop(mid, None)
            live = []
            for row in sorted(rows, key=lambda r: int(r.get("id") or 0), reverse=True):
                access = admitted(row)
                if not access:
                    continue
                mid, status = str(row.get("id")), str(row.get("status") or "")
                running = status in RUNNING
                if mid not in seen and not running:
                    continue                      # an old call, ended before this link started
                if seen.get(mid) != status:
                    events.append({"type": "system", "event": "status", "meeting": _meeting(row, access),
                                   "status": status, "previous": seen.get(mid)})
                    seen[mid] = status
                if not running:
                    audit_end(mid, read_to.pop(mid, None), "ended", row.get("user_id"))
                    continue
                live.append((row, access))
                mark = read_to.get(mid) if isinstance(read_to.get(mid), dict) else None
                if (not first and mark is not None and row.get("mark") is not None
                        and mark.get("c") == row["mark"]):
                    continue                      # its transcript has not moved: nothing to read
                async with _db_gate():
                    doc = await store.get_transcript_by_id(user_id, int(row["id"]), member_workspaces)
                if doc is None:                   # gone between the two reads: the same as revoked
                    events.append(_ended(mid))
                    audit_end(mid, mark, "revoked", row.get("user_id"))
                    seen.pop(mid, None)
                    read_to.pop(mid, None)
                    live.pop()
                    continue
                segments = doc.get("segments") or []
                finals = _finals(segments)
                if mark is None:
                    # The first read starts at the end (so does a cursor from before segment ids);
                    # a call that starts while the link runs is read from its first line.
                    skip = finals if (first or mid in read_to) else []
                    mark = {"n": len(skip), "h": {_line_key(x): _h(x.get("text") or "")
                                                   for x in skip[-RECENT_FINALS:]}}
                    if access != share_access.OWNER:
                        log_event("listen_shared", audience="system", span="link.events",
                                  user_id=user_id, meeting_id=mid,
                                  fields={"access": access, "owner_id": row.get("user_id")})
                sent_to, known = int(mark.get("n") or 0), dict(mark.get("h") or {})
                window = max(0, min(RECENT_FINALS, int(mark.get("w", RECENT_FINALS))))
                lo, upto = max(0, sent_to - window), len(finals)
                for i in range(lo, len(finals)):
                    seg = finals[i]
                    key, words = _line_key(seg), _h(seg.get("text") or "")
                    if known.get(key) == words:
                        continue                  # already sent with these words
                    if len(events) >= MAX_EVENTS:
                        upto = i
                        break                     # the rest comes in the next answer
                    known[key] = words
                    if not str(seg.get("text") or "").strip():
                        continue
                    events.append({"type": "transcript", "meeting": _meeting(row, access), "index": i,
                                   "segment_id": seg.get("segment_id"), "start": seg.get("start"),
                                   "absolute_start_time": seg.get("absolute_start_time"),
                                   "speaker": seg.get("speaker"), "text": seg.get("text")})
                # The window the cursor vouches for: the newest finished lines, contiguous back from
                # `upto`, whose words are known (a cursor that had to shrink knows fewer). In
                # transcript order, so fit_cursor keeps the newest when it must shrink further.
                recent = [_line_key(x) for x in finals[max(0, upto - RECENT_FINALS):upto]]
                width = 0
                for k in reversed(recent):
                    if k not in known:
                        break
                    width += 1
                covered = recent[len(recent) - width:]
                read_to[mid] = {"n": upto, "h": {k: known[k] for k in covered}, "a": _SHORT[access]}
                if width < RECENT_FINALS:
                    read_to[mid]["w"] = width
                if upto == len(finals) and row.get("mark") is not None:
                    read_to[mid]["c"] = row["mark"]   # everything up to this mark is sent
            first = False
            # A call that ended and was reported once is forgotten; the cursor stays small.
            reported = {str(e["meeting"]["id"]) for e in events}
            seen = {k: v for k, v in seen.items() if v in RUNNING or k in reported}
            seen = {k: v for k, v in seen.items() if k in by_id or k in reported}
            read_to = {k: v for k, v in read_to.items() if k in seen and seen[k] in RUNNING}
            if events or time.monotonic() >= deadline:
                break
            await asyncio.sleep(min(LINK_POLL_S, max(0.0, deadline - time.monotonic())))
        now_ms = int(time.time() * 1000)
        for row, access in live:
            if access != share_access.OWNER:
                continue                          # only the owner's call shows «Listening» (owner-only stamp)
            try:
                async with _db_gate():
                    await store.annotate_meeting(user_id, int(row["id"]), metadata={
                        "listener_state": "listening", "listener_ts": now_ms, "listener_mode": "link"})
            except Exception:  # noqa: BLE001 — the events are answered whether or not the stamp lands
                pass
        log_event("link_events", audience="user", span="link.events", user_id=user_id,
                  fields={"events": len(events), "live": len(live),
                          "shared": sum(1 for _, a in live if a != share_access.OWNER)})
        body = [f"cursor {fit_cursor({'m': read_to, 's': seen})}"]
        body += [json.dumps(e, ensure_ascii=False) for e in events]
        return PlainTextResponse("\n".join(body) + "\n")
