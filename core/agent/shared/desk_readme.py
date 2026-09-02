"""desk_readme.py — the desk README's generated sections: a VIEW over `kg/`, never a second store.

PRD decision 26.4 (founder, 2026-09-02): *"let's make a default right sidebar page the personal
desk readme and we will make the agent treat this as the actual desk. This surface should bring
everything important to the surface."*

The README is the first page the panel opens, so it is the one document a person reads without
asking for it. That makes it the right place for "what is on this desk" — and the WRONG place for
anything that is only there. So every generated section is **derived from `kg/` on every run** and
lives between markers:

    <!-- desk:people:start -->  … regenerated …  <!-- desk:people:end -->

**Nothing outside the markers is ever touched.** That rule is the whole safety argument: the top of
a README is what the agent wrote for this person when it had something to say, and what the person
themselves edited. A generator that rewrote the file would destroy both, silently, on a schedule —
the same failure class as `gh pr edit --body-file` over a human attestation. On a README that has
no markers yet the blocks are APPENDED, below whatever is already there; the header stays theirs.

A missing section is written as an honest empty line ("No people on this desk yet"), never omitted:
an absent section reads as *not looked at*, and the reader cannot tell that from *nothing there*.

THE DERIVATIONS ARE PROXIES AND THEY SAY SO. People/companies/meetings are a directory listing —
exact. Open commitments and next dates are pattern reads over what the agent wrote (the bullets
under a "Committed"/"Open items" heading; ISO dates in frontmatter and in those bullets). They will
miss a commitment phrased some other way. That is stated in the section itself rather than hidden,
because the alternative — a model call per README refresh — is a cost on every turn for a list.
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Iterable, Optional

from shared.entities import ENTITIES_DIR, split_frontmatter
from shared.links import format_ref

README = "README.md"

# key → heading. The order here is the order on the page: who and what first (a person scans for a
# name), then what is owed and when, then the rooms this desk belongs to.
SECTIONS: tuple[tuple[str, str], ...] = (
    ("people", "People"),
    ("companies", "Companies"),
    ("meetings", "Meetings"),
    ("commitments", "Open commitments"),
    ("dates", "Next dates"),
    ("workspaces", "Workspaces"),
)

_MARKER = "<!-- desk:{key}:{edge} -->"

# Headings whose bullets are commitments. Matched on the heading TEXT, case-insensitively, because
# the note templates in this product spell it three ways already.
_COMMIT_HEADING = re.compile(r"^#{1,6}\s*(committed|commitments|open items?|action items?|next steps?)\b",
                             re.I | re.M)
_HEADING = re.compile(r"^#{1,6}\s", re.M)
_BULLET = re.compile(r"^\s*[-*]\s+(.+?)\s*$", re.M)
_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_SOURCE_SUFFIX = re.compile(r"\s+—\s+source:\s.*$")

# What a section shows before it is longer than a person will read. The README is a surface, not a
# report: past this many rows it stops being "what is on the desk" and becomes a database dump.
MAX_ROWS = 25


def _marker(key: str, edge: str) -> str:
    return _MARKER.format(key=key, edge=edge)


def _title_of(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return path.stem
    fm, _ = split_frontmatter(text)
    for ln in fm:
        k, sep, v = ln.partition(":")
        if sep and k.strip() == "title":
            return v.split("#")[0].strip() or path.stem
    return path.stem


def _pages(root, kind: str) -> list[Path]:
    d = Path(root) / ENTITIES_DIR / kind
    if not d.is_dir():
        return []
    return sorted((f for f in d.glob("*.md") if f.name != "index.md"), key=lambda f: f.stem)


def _entity_list(root, kind: str) -> list[str]:
    return [f"- [[{_title_of(p)}]]" for p in _pages(root, kind)]


def _commitments(root) -> list[str]:
    """Bullets under a commitment-shaped heading, attributed to the page they came from."""
    out: list[str] = []
    base = Path(root) / ENTITIES_DIR
    for p in sorted(base.rglob("*.md")) if base.is_dir() else []:
        if p.name == "index.md":
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _COMMIT_HEADING.finditer(text):
            nxt = _HEADING.search(text, m.end())
            block = text[m.end(): nxt.start() if nxt else len(text)]
            for b in _BULLET.findall(block):
                line = _SOURCE_SUFFIX.sub("", b).strip()
                if line:
                    out.append(f"- {line} — [[{_title_of(p)}]]")
    return out


def _dates(root, today: str) -> list[str]:
    """Every ISO date at or after ``today`` that appears in an entity page, with its line.

    Frontmatter dates and bullet dates alike, because a meeting's date lives in one and a
    commitment's deadline in the other. De-duplicated on (date, page) so a page that repeats its
    own date five times contributes one row."""
    rows: dict[tuple[str, str], str] = {}
    base = Path(root) / ENTITIES_DIR
    for p in sorted(base.rglob("*.md")) if base.is_dir() else []:
        if p.name == "index.md":
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        title = _title_of(p)
        for line in text.splitlines():
            if line.startswith("## ") or line.startswith("created:"):
                continue          # the dated-entry headings and the page's own birthday are history
            for d in _ISO_DATE.findall(line):
                if d >= today:
                    rows.setdefault((d, title), f"- {d} — [[{title}]]")
    return [rows[k] for k in sorted(rows)]


def _workspaces(workspaces: Iterable[dict]) -> list[str]:
    """The person's groups, as ID-links to each group's README (decision 26.4).

    An id-link and not a name: a group gets renamed, and the whole reason this section exists on
    the desk is to be the door that still opens afterwards."""
    out = []
    for w in workspaces or []:
        wid = str((w or {}).get("id") or "").strip()
        if not wid:
            continue
        out.append(f"- {format_ref(wid, README)}")
    return out


def render_sections(root, *, workspaces: Iterable[dict] = (), today: Optional[str] = None) -> dict:
    """``{key: body}`` — the generated body of every section, headings included."""
    day = today or _dt.date.today().isoformat()

    def block(heading: str, rows: list[str], empty: str, note: str = "") -> str:
        head = f"## {heading}\n"
        if note:
            head += f"\n{note}\n"
        if not rows:
            return head + f"\n_{empty}_\n"
        shown, more = rows[:MAX_ROWS], max(0, len(rows) - MAX_ROWS)
        body = head + "\n" + "\n".join(shown) + "\n"
        if more:
            body += f"\n_{more} more — read `kg/` for the rest._\n"
        return body

    return {
        "people": block("People", _entity_list(root, "person"),
                        "No people on this desk yet."),
        "companies": block("Companies", _entity_list(root, "company"),
                           "No companies on this desk yet."),
        "meetings": block("Meetings", _entity_list(root, "meeting"),
                          "No meetings on this desk yet."),
        "commitments": block("Open commitments", _commitments(root),
                             "Nothing recorded as committed yet.",
                             note="_Read from the commitment sections of the pages below — "
                                  "a commitment phrased another way will not be here._"),
        "dates": block("Next dates", _dates(root, day),
                       f"Nothing dated on or after {day}."),
        "workspaces": block("Workspaces", _workspaces(workspaces),
                            "This desk belongs to no group workspace yet."),
    }


def _replace_marked(text: str, key: str, body: str) -> tuple[str, bool]:
    """Replace what is between one section's markers. ``(text, found)``."""
    start, end = _marker(key, "start"), _marker(key, "end")
    i, j = text.find(start), text.find(end)
    if i == -1 or j == -1 or j < i:
        return text, False
    return text[: i + len(start)] + "\n" + body.strip("\n") + "\n" + text[j:], True


