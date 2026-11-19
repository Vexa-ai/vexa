"""WHO WROTE A CHANGE (H10): the git trailer lines of a commit an agent made for a person.

``On-behalf-of: <person>``, ``Written-by: agent`` and, when the agent's session is known,
``Agent-unit: <unit>``. A person's own commit carries none. The audit reads them with
``git log --format=%(trailers)``. The values come from a verified identity only — the worker's
dispatch environment, or the identity door's rebuilt delegation headers (``control_plane
.commit_trailers.writer_trailers``) — and are reduced to a plain token alphabet here, so nothing
spelled in a value can open a new trailer line.
"""
from __future__ import annotations

import re
from typing import Iterable

ON_BEHALF_OF = "On-behalf-of"
WRITTEN_BY = "Written-by"
AGENT_UNIT = "Agent-unit"
_TRAILER_VALUE = re.compile(r"[^A-Za-z0-9@._+:-]")


def agent_trailers(subject: str, unit: str = "") -> list[str]:
    """The trailer lines of a commit an agent made for ``subject`` (``unit``: its session, if known)."""
    who = _TRAILER_VALUE.sub("", str(subject or ""))[:128] or "unknown"
    out = [f"{ON_BEHALF_OF}: {who}", f"{WRITTEN_BY}: agent"]
    unit = _TRAILER_VALUE.sub("", str(unit or ""))[:160]
    if unit:
        out.append(f"{AGENT_UNIT}: {unit}")
    return out


def with_trailers(message: str, trailers: Iterable[str]) -> str:
    """``message`` with ``trailers`` as its last paragraph (unchanged when there are none)."""
    lines = [t for t in (trailers or ()) if t]
    return f"{message}\n\n" + "\n".join(lines) if lines else message
