"""THE FIXED NUDGE BODIES (PRD 0.13.4 M4, §16; D38 floor ``nudge_body = template_only``; deny test 26).

A nudge that leaves the app says WHO, the TITLE the recipient may read, and ONE link: "Open to
decide". Everything else — the card's fields, a room's messages, anything personal of anyone —
stays behind the sign-in. This is a floor, not a tuning line: no workspace or person can lower it.

HOW THE FLOOR IS HELD. ``Fields`` is the whole vocabulary a template can see: kind, title, who,
room, workspace, link. There is no parameter that carries free text, so a caller cannot put a
message into a nudge by passing one. ``fields_from`` goes further and REFUSES a mapping that tries
(``body``, ``text``, ``message``, …): an item that arrives carrying content is a producer bug, and
silently dropping the content would hide the bug while the next producer copies it.

Each field is cleaned before it is rendered: one line, control characters removed, any URL replaced
by "[link]" (a title is not a place to smuggle a second link — the one link is ours), and capped.
The link is always a path in this deployment's own terminal (``VEXA_UI_URL``), never another origin.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

#: Keys that would carry content into a nudge. Refused outright (deny test 26).
FREE_TEXT_KEYS = frozenset({"body", "text", "message", "content", "snippet", "preview",
                            "description", "summary", "excerpt", "transcript", "fields", "note"})
#: Keys a template may read. Anything else on an item is ignored.
ALLOWED = ("kind", "title", "who", "room", "workspace", "link")

TITLE_MAX, NAME_MAX = 140, 80
_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_CTRL = re.compile(r"[\x00-\x1f\x7f​-‏  ‪-‮]")
_PATH = re.compile(r"^/[A-Za-z0-9/_\-.~%=&?:+@]*$")

FOOT_SINGLE = "You get at most {budget} of these a day. Change it in Settings → Notifications."
FOOT_DIGEST = ("Only what is still unseen. Grouped by workspace. "
               "Change how often in Settings → Notifications.")
CTA = "Open to decide"


class FreeTextRefused(ValueError):
    """An item tried to carry content into a nudge (the D38 floor, ``nudge.free_text``)."""


def clean(value, cap: int) -> str:
    s = _CTRL.sub(" ", str(value or ""))
    s = _URL.sub("[link]", s)
    s = " ".join(s.split())
    return s if len(s) <= cap else s[: cap - 1].rstrip() + "…"


@dataclass(frozen=True)
class Fields:
    kind: str
    title: str = ""
    who: str = ""
    room: str = ""
    workspace: str = ""
    link: str = ""


def fields_from(item: dict) -> Fields:
    """The template's view of an item. Refuses content keys; ignores everything else unknown."""
    bad = sorted(k for k in (item or {}) if k in FREE_TEXT_KEYS)
    if bad:
        raise FreeTextRefused(f"a nudge carries template fields only; refused: {', '.join(bad)}")
    g = (item or {}).get
    return Fields(kind=str(g("kind") or "card"), title=clean(g("title"), TITLE_MAX),
                  who=clean(g("who"), NAME_MAX), room=clean(g("room"), NAME_MAX),
                  workspace=clean(g("workspace"), NAME_MAX), link=str(g("link") or ""))


def url_for(link: str, ui: str) -> str:
    """The absolute "Open to decide" URL: a path in this deployment's terminal, never elsewhere."""
    path = link if isinstance(link, str) and _PATH.match(link or "") and not link.startswith("//") else "/"
    base = (ui or "").rstrip("/")
    if not base:
        raise ValueError("VEXA_UI_URL is unset — a nudge without a link to open is not sent")
    return base + path


def card_link(card_id: str) -> str:
    return "/decide/" + quote(str(card_id), safe="")


def line(f: Fields) -> str:
    """The one sentence an item is, per kind. Names and a title — never a message."""
    who = f.who or "Someone"
    room = f.room or "a room"
    title = f"“{f.title}”" if f.title else "a card"
    return {
        "card": f"{who} is waiting on you: {title}",
        "slip": f"{title} is past its date — {who} is waiting on it",
        "mention": f"{who} mentioned you in {room}",
        "call": f"Missed call from {who} in {room}",
        "join_request": f"{who} asks to join {room}",
    }.get(f.kind, f"{who} is waiting on you")


def subject(f: Fields) -> str:
    return {
        "card": f"Waiting on you: {f.title or 'a card'}",
        "slip": f"Slipped: {f.title or 'a card'}",
        "mention": f"{f.who or 'Someone'} mentioned you",
        "call": f"Missed call in {f.room or 'a room'}",
        "join_request": f"{f.who or 'Someone'} asks to join {f.room or 'a room'}",
    }.get(f.kind, "Waiting on you")[:160]


def render_single(item: dict, *, ui: str, budget: int) -> tuple[str, str, str]:
    """``(subject, body, url)`` for the one email an item may get after the idle window."""
    f = fields_from(item)
    url = url_for(f.link, ui)
    where = f"\n{f.workspace}" if f.workspace else ""
    body = f"{line(f)}{where}\n\n{CTA}: {url}\n\n{FOOT_SINGLE.format(budget=int(budget))}\n"
    return subject(f), body, url


def render_digest(items: list[dict], *, ui: str) -> tuple[str, str]:
    """``(subject, body)`` for one batched digest: what is still unseen, grouped by workspace."""
    fs = [fields_from(i) for i in items]
    n = len(fs)
    groups: dict[str, list[Fields]] = {}
    for f in fs:
        groups.setdefault(f.workspace or "Your Vexa", []).append(f)
    parts = [f"{n} {'thing waits' if n == 1 else 'things wait'} on you", ""]
    for ws, rows in groups.items():
        parts.append(ws)
        for f in rows:
            parts.append(f"- {line(f)} — {CTA}: {url_for(f.link, ui)}")
        parts.append("")
    parts.append(FOOT_DIGEST)
    subj = f"{n} {'thing waits' if n == 1 else 'things wait'} on you"
    return subj, "\n".join(parts) + "\n"
