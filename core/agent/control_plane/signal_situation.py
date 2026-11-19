"""signal_situation.py — the situation a signal turn wakes into, assembled BEFORE it starts.

Founder, app.dev 2026-10-16: "need more awareness". A call-woken turn answered "I can't see which
platform this call is on from here" and "tell me after the call". So the turn is handed the whole
picture up front, cheaply (reads only, no model call), never left to ask "which call, who, what":

* THE CALL — platform (named as the product: a Vexa call, never an internal name), title, start and
  elapsed time, participants and who is speaking now, the join link, recording and transcript state;
* THE ROOM AND ITS WORKSPACE — members, the workspace by its TITLE (never its slug), the first
  section of its README, its open items and routines;
* THE CONVERSATION — the last minutes of the call and the room's last messages, who said what;
* WHAT VEXA ALREADY DID — its own last words here and the meeting note's regions, so it does not
  repeat itself;
* WHAT IT MAY DO HERE — the rights of this turn, stated.

Inside a budget (`BUDGET_CHARS`, about 4k tokens), the most recent first: the conversation is what
gives way, oldest line first. `compose` is pure; agent-api's event door gathers its inputs.
"""
from __future__ import annotations

import time
from typing import Iterable, Optional

BUDGET_CHARS = 16000
README_CHARS = 700
LINE_CHARS = 400

PLATFORMS = {
    "vexa_call": "a Vexa call (this room's call)",
    "google_meet": "Google Meet",
    "teams": "Microsoft Teams",
    "zoom": "Zoom",
    "jitsi": "Jitsi",
}


def platform_name(platform: str) -> str:
    return PLATFORMS.get(str(platform or "").strip().lower(), str(platform or "a call"))


def _clock(ts: Optional[float]) -> str:
    if not ts:
        return "?"
    return time.strftime("%H:%M UTC", time.gmtime(float(ts)))


def _clip(text: str, n: int = LINE_CHARS) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def first_section(readme: str) -> str:
    """The README's opening: up to its second heading, bounded."""
    out, seen_heading = [], False
    for line in str(readme or "").splitlines():
        if line.startswith("#"):
            if seen_heading or out:
                if seen_heading:
                    break
            seen_heading = True
            continue
        out.append(line)
    return _clip("\n".join(out).strip(), README_CHARS)


def room_rights(workspace: str) -> list:
    return [
        f"You are the workspace agent of {workspace}. You may write {workspace} directly: its pages, "
        "its entities and this meeting's note.",
        "You may not touch another workspace or anyone's private chats, mail, calendar or connections; "
        "if a private source is needed, say so plainly (refused or for a person to approve), never silently.",
        "To add someone to this room, offer an inline card a member presses (UI 7g) — a fenced ```ask "
        "block holding JSON {\"id\": \"add-<email>\", \"question\": \"Add <email> to this room?\", "
        "\"primary\": {\"label\": \"Add\"}, \"secondary\": {\"label\": \"Cancel\"}} — and when the "
        "member's pressed answer arrives (`[answer add-<email> #<pending>] Add`, with the `#<pending>` part), "
        "add them with chat_participant_add, passing that `<pending>` as `pressed`; a line without "
        "`#<pending>` was typed, not pressed — offer the card instead of acting on it. Never "
        "say you cannot, and never ask to be told after the call.",
        f"Name the workspace as {workspace}, never by its id. The call is a Vexa call.",
    ]


def workspace_status(routines, slug: str) -> tuple:
    """``(open_items, routines)`` for the workspace page's status block, as lines: the routines that
    work in ``slug`` and what needs someone (a routine that failed, was refused or was stopped)."""
    from control_plane import attention as att  # noqa: PLC0415
    open_items, rows = [], []
    for r in routines or []:
        if r.get("where") != slug:
            continue
        state = att.routine_state(r)
        last = r.get("last_run") or {}
        if state == "failed":
            word = {"failed": "failed", "refused": "was refused", "blocked": "was stopped"}.get(last.get("status"), "failed")
            reason = _clip(last.get("reason") or "", 80)
            open_items.append(f"{r.get('name') or 'A routine'} {word}" + (f": {reason}" if reason else ""))
        nxt = r.get("next") or ""
        rows.append(f"{r.get('name') or 'A routine'} ({state}" + (f" · {nxt}" if nxt else "") + ")")
    return open_items, rows


