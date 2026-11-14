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
        changes state, `{"type": "transcript", "meeting": {...}, "index", "speaker", "text"}` per new
        line in a running call. Blocks up to `timeout_sec` until there is something; pass the cursor
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
                if first or mid not in read_to:
                    read_to[mid] = len(segments) if first else 0
                for i in range(int(read_to[mid]), len(segments)):
                    if len(events) >= MAX_EVENTS:
                        break                     # the rest comes in the next answer
                    seg = segments[i]
                    events.append({"type": "transcript", "meeting": _meeting(row), "index": i,
                                   "speaker": seg.get("speaker"), "text": seg.get("text")})
                    read_to[mid] = i + 1
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
