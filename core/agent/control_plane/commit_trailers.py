"""WHO WROTE A CHANGE (H10): the git trailers a commit made by an agent carries.

A commit made for a person by their agent closes its message with trailers that say so:
``On-behalf-of: <person>`` and ``Written-by: agent`` (plus ``Agent-unit: <unit>`` when the identity
names one). The person's own commit carries none. The audit reads them with
``git log --format=%(trailers)``. Nothing a caller sends reaches the trailer block: it is written
here from the verified identity alone — the delegation headers the identity door rebuilt from a
signed claim, the signed credential class (an API key is a program writing for its person), or, for a write the platform makes for an agent on the internal tier, the subject that
tier names.

Every writer that commits a change a caller asked for takes its trailers from here, so one rule
decides attribution everywhere: the page routes, entity cards, assets, the brief, workspace icons
and pins, templates, accepted call proposals, folder imports and meeting pages. A worker turn's own
commits take theirs from ``shared.commit_trailers`` (the same lines, from its dispatch).
"""
from __future__ import annotations

from typing import Mapping

from control_plane import identity_token
from shared.commit_trailers import (AGENT_UNIT, ON_BEHALF_OF, WRITTEN_BY,  # noqa: F401  (the one rule)
                                    agent_trailers, with_trailers)


def writer_trailers(headers: Mapping[str, str]) -> list[str]:
    """The trailer lines a commit made for this caller carries: none for a person's own write.

    A person's own write is one made with their SESSION (the signed-in browser). A worker's
    delegation token is an agent by definition, and so is an API KEY (credential class ``key``): a
    key is what a program holds — a person's own MCP client, a script — never the person at the
    keyboard, so its commit reads ``On-behalf-of: <person>`` and ``Written-by: agent`` too (without
    ``Agent-unit``: a key names no session)."""
    if identity_token.is_delegated(headers):
        return agent_trailers(str(headers.get("x-user-id") or ""), identity_token.session_of(headers))
    if identity_token.credential_class_of(headers) == identity_token.CREDENTIAL_KEY:
        return agent_trailers(str(headers.get("x-user-id") or ""))
    return []


def attributed(message: str, headers: Mapping[str, str]) -> str:
    """``message`` with the writer's trailers as its last paragraph (unchanged for a person)."""
    return with_trailers(message, writer_trailers(headers))