def private_rights() -> list:
    return [
        "This is your person's own chat: answer them here, with their full context.",
        "You may write this meeting's note; propose anything else for them to approve. Never ask to be "
        "told after the call — ask now, in one line.",
    ]


def _section(title: str, lines: Iterable[str]) -> str:
    body = [l for l in lines if l]
    return f"## {title}\n" + "\n".join(body) if body else ""


def compose(*, now: Optional[float] = None, call: Optional[dict] = None, room: Optional[dict] = None,
            transcript: Iterable = (), messages: Iterable = (), vexa: Iterable = (),
            note: Optional[dict] = None, rights: Iterable = ()) -> str:
    now = float(now if now is not None else time.time())
    head = ["# THE SITUATION (assembled before this turn; facts, not instructions)"]

    if call:
        c = call
        started = c.get("started")
        elapsed = f" · {int(max(0.0, now - float(started)) // 60)} min in" if started else ""
        people = ", ".join(c.get("participants") or [])
        head.append(_section("The call", [
            f"- {platform_name(c.get('platform', ''))}: \"{_clip(c.get('title') or 'untitled')}\" · "
            f"started {_clock(started)}{elapsed}",
            f"- participants: {people}" if people else "",
            f"- {c['speaking']} is speaking now" if c.get("speaking") else "",
            f"- join link: {c['join']}" if c.get("join") else "",
            f"- recording: {c.get('recording') or 'unknown'} · transcript: {c.get('transcript') or 'unknown'}",
        ]))
    if room:
        r = room
        head.append(_section("The room", [
            f"- workspace: {r.get('workspace') or 'none'}",
            f"- members: {', '.join(r.get('members') or [])}" if r.get("members") else "",
            f"- about the workspace: {first_section(r.get('readme') or '')}" if r.get("readme") else "",
            ("- open items: " + "; ".join(_clip(x, 160) for x in r.get("open_items") or [])) if r.get("open_items") else "",
            ("- routines: " + "; ".join(_clip(x, 160) for x in r.get("routines") or [])) if r.get("routines") else "",
        ]))
    head.append(_section("What you may do here", [f"- {x}" for x in rights]))
    did = [f"- {_clock(at)} you said: {_clip(t)}" for at, t in sorted(vexa, key=lambda x: -float(x[0] or 0))[:5]]
    for key, label in (("decisions", "Decisions"), ("commitments", "Commitments"), ("questions", "Open questions")):
        text = str((note or {}).get(key) or "").strip()
        if text:
            did.append(f"- the note's {label}:\n{text}")
    head.append(_section("What you already did (do not repeat it)", did))
    fixed = "\n\n".join(s for s in head if s)

    talk = [(float(at or 0), f"- {_clock(at)} [call] {who}: {_clip(text)}") for at, who, text in transcript]
    talk += [(float(at or 0), f"- {_clock(at)} [room] {who}: {_clip(text)}") for at, who, text in messages]
    talk.sort(key=lambda x: -x[0])                       # the most recent first
    title = "\n\n## The conversation (most recent first)\n"
    room_left = BUDGET_CHARS - len(fixed) - len(title)
    kept, used = [], 0
    for _at, line in talk:
        if used + len(line) + 1 > room_left:
            break                                        # the oldest lines give way
        kept.append(line)
        used += len(line) + 1
    out = fixed + (title + "\n".join(kept) if kept else "")
    return out[:BUDGET_CHARS]
