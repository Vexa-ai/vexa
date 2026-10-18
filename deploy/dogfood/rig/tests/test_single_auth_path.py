"""Founder ruling, 2026-09-03: the rig has ONE authentication path, and it is the connection's.

The `/do/<tool>?token=…` GET bridge and the `token=` call-argument fallback were a second, parallel
way to authenticate — a credential in a query string and a credential in a tool argument, both
landing in access logs, browser history and the chat transcript. They were built for agents that
can only issue a GET, and they were gated behind `VEXA_RIG_MODE` rather than removed. A gate on a
duplicated auth path still leaves the path there, one env var from being open, and every
instruction paragraph in the file kept teaching agents to use it.

They are DELETED. **Fetch-only agents lose access by design.**

What survives: the bearer token on the connection (`Authorization: Bearer`), and the `?c=<code>`
setup URL, which is how a client that cannot set a header is CONFIGURED. Both are properties of the
connection, decided once, not arguments a model composes per call.
"""
from __future__ import annotations

import ast
import json
import pathlib

from conftest import as_user, tool
import vexa_control_mcp as rig

RIG = pathlib.Path(rig.__file__)


def test_the_do_bridge_is_gone():
    """GATE 1. Not disabled — gone. No route, no handler, no env var that could bring it back."""
    src = RIG.read_text()
    # EXECUTABLE lines only: the comments where the bridge used to be name it on purpose, so a
    # reader who never saw it can tell what was removed and why it is not coming back.
    code = [ln for ln in src.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert not [ln for ln in code if "RIG_MODE" in ln], \
        "VEXA_RIG_MODE still exists, so the bridge is one env var from being back"
    spellings = ['"/do"', "'/do'", '"/do/"', 'startswith("/do']
    for spelling in spellings:
        assert not [ln for ln in code if spelling in ln], f"a /do route survives: {spelling}"


def test_a_do_request_is_a_plain_404(monkeypatch):
    """GATE 2. Driven through the ASGI app, because "the route is gone" is a claim about
    behaviour: an agent that still fetches `/do/whats_waiting?token=…` must get nothing."""
    import asyncio

    async def once(path: str, query: bytes = b"") -> tuple[int, bytes]:
        sent = []

        async def send(msg):
            sent.append(msg)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        await rig.app({"type": "http", "method": "GET", "path": path, "query_string": query,
                       "headers": [(b"host", b"localhost:18310")], "scheme": "http"},
                      receive, send)
        status = next(m["status"] for m in sent if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        return status, body

    for path, q in (("/do/whats_waiting", b"token=vxa_mcp_ANY"),
                    ("/do", b""),
                    ("/do/", b"")):
        status, body = asyncio.run(once(path, q))
        assert status == 404, (path, status, body[:400])
        assert b"vxa_mcp_ANY" not in body


def test_no_tool_advertises_a_token_argument():
    """GATE 3. The schema is what teaches the model. While `token=` is in a signature the client
    shows it as a parameter and a model will fill it — so the argument goes, not just its effect."""
    offenders = []
    for node in ast.walk(ast.parse(RIG.read_text())):
        if not isinstance(node, ast.FunctionDef):
            continue
        decorated = any(
            (isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "tool")
            or getattr(d, "attr", "") == "tool"
            for d in node.decorator_list)
        if decorated and any(a.arg == "token" for a in node.args.args + node.args.kwonlyargs):
            offenders.append(f"{node.name}:{node.lineno}")
    assert offenders == [], f"tools still advertise token=: {offenders}"


def test_a_token_argument_is_ignored_not_honoured(monkeypatch):
    """GATE 4. An agent mid-conversation may still send one. It must not authenticate the call —
    and it must not crash it either, because a TypeError reads to a model as "Vexa is broken"."""
    as_user(monkeypatch, "7")
    rig.CURRENT.set(None)                      # anonymous connection
    rig.CALL_TOKEN.set(None)

    out = json.loads(tool("whats_waiting")(token="vxa_mcp_A_REAL_LOOKING_TOKEN"))

    assert out.get("authenticated") is False, (
        "a token passed as a call argument authenticated the call — the second path is still live")
    assert rig.CALL_TOKEN.get() is None


def test_nothing_teaches_an_agent_to_pass_a_token_or_fetch_a_tool():
    """GATE 5. The instruction text IS the product here — it is what the model reads and does.

    Leaving the paragraphs in place while deleting the routes would produce an agent that follows
    our own documentation into a 404 and reports Vexa as broken."""
    src = RIG.read_text()
    strings = [n.value for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    bad = []
    for s in strings:
        low = s.lower()
        # `?token=` alone is NOT this defect: `_ws_url` puts a short-lived, path-scoped VIEW token
        # in a `/w/` link (R-D04). That one opens one file for fifteen minutes and authenticates
        # no tool call. What is banned is teaching a model to send a credential AS AN ARGUMENT.
        if "token=<" in low or "token=…" in s or "token=..." in s:
            bad.append(("token=", s.strip()[:110]))
        if "/do/" in s or "/do?" in s or s.strip().endswith("/do"):
            bad.append(("/do", s.strip()[:110]))
    assert bad == [], f"instruction text still teaches the removed path: {bad[:6]}"
