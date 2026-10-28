"""The rule tier: wake at once, with no model call.

Two kinds of rule, both checked before any line is scored:

* **fixed** — being addressed always wakes the agent, and the agent cannot remove it. What counts as
  addressed depends on the source (a chat: ``@vexa``, the agent's name, a reply to the agent, a
  question right after an agent message), so the source marks those lines itself.
* **the agent's own** — the brief's ``rules``: ``{kind: keyword|person|regex, value, wake: true}``.
  ``keyword`` is a case-insensitive whole-word or phrase match, ``person`` matches the speaker's name
  or id exactly (case-insensitive), ``regex`` is a Python pattern of at most 200 characters.

A regex runs against at most 2000 characters, and a pattern with a quantified group that itself holds
a quantifier (``(a+)+``, the shape of catastrophic backtracking) is refused when the brief is read.
Each match also runs off the event loop with a 50 ms deadline; a pattern that overruns is switched
off for the rest of the process and named in the log.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

log = logging.getLogger("signals.rules")

KINDS = ("keyword", "person", "regex")
MAX_RULES = 20
MAX_VALUE = 200
REGEX_TEXT_MAX = 2000
REGEX_DEADLINE_S = 0.05
_NESTED = re.compile(r"\((?:[^()\\]|\\.)*[+*}](?:[^()\\]|\\.)*\)\s*[+*{]")
_disabled: set = set()


@dataclass(frozen=True)
class Rule:
    kind: str
    value: str
    wake: bool = True


def validate(raw) -> tuple:
    """The brief's ``rules`` → a tuple of valid ``Rule``s; anything else is dropped."""
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or item.get("kind") not in KINDS:
            continue
        value = str(item.get("value") or "").strip()
        if not value or len(value) > MAX_VALUE:
            continue
        if item["kind"] == "regex":
            if _NESTED.search(value):
                log.warning("rules: a regex with a nested quantifier is refused")
                continue
            try:
                re.compile(value)
            except re.error:
                continue
        out.append(Rule(item["kind"], value, bool(item.get("wake", True))))
        if len(out) >= MAX_RULES:
            break
    return tuple(out)


def _keyword(value: str, text: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", text, re.IGNORECASE) is not None


async def matches(rules: tuple, *, text: str, speaker: str, speaker_id: str = "") -> bool:
    """True when any waking rule matches this line."""
    for r in rules:
        if not r.wake:
            continue
        if r.kind == "keyword" and _keyword(r.value, text):
            return True
        if r.kind == "person" and r.value.casefold() in (speaker.casefold(), str(speaker_id).casefold()):
            return True
        if r.kind == "regex" and r.value not in _disabled:
            pattern = re.compile(r.value)
            try:
                hit = await asyncio.wait_for(asyncio.to_thread(pattern.search, text[:REGEX_TEXT_MAX]),
                                             REGEX_DEADLINE_S)
            except asyncio.TimeoutError:
                _disabled.add(r.value)
                log.warning("rules: a regex overran %s s and is switched off", REGEX_DEADLINE_S)
                continue
            if hit:
                return True
    return False
