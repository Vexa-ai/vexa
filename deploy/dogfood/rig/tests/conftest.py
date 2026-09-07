"""The rig's first behavioural tests — and the harness that makes them possible.

`vexa_control_mcp.py` is 5,033 lines and 53 verbs with **no test that calls one of them** (R-D20):
the only file that touched it, `core/agent/tests/test_rig_stateless.py`, greps the source text. So
the shell injection, the missing membership check and an operator gate that broke `bot_schedule`
were all invisible to a green suite. Every one of them is a function call away.

Two things stood in the way, and both are handled here rather than in each test:

* **The MCP SDK.** The module builds its ASGI app at import time. A stub server is installed when
  the real `mcp.server.mcpserver` is not importable, so a test run does not depend on an SDK
  version being present. The stub registers tools exactly where the module reads them back
  (`mcp._tool_manager._tools`), which is also what the `/do` bridge uses.
* **The live host.** `VEXA_RIG_STATE_DIR` points every credential store at a tmp directory before
  the module is imported, and `VEXA_FLOWS_API_KEY` is set so the import-time key read never opens
  `~/.storm/flows-api-key`. Nothing in this suite reads or writes a real credential store.
"""
from __future__ import annotations

import importlib
import os
import pathlib
import sys
import tempfile
import types

RIG_DIR = pathlib.Path(__file__).resolve().parents[1]
_STATE = pathlib.Path(tempfile.mkdtemp(prefix="rig-tests-"))

# Set BEFORE the module is imported: both are read at import time.
os.environ["VEXA_RIG_STATE_DIR"] = str(_STATE)
os.environ.setdefault("VEXA_FLOWS_API_KEY", "test-flows-key")
# F95 made this fail-closed at import: no default, and the process refuses to start without
# it. A test value keeps the suite offline — it is never compared against anything real.
os.environ.setdefault("INTERNAL_API_SECRET", "test-internal-secret")
os.environ.setdefault("VEXA_MCP_DELEGATION_SECRET", "test-delegation-secret")
os.environ.setdefault("VEXA_MCP_VIEW_SECRET", "test-view-secret")
os.environ.setdefault("VEXA_RIG_IMPORT_DIR", str(_STATE / "imports"))

if str(RIG_DIR) not in sys.path:
    sys.path.insert(0, str(RIG_DIR))


def _install_mcp_stub() -> None:
    """A minimal `mcp.server.mcpserver` — enough for import, registration and the `/do` lookup."""
    try:
        importlib.import_module("mcp.server.mcpserver")
        importlib.import_module("mcp.server.transport_security")
        return
    except Exception:  # noqa: BLE001 — absent or a different SDK generation; stub either way
        pass

    class _ToolManager:
        def __init__(self):
            self._tools = {}

    class _Tool:
        def __init__(self, fn):
            self.fn = fn

    class MCPServer:
        def __init__(self, *a, **kw):
            self._tool_manager = _ToolManager()

        def tool(self, *a, **kw):
            def deco(fn):
                self._tool_manager._tools[fn.__name__] = _Tool(fn)
                return fn
            return deco

        def prompt(self, *a, **kw):
            def deco(fn):
                return fn
            return deco

        def streamable_http_app(self, *a, **kw):
            async def _app(scope, receive, send):
                """The MCP app under our middleware. It knows the MCP endpoint and nothing else,
                so any other path is a 404 — which is what makes "the /do route is gone" testable:
                the request has to fall all the way through `_Auth` to get here."""
                body = b'{"error":"not_found"}'
                await send({"type": "http.response.start", "status": 404, "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
            return _app

    class TransportSecuritySettings:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    root = types.ModuleType("mcp")
    server = types.ModuleType("mcp.server")
    mcpserver = types.ModuleType("mcp.server.mcpserver")
    transport = types.ModuleType("mcp.server.transport_security")
    mcpserver.MCPServer = MCPServer
    transport.TransportSecuritySettings = TransportSecuritySettings
    server.mcpserver, server.transport_security = mcpserver, transport
    root.server = server
    for name, mod in (("mcp", root), ("mcp.server", server),
                      ("mcp.server.mcpserver", mcpserver),
                      ("mcp.server.transport_security", transport)):
        sys.modules[name] = mod


_install_mcp_stub()

import rig_secrets  # noqa: E402
import vexa_control_mcp as rig  # noqa: E402

STATE = _STATE


def tool(name: str):
    """The callable a client actually reaches — the registered, fully decorated tool body."""
    return rig.mcp._tool_manager._tools[name].fn


class HTTP:
    """A recording stand-in for `rig._http`. Every test asserts on what the rig ASKED FOR, which
    is where authorization lives: the subject on a query string, the header a secret travels in."""

    def __init__(self, admin_uids=(), routes=None):
        self.calls = []
        self.admin_uids = {str(u) for u in admin_uids}
        self.routes = routes or {}

    def __call__(self, method, url, headers=None, body=None, timeout=40):
        self.calls.append({"method": method, "url": url, "headers": headers or {}, "body": body})
        for frag, answer in self.routes.items():
            if frag in url:
                return answer
        if "/admin/users/" in url and method == "GET":
            uid = url.rsplit("/", 1)[-1]
            return 200, {"id": uid, "data": {"is_admin": uid in self.admin_uids}}
        return 200, {}

    def urls(self, frag=""):
        return [c["url"] for c in self.calls if frag in c["url"]]


def as_user(monkeypatch, uid="7", admin=False, routes=None) -> HTTP:
    """Sign a test in as ``uid`` and route every HTTP call through a recorder."""
    http = HTTP(admin_uids=[uid] if admin else [], routes=routes)
    monkeypatch.setattr(rig, "_http", http)
    monkeypatch.setattr(rig, "_admin_key", lambda: "test-admin-key")
    rig._UID_ALIVE.clear()
    rig.CURRENT.set(str(uid))
    rig.CALL_SCOPE.set(None)
    rig.CALL_TOKEN.set(None)
    # No chat target unless a test sets one (Vexa-ai/vexa#1611). A contextvar outlives a test in the
    # same process, so leaving this would let one test's target decide another test's writes — the
    # exact silent-wrong-workspace failure the field exists to end.
    rig.CALL_TARGET.set("")
    return http
