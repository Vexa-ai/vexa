"""THE TIMELINE AS TEXT, in the person's own zone — pure, stdlib, and therefore testable.

Two readers use exactly this rendering and they must not disagree: the control-MCP `timeline` tool
(a person asking) and the dispatch preamble (the same person's agent being told, unasked). Two
spellings of "half past two, their time" is how a chat ends up saying one thing and the machinery
note another about the same meeting.

Three rules, all of them lessons already paid for elsewhere in this codebase:

  * NEVER a bare `HH:MM`. Times were once rendered on the server's clock and a Lisbon person was
    told their standup joined at 19:15 when it was 17:15 where they stood (`_person_tz`). Every
    stamp here carries its zone.
  * NEVER a bare `HH:MM` for a day that is not today either — "11:23" for something that happened
    on Monday is what makes an agent talk about last week as if it were this morning.
  * `now` FIRST. A timeline without a now is a list; the whole ask in decision 31 was a SENSE of
    now, and "in an hour" has nothing to be relative to without it.
"""
from __future__ import annotations

import datetime
from typing import Optional

UTC = datetime.timezone.utc


def zone(tz: str):
    """The person's `tzinfo`, or UTC. An unknown zone degrades; it never raises."""
    if tz:
        try:
            import zoneinfo
            return zoneinfo.ZoneInfo(tz)
        except Exception:  # noqa: BLE001 — a bad zone is a setting, not a crash
            pass
    return UTC


def clock(epoch: float, tz: str) -> str:
    """`HH:MM ZONE` — the house format, zone always attached."""
    z = zone(tz)
    t = datetime.datetime.fromtimestamp(float(epoch), z)
    return t.strftime("%H:%M ") + (t.tzname() or (tz or "UTC"))


def when(epoch: float, tz: str, today: str) -> str:
    """`HH:MM ZONE` when it is today for THEM, `Wed 03 Sep HH:MM ZONE` when it is not."""
    t = datetime.datetime.fromtimestamp(float(epoch), zone(tz))
    stamp = clock(epoch, tz)
    return stamp if t.strftime("%Y-%m-%d") == today else t.strftime("%a %d %b ") + stamp


def _tail(event: dict) -> str:
    produced = event.get("produced") or {}
    return str(produced.get("link") or produced.get("note_path")
               or produced.get("mail_subject") or "")


def line(event: dict, tz: str, today: str) -> str:
    """One event, one line. A status is shown only when it is NOT the boring one — a line that
    says `done` on every row trains the reader to stop reading the column that matters."""
    bits = [f"{when(event.get('at_epoch') or 0, tz, today):>22}",
            f"{str(event.get('kind') or ''):<18}",
            str(event.get("title") or "")[:48]]
    status = str(event.get("status") or "")
    if status and status not in ("done", "confirmed", "completed"):
        bits.append(f"[{status}]")
    tail = _tail(event)
    if tail:
        bits.append(tail[:100])
    return "  " + "  ".join(b for b in bits if b.strip())


def _split(events, now: float, *, back: int, ahead: int):
    past = [e for e in events if (e.get("at_epoch") or 0) <= now]
    future = [e for e in events if (e.get("at_epoch") or 0) > now]
    return (past[-back:] if back else past), (future[:ahead] if ahead else future)


def render_text(payload: dict, tz: str = "", *, back: int = 0, ahead: int = 0,
                now: Optional[float] = None) -> str:
    """The `timeline` tool's answer: now, then what happened, then what is coming.

    `back`/`ahead` of 0 mean "everything the payload holds" — the tool shows what was asked for.
    The preamble passes 5 and 5 (decision 31 §1: *a handful of lines*).
    """
    events = payload.get("events") or []
    now = float(now if now is not None else (payload.get("now_epoch") or 0))
    z = zone(tz)
    head = datetime.datetime.fromtimestamp(now, z)
    today = head.strftime("%Y-%m-%d")
    past, future = _split(events, now, back=back, ahead=ahead)
    out = [f"now  {head.strftime('%a %d %b')}  {clock(now, tz)}"
           + ("" if tz else "   (no timezone set — say your IANA zone and it will be remembered)")]
    out.append("")
    out.append(f"already happened ({len(past)}):" if past
               else "already happened: nothing in this window")
    out += [line(e, tz, today) for e in past]
    out.append("")
    out.append(f"still ahead ({len(future)}):" if future else "still ahead: nothing scheduled")
    out += [line(e, tz, today) for e in future]
    return "\n".join(out)


def render_preamble(payload: dict, tz: str = "", *, back: int = 5, ahead: int = 5) -> str:
    """The compact block that ships on EVERY dispatch (decision 31 §1) — a heading, `now`, the last
    few events concerning this person and the next few scheduled, and nothing else.

    Returns "" when there is nothing to say — no payload, no now. A preamble that renders a heading
    over an empty list teaches the model that the section is noise, and it will then skip the turn
    where the section is not.
    """
    if not payload or not payload.get("now_epoch"):
        return ""
    events = payload.get("events") or []
    now = float(payload["now_epoch"])
    z = zone(tz)
    head = datetime.datetime.fromtimestamp(now, z)
    today = head.strftime("%Y-%m-%d")
    past, future = _split(events, now, back=back, ahead=ahead)
    out = ["## Where this person is in time\n",
           f"**Now: {head.strftime('%A %d %B %Y')}, {clock(now, tz)}.** "
           "State times in this zone; never a bare clock time from another one. "
           "`timeline(since, until)` reaches further back or further forward than this block.\n"]
    if past:
        out.append("Last events concerning them:")
        out += [line(e, tz, today) for e in past]
        out.append("")
    if future:
        out.append("Next, scheduled:")
        out += [line(e, tz, today) for e in future]
        out.append("")
    if not past and not future:
        out.append("Nothing recorded for them in the last two weeks and nothing scheduled ahead — "
                   "say so if it comes up rather than implying a history you cannot see.\n")
    return "\n".join(out) + "\n"