def update_readme(root, *, workspaces: Iterable[dict] = (), today: Optional[str] = None) -> dict:
    """Regenerate the desk README's marked sections. Returns ``{path, changed, sections}``.

    Idempotent: a desk whose `kg/` has not moved rewrites byte-identical content and reports
    ``changed: False``, so this is safe to run at the end of every turn."""
    p = Path(root) / README
    try:
        before = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        before = ""
    text = before
    sections = render_sections(root, workspaces=workspaces, today=today)
    appended: list[str] = []
    for key, _heading in SECTIONS:
        text, found = _replace_marked(text, key, sections[key])
        if not found:
            appended.append(f"{_marker(key, 'start')}\n{sections[key].strip()}\n{_marker(key, 'end')}")
    if appended:
        head = text.rstrip("\n")
        if not head:
            # A desk with no README yet gets the one sentence that says what this page IS. It is
            # written ONCE, above the markers, and never regenerated — the moment the agent has
            # something better to say at the top, it says it and this line is out of the way.
            head = ("# This desk\n\nThe view over the files on this desk. The sections below are "
                    "generated from `kg/` — edit the pages, not the lists.")
        text = head + "\n\n" + "\n\n".join(appended) + "\n"
    if text != before:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return {"path": README, "changed": text != before, "sections": [k for k, _ in SECTIONS]}
