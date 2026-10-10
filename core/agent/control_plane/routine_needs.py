"""routine_needs.py — does a routine ask for something only a person in chat can do?

A routine runs on a clock with nobody in the loop (``unit.v1`` trigger ``scheduled``, delegation
regime ``autonomous``), and every verb that reads the person's mail or calendar, or spends one of
their connections, refuses that regime with gateway-identity.v1 ``REFUSAL``
(``human_session_required``, remedy ``ask_in_chat``). A routine whose whole job is "check my email"
therefore cannot do its job on any run, and the person only finds out after it has run and been
refused, if at all.

This module answers the question at save time, so the Routines surface and the create route can say
so beside the routine. It never refuses a save: the person may still want the routine (the rest of
its work may not need a person), and the refusal policy itself is unchanged.

PURE and CONSERVATIVE. It reads the routine's prompt and nothing else, and it flags a routine only
when the prompt says mail or calendar in so many words, or names one of the tools whose route needs
a person (read from ``routes.v1.json`` ``verbs[].person`` joined to ``mcp.tools.v1.json`` routes, so
a new person-only Connections tool is recognised without editing this file). A routine it misses is
refused at run time exactly as before; a routine it flags wrongly carries one sentence it did not
need.
"""
from __future__ import annotations

import functools
import json
import re
from pathlib import Path

#: `core/agent/` — both manifests sit there, beside `routes.v1.json` (`route_policy.MANIFEST_PATH`).
_AGENT_DIR = Path(__file__).resolve().parents[1]

#: What a routine may need, in the order the warning names them.
NEEDS = ("mail", "calendar", "connections")

#: The words that mean a routine reads mail or the calendar. Whole words only, case-insensitive.
#: "invite" alone is NOT here: a workspace invite is not a calendar invite.
_WORDS = {
    "mail": re.compile(r"\b(?:e-?mails?|mail|mailbox|inbox|gmail|outlook)\b", re.I),
    "calendar": re.compile(r"\b(?:calendars?|gcal|(?:meeting|calendar) invit(?:e|es|ations?))\b", re.I),
}

#: The sentence the card carries. One sentence, the same for every routine; only the needs vary.
WARNING = ("This routine needs your {needs}. A routine runs on its schedule without you present, so "
           "each run will be refused when it reaches for {them}. Ask in chat instead when you want "
           "it done; nothing is wrong with your connection.")


def _category_for_route(path: str) -> str:
    if "/connections/gmail/" in path:
        return "mail"
    if "/connections/calendar/" in path:
        return "calendar"
    return "connections"


@functools.lru_cache(maxsize=1)
def person_tools() -> dict[str, str]:
    """``{tool name: need}`` for every product tool whose route needs a person and lives under
    Connections. Empty (never raises) when a manifest cannot be read: the keyword half still works."""
    try:
        routes = json.loads((_AGENT_DIR / "routes.v1.json").read_text())
        tools = json.loads((_AGENT_DIR / "mcp.tools.v1.json").read_text())
    except (OSError, ValueError):
        return {}
    person = {(str(v.get("method") or "").upper(), str(v.get("path") or ""))
              for v in routes.get("verbs") or [] if v.get("person")}
    out: dict[str, str] = {}
    for t in tools.get("tools") or []:
        route = t.get("route") or {}
        path = str(route.get("path") or "")
        if not path.startswith("/api/connections/"):
            continue
        public = "/agent/" + path[len("/api/"):]
        if (str(route.get("method") or "").upper(), public) in person:
            out[str(t.get("name"))] = _category_for_route(path)
    return out


def needs_person(prompt: str) -> list[str]:
    """What this routine's prompt asks for that only works when the person asks in chat, in
    :data:`NEEDS` order; ``[]`` when nothing. Pure: the prompt in, a list out."""
    text = prompt or ""
    found: set[str] = {need for need, rx in _WORDS.items() if rx.search(text)}
    for tool, need in person_tools().items():
        if re.search(rf"\b{re.escape(tool)}\b", text):
            found.add(need)
    return [n for n in NEEDS if n in found]


def warning_for(needs: list[str]) -> str:
    """The card's sentence for ``needs``, or ``""``."""
    if not needs:
        return ""
    words = {"mail": "mail", "calendar": "calendar", "connections": "connected accounts"}
    named = [words[n] for n in needs]
    joined = named[0] if len(named) == 1 else ", ".join(named[:-1]) + " and " + named[-1]
    return WARNING.format(needs=joined, them="it" if len(named) == 1 else "them")


def annotate(card: dict, prompt: str) -> dict:
    """``card`` with ``needs_person`` (always, possibly empty) and ``warning`` (only when non-empty)
    set from ``prompt``. Returns the same dict for chaining."""
    needs = needs_person(prompt)
    card["needs_person"] = needs
    if needs:
        card["warning"] = warning_for(needs)
    else:
        card.pop("warning", None)
    return card
