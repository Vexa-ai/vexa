"""F44 — a dropped or stale MCP session cannot cost a turn.

⚠ 2026-09-02. The founder was told a control "isn't available this turn". The MCP CLIENT is the
Claude CLI's own — the worker hands it a `.mcp.json` and the CLI owns the connection loop — so no
retry logic in this repo can make that client reconnect. What this repo owns is whether a session
exists to be lost, and the rig was STATEFUL: its `Mcp-Session-Id` lived in the transport manager's
memory, so a rig restart invalidated every in-flight client mid-turn.

Measured on a scratch rig, both versions, same three probes:

                                   stateful (before)   stateless (after)
    tools/list, no session id            400                 200
    tools/list, stale session id     404 "Session not found" 200
    two calls, no session carried       400/400             200/200

Nothing in the rig needs the session: SESSION_BIND is declared and read once and never written,
CURRENT_SID is set and never read, and identity is the bearer token on every request.
"""
from __future__ import annotations

import pathlib
import re

RIG = pathlib.Path(__file__).resolve().parents[3] / "deploy/dogfood/rig/vexa_control_mcp.py"


def _src() -> str:
    return RIG.read_text()


def test_the_rig_serves_statelessly():
    src = _src()
    m = re.search(r"streamable_http_app\((.*?)\)\)", src, re.S)
    assert m, "the app construction moved — re-read it before trusting this test"
    assert "stateless_http=True" in m.group(1), (
        "a stateful rig loses every in-flight client on restart; there is no client-side retry to "
        "fall back on, because the client is the Claude CLI's own")


def test_nothing_depends_on_the_transport_session():
    """The reason statelessness is SAFE here — asserted, not remembered.

    A WRITE is a subscript assignment, a mutating method, or a rebinding. The single declaration is
    excluded by an exact string comparison rather than a negative lookahead: the lookahead version
    backtracked over the space before `{}` and flagged the declaration anyway, which is the sort of
    clever that fails quietly."""
    src = _src()
    decl = "SESSION_BIND: dict = {}"
    writes = []
    for raw in src.splitlines():
        ln = raw.strip()
        if ln.startswith("#") or ln == decl:
            continue
        if re.search(r"SESSION_BIND\s*\[[^\]]+\]\s*=", ln):
            writes.append(ln)
        elif re.search(r"SESSION_BIND\.(update|setdefault|pop|clear)\(", ln):
            writes.append(ln)
        elif re.match(r"SESSION_BIND\s*(:\s*\w+\s*)?=", ln):
            writes.append(ln)
    assert writes == [], (
        f"SESSION_BIND is now written — statelessness is no longer free: {writes}")
    assert "CURRENT_SID.get()" not in src, "something now reads the session id"


def test_identity_is_per_request_not_per_session():
    # A stateless server can only work if every request carries its own identity. It does: the
    # bearer token is resolved on each one.
    src = _src()
    assert "def _subject_raw()" in src
    assert "CALL_TOKEN.get()" in src
