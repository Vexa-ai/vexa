"""The tool registry — how a domain module says "this function is a tool".

The rig decorated every function with ``@mcp.tool()`` at import time, which meant the server object
had to exist before any tool could be defined, which is why all 64 lived in one file. Here a domain
module decorates with :func:`tool`, and :func:`vexa_mcp.server.build` registers whatever the
domains collected. The domains never import the server, so they can be imported (and AST-walked, and
unit-tested) with no transport, no network and no configuration.
"""
from __future__ import annotations

TOOLS: list = []
PROMPTS: list = []


def tool(fn):
    """Mark a module-level function as an MCP tool. Registration order is definition order."""
    TOOLS.append(fn)
    return fn


def prompt(*, name: str, title: str, description: str):
    """Mark a function as an MCP prompt. Prompts are the only thing a server can put in front of a
    person without being asked, so they carry the onboarding."""
    def deco(fn):
        PROMPTS.append((name, title, description, fn))
        return fn
    return deco


def load_all() -> None:
    """Import every domain module, which is what fills :data:`TOOLS`."""
    from .tools import (  # noqa: F401
        docs, flows, friction, identity, meetings, panel, rehearse, workspaces,
    )
