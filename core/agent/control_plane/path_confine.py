"""path_confine.py — ``base/<name>``, or a refusal: the containment check, spelled once.

The modules that build a workspace path from a caller's name (a subject, a slug, a workspace id, an
invite or a link id) each validate that name — one segment, no leading dot — and most then compared
``Path.resolve()`` against ``.parents``. That holds, but it is a check a reader (or a scanner) has to
trace through each module to believe. This is the same check written the canonical way: resolve
links with ``os.path.realpath``, then require the result to start with the resolved base plus a
separator. The path returned is the resolved one, so whatever the caller does next is done to the
path that was checked, never to the name it was built from.
"""
from __future__ import annotations

import os
from pathlib import Path


def within(base, name: str, error: Exception) -> Path:
    """``base/<name>`` with every link resolved, strictly INSIDE ``base`` — else ``raise error``.

    ``name`` may be several segments (``"a/b.json"``); an absolute ``name``, a ``..`` that climbs out,
    a link that points out, and ``base`` itself all raise. ``base`` need not exist."""
    root = os.path.realpath(base)
    path = os.path.realpath(os.path.join(root, name))
    if not path.startswith(root.rstrip(os.sep) + os.sep):
        raise error
    return Path(path)

