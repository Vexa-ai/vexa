"""Build the MCP server: one instruction string, 64 tools, three prompts, one transport.

The rig built this implicitly — ``mcp = MCPServer(...)`` at module scope, and every tool decorated
with ``@mcp.tool()`` at import time, which is why all 64 had to live in one file. Here the domains
collect themselves into :mod:`vexa_mcp.registry` with no server in sight, and this module is the
only place that knows what a transport is.
"""
from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer

from . import config, prompts, registry  # noqa: F401 — importing prompts registers them
from .instructions import INSTRUCTIONS


def build() -> MCPServer:
    """The server, with every domain's tools and every prompt registered."""
    registry.load_all()
    mcp = MCPServer(name="vexa-control", instructions=INSTRUCTIONS)
    for fn in registry.TOOLS:
        mcp.tool()(fn)
    for name, title, description, fn in registry.PROMPTS:
        mcp.prompt(name=name, title=title, description=description)(fn)
    return mcp


def transport_security():
    """Which Host headers ``/mcp`` will answer to.

    The SDK's DNS-rebinding guard defaults to localhost-only for a loopback bind. Behind the
    gateway that means every real request is refused, so we add the host we publish ourselves under
    — read from ``VEXA_PUBLIC_MCP_URL``, never from the request — plus the loopback names this
    service and its own health checks use. Deriving it from the published name keeps one source of
    truth: the name in our metadata is exactly the name we accept.
    """
    from urllib.parse import urlparse

    from mcp.server.transport_security import TransportSecuritySettings

    port = str(config.PORT)
    hosts = [f"localhost:{port}", f"127.0.0.1:{port}", "localhost", "127.0.0.1"]
    origins = [f"http://localhost:{port}", f"http://127.0.0.1:{port}"]
    for extra in (os.environ.get("VEXA_MCP_ALLOWED_HOSTS", "") or "").split(","):
        extra = extra.strip()
        if extra and extra not in hosts:
            hosts.append(extra)

    parsed = urlparse(config.CANONICAL)
    pub = parsed.netloc
    if pub and pub not in hosts:
        hosts.append(pub)
        origins.append(f"{parsed.scheme}://{pub}")

    return TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origins)


def http_app():
    """The ASGI app: the streamable-HTTP MCP transport, wrapped in this edge's web surface.

    STATELESS ON PURPOSE — there is no session to lose, so there is nothing to reconnect. The MCP
    client here is the person's own harness; it owns the connection loop, so no retry logic in this
    repo can make it reconnect. What we DO own is whether a session exists to be lost, and a
    stateful server's ``Mcp-Session-Id`` lives in the transport manager's memory: every restart
    invalidates every in-flight client mid-turn, and a dropped connection cannot be resumed.
    Identity comes from the bearer token on every request, which is self-contained by construction,
    so the session was pure liability. The cost is server-initiated streaming, which this server
    does not use — every tool answers in one response.
    """
    from .web import AUTH_MIDDLEWARE
    mcp = build()
    return AUTH_MIDDLEWARE(mcp.streamable_http_app(
        transport_security=transport_security(), stateless_http=True))
