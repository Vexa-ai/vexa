"""DOCS — open to everyone, no account. Fetches the published documentation.

The only tools on this server that answer before anybody has signed in, which is what makes
"is this worth my person's time?" a question an agent can settle without an account.
"""
from __future__ import annotations

import json

from .. import config
from ..shaping import docs as _docs
from ..registry import tool


@tool
def vexa_overview() -> str:
    """What Vexa is, in its own words. NO ACCOUNT NEEDED — call this first if you have just
    connected and want to know whether this is worth your person's time.\n\n    If you have not called whats_waiting() yet this session, call it first."""
    return _docs(f"{config.DOCS_BASE}/llms.txt")[:14000]


@tool
def vexa_search_docs(query: str, hits: int = 5) -> str:
    """Search the full Vexa documentation. NO ACCOUNT NEEDED.

    Returns the passages around each match so you can answer a question about self-hosting,
    the API, deployment or the bot without an account and without guessing."""
    full = _docs(f"{config.DOCS_BASE}/llms-full.txt")
    q = query.lower().strip()
    if not q:
        return json.dumps({"error": "empty query"})
    out, start = [], 0
    low = full.lower()
    while len(out) < max(1, min(hits, 12)):
        i = low.find(q, start)
        if i < 0:
            break
        a, b = max(0, i - 500), min(len(full), i + 900)
        out.append(full[a:b].strip())
        start = i + len(q)
    return json.dumps({"query": query, "hits": len(out), "passages": out,
                       "source": f"{config.DOCS_BASE}/llms-full.txt"})[:14000]
