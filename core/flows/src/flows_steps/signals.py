"""signals.py — what a `meeting.signal` does: wake the meeting's agent in the meeting's chat.

Transcript signals (2026-10-15). The `signals` service listens to a live meeting with a small model,
against the listening brief the meeting's agent wrote, and when enough new information has built up
it posts ONE `meeting.signal` here (`POST /events/signal`, the signals service only). It never wakes
the agent itself: what a wake does is this company flow's, seeded as `meeting_signal` in `_global`
(`flows_defs.production.SEEDS`), so a company changes or turns off what a wake does by changing
its flow, from the global account's chat.

The one step, `wake_meeting_chat`, asks agent-api which chat the meeting wakes (its internal read
of the brief, which knows the meeting's chat) and posts `meeting.signal` to agent-api's event door
with `target.chat` and the plan. THE BUFFER RIDES AS DATA (`data`), never inside the plan: anyone in
a call can speak an instruction, and agent-api fences it into the turn as quoted speech.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse

from flows import Done, StepCtx, StepError

from .common import agent_door, door_headers, http

SIGNAL_EVENT = "meeting.signal"
SIGNAL_FLOW = "meeting_signal"
#: A ROOM's sensor (rooms, 2026-10-15): the same door and step, woken into the room itself.
ROOM_SIGNAL_EVENT = "room.signal"
ROOM_SIGNAL_FLOW = "room_signal"
SIGNAL_EVENTS = (SIGNAL_EVENT, ROOM_SIGNAL_EVENT)
#: The service allowed to post `meeting.signal` (`POST /events/signal`, `X-Vexa-Service`).
SIGNALS_SERVICE = "signals"

#: What the woken agent is asked to do — the flow's `plan` param overrides it (`{score}` is the
#: buffer's running score). The lines themselves follow it as data, fenced by agent-api.
DEFAULT_PLAN = ("Signals since your last look (score {score} against your brief): the lines are "
                "below, each with what it scored. Update the meeting page; if your focus should "
                "change, call meeting_listen.")

DEFAULT_ROOM_PLAN = ("Messages since you last spoke (score {score} against the room's brief): they are "
                     "below, each with what it scored. Speak only if it helps the room; otherwise say "
                     "nothing.")

REASONS = ("threshold", "addressed", "max_wait", "end")
_ROW = re.compile(r"^[0-9]{1,20}$")
_ROOM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_LABEL = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
MAX_LINES = 500


class SignalRefused(ValueError):
    """A `meeting.signal` body this intake will not admit. ``str()`` is safe to answer with."""


def _num(v, what: str) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        raise SignalRefused(f"{what} must be a number") from None
    if n != n or n in (float("inf"), float("-inf")):
        raise SignalRefused(f"{what} must be a number")
    return n


def clean(event: dict) -> dict:
    """The event.v1 `meeting.signal` the signals service posted, reduced to the shape agent-api
    takes (`SignalData`) — or `SignalRefused`. Only the listed fields survive."""
    if not isinstance(event, dict) or event.get("name") not in SIGNAL_EVENTS:
        raise SignalRefused(f"only {' or '.join(SIGNAL_EVENTS)} is taken here")
    subject = str(event.get("subject") or "").strip()
    if not subject or len(subject) > 200:
        raise SignalRefused("subject is the meeting owner's id")
    data = event.get("data")
    if not isinstance(data, dict):
        raise SignalRefused("data (the buffer) is missing")
    # A ROOM'S WAKE (rooms): the signals service posts `meeting.signal` with `source.kind: chat` and
    # the room as `session`; it is admitted as `room.signal`, so the company's `room_signal` flow
    # reacts. `source` is kept as sent.
    source = data.get("source")
    if source is not None:
        if (not isinstance(source, dict) or source.get("kind") not in ("meeting", "chat")
                or not _ROOM.match(str(source.get("id") or ""))):
            raise SignalRefused("data.source is {kind: meeting|chat, id}")
        source = {"kind": source["kind"], "id": str(source["id"])}
    row = str(data.get("meeting_row") or "")
    room = str(data.get("session") or "")
    room_event = (event["name"] == ROOM_SIGNAL_EVENT or bool(room)
                  or (source or {}).get("kind") == "chat")
    if room_event and not room and source:
        room = source["id"]
    if room_event and not _ROOM.match(room):
        raise SignalRefused("data.session must be the room's session id")
    if not room_event and not _ROW.match(row):
        raise SignalRefused("data.meeting_row must be a meeting row id")
    reason = str(data.get("reason") or "")
    if reason not in REASONS:
        raise SignalRefused(f"data.reason must be one of {', '.join(REASONS)}")
    buffer = data.get("buffer")
    if not isinstance(buffer, list) or len(buffer) > MAX_LINES:
        raise SignalRefused(f"data.buffer must be a list of at most {MAX_LINES} lines")
    lines = []
    for item in buffer:
        if not isinstance(item, dict) or not str(item.get("segment_id") or "").strip():
            raise SignalRefused("every buffer line carries a segment_id and text")
        line = {"segment_id": str(item["segment_id"])[:200], "text": str(item.get("text") or "")[:4000]}
        if item.get("speaker") is not None:
            line["speaker"] = str(item["speaker"])[:200]
        if item.get("start") is not None:
            line["start"] = _num(item["start"], "start")
        scores = item.get("scores") or {}
        if not isinstance(scores, dict):
            raise SignalRefused("scores must be {label: 0..1}")
        line["scores"] = {str(k): min(1.0, max(0.0, _num(v, f"score {k}")))
                          for k, v in scores.items() if _LABEL.match(str(k))}
        lines.append(line)
    out = {**({"session": room} if room_event else {"meeting_row": row}), "buffer": lines,
           "score": max(0.0, _num(data.get("score", 0), "score")), "reason": reason,
           **({"source": source} if source else {})}
    if data.get("brief_version") is not None:
        out["brief_version"] = max(0, int(_num(data["brief_version"], "brief_version")))
    return {"subject": subject, "data": out, "name": ROOM_SIGNAL_EVENT if room_event else SIGNAL_EVENT}


def source_id(cleaned: dict) -> str:
    """The idempotency key of one wake: the same buffer posted twice wakes once."""
    d = cleaned["data"]
    digest = hashlib.sha1(json.dumps([d["reason"], [x["segment_id"] for x in d["buffer"]]],
                                     sort_keys=True).encode()).hexdigest()[:20]
    where = d.get("meeting_row") or f"room:{d.get('session')}"
    return f"signal:{where}:{digest}"


def _internal() -> dict:
    return door_headers()


def register(reg) -> None:
    @reg.step(needs=("agent",))
    def wake_meeting_chat(ctx: StepCtx):
        """Wake the meeting's agent in the meeting's chat with what was just said: ask agent-api
        which chat the meeting wakes and whose it is (the call's attachment), then post
        `meeting.signal` as that person with that chat as `target.chat` and the plan; the buffer
        rides as data, never as instructions. Result: {chat, queued}."""
        if ctx.scratch.get("woken"):
            return Done(ctx.scratch["woken"])
        uid = str(ctx.refs.get("uid") or "")
        data = ctx.refs.get("signal")
        row = str((data or {}).get("meeting_row") or "")
        room = str((data or {}).get("session") or "")
        if not uid or not isinstance(data, dict) or not (_ROW.match(row) or _ROOM.match(room)):
            raise StepError("a signal carried no uid or buffer", retryable=False)
        if room and not row:
            # A ROOM'S SENSOR wakes the room itself: the room is the chat
            return _wake(ctx, uid, ROOM_SIGNAL_EVENT, room, data, where={"session": room})
        code, info = http("GET", f"{agent_door()}/internal/meetings/{row}/listen?subject="
                                 f"{urllib.parse.quote(uid)}", _internal())
        code = int(code or 0)
        if code == 404:
            return Done({"skipped": "meeting_not_found", "meeting_row": row})
        if not 200 <= code < 300 or not isinstance(info, dict) or not info.get("chat_session"):
            raise StepError(f"agent-api answered {code} to the meeting's brief read — {str(info)[:200]}",
                            retryable=code == 429 or code >= 500 or code == 0)
        return _wake(ctx, str(info.get("chat_subject") or uid), SIGNAL_EVENT, str(info["chat_session"]),
                     data, where={"meeting_row": row})


def _wake(ctx: StepCtx, uid: str, name: str, chat: str, data: dict, *, where: dict):
    """Post the wake to agent-api's event door: ``name`` with ``target.chat``, the plan, the buffer."""
    template = (ctx.flow.param("plan") if ctx.flow is not None else None) or (
        DEFAULT_ROOM_PLAN if name == ROOM_SIGNAL_EVENT else DEFAULT_PLAN)
    try:
        plan = str(template).format(score=f"{float(data.get('score') or 0):.2f}")
    except (KeyError, IndexError, ValueError):
        plan = str(template)
    event = {"name": name, "subject": uid, "target": {"chat": chat},
             "plan": {"prompt": plan}, "data": data}
    code, out = http("POST", f"{agent_door()}/events", _internal(), event)
    code = int(code or 0)
    if code in (403, 404, 409, 429):
        # Signals off, the cap reached, a meeting or chat the person cannot reach: nothing to
        # wake, said on the run rather than retried.
        reason = ((out or {}).get("detail") or {}).get("reason") if isinstance(out, dict) else ""
        return Done({"skipped": reason or f"http_{code}", "chat": chat, **where})
    if not 200 <= code < 300:
        raise StepError(f"agent-api answered {code} to a signal — {str(out)[:200]}",
                        retryable=code >= 500 or code == 0)
    woken = {"chat": chat, **where,
             "queued": bool((out or {}).get("queued")) if isinstance(out, dict) else False}
    ctx.scratch["woken"] = woken
    return Done(woken, provider_ref=chat)
