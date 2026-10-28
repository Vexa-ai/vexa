"""entity_summary.py — the short story an entity's hover card tells (founder, live 2026-10-15: "this
should tell the full story, short one").

A page's frontmatter ``summary``: up to three sentences, ≤280 characters — who they are, how they
relate to the reader, and the latest open thread ("Founder of AI Tinkerers HQ; approves chapter
budgets. You run the Lisbon chapter with him. Waiting on his answer about the invoice customer since
Oct 13."). The agent writes it whenever it updates the page; a page without one is given one once,
by the cheap model, from the page itself. Pure apart from the model call it is handed.
"""
from __future__ import annotations

import json
import re
from typing import Callable, Optional

MAX_CHARS = 280
MIN_GAP_S = 2.0

SYSTEM = (
    "You write the short story a hover card shows about one person, company or project, from its "
    "page. Reply with ONE JSON object and nothing else: {\"summary\": \"...\"}. At most three short "
    "sentences and 280 characters: who or what it is; how it relates to the reader (the page's "
    "owner); the latest open thread, with its date if the page gives one. Plain words, in the page's "
    "language, no markdown, no ids, no file paths; only what the page says."
)

_FM = re.compile(r"^---\n([\s\S]*?)\n---\n?")


def read_summary(md: str) -> str:
    m = _FM.match(str(md or ""))
    if not m:
        return ""
    s = re.search(r"^summary:\s*(.+)$", m.group(1), re.M)
    if not s:
        return ""
    v = s.group(1).strip()
    if v.startswith('"') and v.endswith('"'):
        try:
            v = json.loads(v)
        except ValueError:
            v = v.strip('"')
    return clip(v)


def clip(text) -> str:
    """At most ``MAX_CHARS``, ending on a sentence — never mid-line."""
    t = re.sub(r"\s+", " ", re.sub(r"[*_`#>]|\[\[|\]\]", "", str(text or ""))).strip()
    if len(t) <= MAX_CHARS:
        return t
    cut = t[:MAX_CHARS]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: end + 1] if end > 60 else cut[: cut.rfind(" ")].rstrip(",;:") + "…"


def with_summary(md: str, summary: str) -> str:
    """The page with ``summary:`` in its frontmatter (added, or the empty one filled)."""
    line = f"summary: {json.dumps(summary, ensure_ascii=False)}"
    md = str(md or "")
    m = _FM.match(md)
    if not m:
        return f"---\n{line}\n---\n{md}"
    body = m.group(1)
    body = re.sub(r"^summary:.*$", line, body, flags=re.M) if re.search(r"^summary:", body, re.M) else f"{body}\n{line}"
    return f"---\n{body}\n---\n" + md[m.end():]


def generate(md: str, complete: Optional[Callable[[str, str], Optional[dict]]]) -> str:
    """One story from the page, or "" (no model, or nothing usable came back)."""
    if complete is None:
        return ""
    try:
        out = complete(SYSTEM, str(md or "")[:6000])
    except Exception:  # noqa: BLE001 — a card is furniture
        return ""
    story = clip((out or {}).get("summary"))
    if not story or re.search(r"\b(?:pchat|meet|call|scaffold)-[\w-]{4,}\b|/[\w-]+\.md\b", story):
        return ""
    return story
