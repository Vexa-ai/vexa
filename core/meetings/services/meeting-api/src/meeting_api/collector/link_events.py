"""link_events.py — GET /link/events: everything about the person's calls, as one stream for vexa-link.

vexa-link is the background script a person's own Claude runs for the length of a chat. It holds
this one long read (≤25 s, then again) and hands each event to the plugin beside it, whose filter
decides what wakes Claude. Two kinds come from here: `system` — a call of theirs changed state
(requested, waiting to be admitted, live, ended) — and `transcript` — each new line said in a call
of theirs that is live. Presses in the web panel travel on the Claude relay, not here.

The cursor is opaque to the caller: where each call's transcript was read to and the state each call
was last seen in. The first read, with no cursor, reports the calls running now and starts their
transcripts at the end, so a script started mid-call is not handed the whole call again. Each read
also stamps the live calls as listened to (`listener_*`, `listener_mode: link`): the panel's
«Listening». Deterministic, no model.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
import zlib
from typing import Any, Callable, Optional

from fastapi import APIRouter, Header, Query
from fastapi.responses import PlainTextResponse

#: The longest one read may block — under the gateway's buffered forward (30 s).
LINK_WAIT_MAX_S = 25
#: How often a waiting read looks again.
LINK_POLL_S = 2.0
#: The states a call is running in: its transcript is followed and it is stamped as listened to.
RUNNING = ("requested", "joining", "awaiting_admission", "active", "needs_help", "needs_human_help", "stopping")
#: How many of the newest meetings one read looks at; calls end, and an ended one is reported once.
RECENT = 20
#: The most events one answer carries; the rest come in the next.
MAX_EVENTS = 200
#: How many of a call's newest finished lines the cursor remembers, by segment id and a hash of their
#: words: a line finished late, or re-worded after it went out, is sent (again) while it is among them.
#: The cursor stays well under the 4 KB query cap (two 8-character hashes per line).
RECENT_FINALS = 24


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


def _meeting(row: dict) -> dict:
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    return {"id": row.get("id"), "platform": row.get("platform"),
            "native_id": row.get("native_meeting_id"), "title": data.get("title") or ""}


def install(router: APIRouter, *, store: Any, resolve_user: Callable[[Optional[str]], int],
            log_event: Callable[..., Any]) -> None:
    @router.get("/link/events")
    async def link_events(
        cursor: str = Query("", max_length=4096, description=(
            "The `cursor` line of the previous answer; empty to start.")),
        timeout_sec: int = Query(LINK_WAIT_MAX_S, ge=1, le=LINK_WAIT_MAX_S, description=(
            f"How long to wait for something new (1-{LINK_WAIT_MAX_S} s).")),
        x_user_id: Optional[str] = Header(default=None),
    ):
        """The person's calls as events, one JSON object per line after a first line `cursor <c>`:
        `{"type": "system", "event": "status", "meeting": {...}, "status", "previous"}` when a call
        changes state, `{"type": "transcript", "meeting": {...}, "index", "segment_id", "start",
        "absolute_start_time", "speaker", "text"}` per line a running call finished (`index` counts
        finished lines; a line re-worded after it went out comes again under its segment_id). Blocks up to `timeout_sec` until there is something; pass the cursor
        back each time."""
        user_id = resolve_user(x_user_id)
        held = decode_cursor(cursor)
        first = held is None
        read_to: dict = dict((held or {}).get("m") or {})
        seen: dict = dict((held or {}).get("s") or {})
        deadline = time.monotonic() + timeout_sec
        events: list = []
        live: list = []
        while True:
            rows = await store.list_meetings(user_id, limit=RECENT)
            rows = list(rows[0] if isinstance(rows, tuple) else (rows or []))
            live = []
            for row in rows:
                mid, status = str(row.get("id")), str(row.get("status") or "")
                running = status in RUNNING
                if mid not in seen and not running:
                    continue                      # an old call, ended before this link started
                if seen.get(mid) != status:
                    events.append({"type": "system", "event": "status", "meeting": _meeting(row),
                                   "status": status, "previous": seen.get(mid)})
                    seen[mid] = status
                if not running:
                    continue
                live.append(row)
                doc = await store.get_transcript_by_id(user_id, int(row["id"]))
                segments = (doc or {}).get("segments") or []
                finals = _finals(segments)
                if first or not isinstance(read_to.get(mid), dict):
                    # The first read starts at the end (so does a cursor from before segment ids);
                    # a call that starts while the link runs is read from its first line.
                    skip = finals if (first or mid in read_to) else []
                    read_to[mid] = {"n": len(skip), "h": {_line_key(x): _h(x.get("text") or "")
                                                         for x in skip[-RECENT_FINALS:]}}
                mark = read_to[mid]
                sent_to, known = int(mark.get("n") or 0), dict(mark.get("h") or {})
                lo, upto = max(0, sent_to - RECENT_FINALS), len(finals)
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
                    events.append({"type": "transcript", "meeting": _meeting(row), "index": i,
                                   "segment_id": seg.get("segment_id"), "start": seg.get("start"),
                                   "absolute_start_time": seg.get("absolute_start_time"),
                                   "speaker": seg.get("speaker"), "text": seg.get("text")})
                keep = {_line_key(x) for x in finals[max(0, upto - RECENT_FINALS):upto]}
                read_to[mid] = {"n": upto, "h": {k: v for k, v in known.items() if k in keep}}
            first = False
            # A call that ended and was reported once is forgotten; the cursor stays small.
            current = {str(r.get("id")) for r in rows}
            seen = {k: v for k, v in seen.items() if k in current and (v in RUNNING or k in {
                str(e["meeting"]["id"]) for e in events})}
            read_to = {k: v for k, v in read_to.items() if k in seen}
            if events or time.monotonic() >= deadline:
                break
            await asyncio.sleep(min(LINK_POLL_S, max(0.0, deadline - time.monotonic())))
        now_ms = int(time.time() * 1000)
        for row in live:
            try:
                await store.annotate_meeting(user_id, int(row["id"]), metadata={
                    "listener_state": "listening", "listener_ts": now_ms, "listener_mode": "link"})
            except Exception:  # noqa: BLE001 — the events are answered whether or not the stamp lands
                pass
        log_event("link_events", audience="user", span="link.events", user_id=user_id,
                  fields={"events": len(events), "live": len(live)})
        body = [f"cursor {encode_cursor({'m': read_to, 's': seen})}"]
        body += [json.dumps(e, ensure_ascii=False) for e in events]
        return PlainTextResponse("\n".join(body) + "\n")
