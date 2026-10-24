"""workspace_search.py — FIND PAGES in one workspace by the words in them (`workspace_search`).

An agent outside the worker (a person's own MCP client) has no filesystem under the workspace: it
can list the tree and read one page at a time, so finding "the page about the Acme renewal" cost
one read per file. This answers it in one call, from the same reader the tree and the page verbs
use, so it sees exactly the files `workspace_tree` lists and reads them exactly as `workspace_read`
does (templates and unfilled pages carry their banners there; here they are matched as text).

The match is plain and predictable — no index, no ranking model: the query is split into words,
and a page matches when EVERY word occurs in its path or its text, ignoring case. Pages whose path
or title carries the words come first, then by how often the words occur. Bounded on every axis
(files scanned, bytes read per file, results returned), and the answer says when a bound was hit.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

#: Text pages only: what an agent writes and a person reads. Binaries and assets are not searched.
TEXT_SUFFIXES = (".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".csv")
#: The most files one search reads, and the most characters of one file it looks at.
MAX_FILES = 5000
MAX_CHARS = 400_000
#: Characters of context on each side of the first hit in a snippet.
SNIPPET_CONTEXT = 80
MAX_TERMS = 12

_TITLE = re.compile(r"^title:\s*(.+?)\s*$|^#\s+(.+?)\s*$", re.M)


def terms_of(query: str) -> list[str]:
    """The words a page must carry: whitespace-split, lower-cased, de-duplicated, at most MAX_TERMS."""
    out: list[str] = []
    for word in str(query or "").lower().split():
        if word and word not in out:
            out.append(word)
    return out[:MAX_TERMS]


def _title_of(text: str) -> str:
    m = _TITLE.search(text[:4000])
    if not m:
        return ""
    return (m.group(1) or m.group(2) or "").strip().strip("\"'")[:200]


def _snippet(text: str, lower: str, terms: list[str]) -> str:
    at = min((i for i in (lower.find(t) for t in terms) if i >= 0), default=0)
    start, end = max(0, at - SNIPPET_CONTEXT), min(len(text), at + SNIPPET_CONTEXT)
    piece = " ".join(text[start:end].split())
    return ("…" if start else "") + piece + ("…" if end < len(text) else "")


def search(reader: Any, base: Path, query: str, *, limit: int = 20,
           path_prefix: Optional[str] = None) -> dict:
    """Pages under ``base`` carrying every word of ``query``: ``{query, results, scanned, truncated}``.

    ``reader`` is the workspace reader (`tree_at` / `read_at`); ``path_prefix`` narrows the search to
    one folder (``kg/entities/meeting``)."""
    terms = terms_of(query)
    if not terms:
        return {"query": query, "results": [], "scanned": 0, "truncated": False}
    prefix = str(path_prefix or "").strip().strip("/")
    files = [p for p in reader.tree_at(base)
             if p.lower().endswith(TEXT_SUFFIXES) and (not prefix or p == prefix or p.startswith(prefix + "/"))]
    truncated = len(files) > MAX_FILES
    hits: list[tuple[tuple, dict]] = []
    scanned = 0
    for rel in files[:MAX_FILES]:
        try:
            text = reader.read_at(base, rel)
        except (ValueError, OSError, UnicodeDecodeError):
            continue
        if text is None:
            continue
        scanned += 1
        text = text[:MAX_CHARS]
        lower, path_l = text.lower(), rel.lower()
        if not all(t in lower or t in path_l for t in terms):
            continue
        title = _title_of(text)
        named = sum(1 for t in terms if t in path_l or t in title.lower())
        count = sum(lower.count(t) for t in terms)
        hits.append(((-named, -count, rel), {
            "path": rel, "title": title, "snippet": _snippet(text, lower, terms), "matches": count}))
    hits.sort(key=lambda h: h[0])
    limit = max(1, min(int(limit or 20), 100))
    return {"query": query, "results": [h[1] for h in hits[:limit]], "scanned": scanned,
            "truncated": truncated or len(hits) > limit}
