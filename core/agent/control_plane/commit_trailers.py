"""WHO WROTE A CHANGE (H10): the git trailers a commit made by an agent carries.

A commit made for a person by their agent closes its message with trailers that say so:
``On-behalf-of: <person>`` and ``Written-by: agent`` (plus ``Agent-unit: <unit>`` when the identity
names one). The person's own commit carries none. The audit reads them with
``git log --format=%(trailers)``. Nothing a caller sends reaches the trailer block: it is written
here from the verified identity alone — the delegation headers the identity door rebuilt from a
signed claim, or, for a write the platform makes for an agent on the internal tier, the subject that
tier names.

Every writer that commits a change a caller asked for takes its trailers from here, so one rule
decides attribution everywhere: the page routes, entity cards, assets, the brief, workspace icons
and pins, accepted call proposals, folder imports and a room's meeting note.
"""
from __future__ import annotations

import re
from typing import Mapping

from control_plane import identity_token

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


def writer_trailers(headers: Mapping[str, str]) -> list[str]:
    """The trailer lines a commit made for this caller carries: none for a person's own write."""
    if not identity_token.is_delegated(headers):
        return []
    return agent_trailers(str(headers.get("x-user-id") or ""), identity_token.session_of(headers))


def with_trailers(message: str, trailers) -> str:
    """``message`` with ``trailers`` as its last paragraph (unchanged when there are none)."""
    lines = [t for t in (trailers or ()) if t]
    return f"{message}\n\n" + "\n".join(lines) if lines else message


def attributed(message: str, headers: Mapping[str, str]) -> str:
    """``message`` with the writer's trailers as its last paragraph (unchanged for a person)."""
    return with_trailers(message, writer_trailers(headers))
