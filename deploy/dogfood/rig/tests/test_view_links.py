"""R-D04 — the link handed to a person is not a credential any more."""
from __future__ import annotations

import json
import time

from conftest import as_user, tool
import vexa_control_mcp as rig


def test_rd04_workspace_links_carry_no_durable_bearer(monkeypatch):
    """GATE 1a (R-D04). `workspace_read` returns a link the agent is told to paste to the person.
    That link used to carry the caller's `vxa_mcp_` bearer — a credential that never expires and
    opens every tool — into chat scrollback, browser history and any Referer. Four call sites, none
    gated by RIG_MODE. What comes back now is a view token scoped to one path."""
    as_user(monkeypatch, "7", routes={"/api/workspace/file": (200, {"content": "hello"})})
    rig.CALL_TOKEN.set("vxa_mcp_LIVEDURABLETOKEN")

    out = json.loads(tool("workspace_read")("notes/one.md", token="vxa_mcp_LIVEDURABLETOKEN"))

    blob = json.dumps(out)
    assert "vxa_mcp_" not in blob, "a durable bearer token is still being minted into the link"
    assert rig.VIEW_PREFIX in out["url"]


def test_rd04_a_view_token_opens_one_path_and_expires():
    """GATE 1b (R-D04). The token is bound to its path and to a deadline, so it cannot be
    re-pointed at another file and does not outlive the conversation it was minted in."""
    tok = rig._view_token("7", "notes/one.md")

    assert rig._view_verify(tok, "notes/one.md") == "7"
    assert rig._view_verify(tok, "notes/other.md") == "", "the token is not bound to its path"
    assert rig._view_verify(tok[:-1] + ("0" if tok[-1] != "0" else "1"), "notes/one.md") == "", \
        "a tampered MAC verified"
    assert rig._view_verify("vxa_mcp_LIVEDURABLETOKEN", "notes/one.md") == "", \
        "the viewer still accepts a durable bearer in the query string"

    expired = rig._view_token("7", "notes/one.md", ttl=-1)
    assert rig._view_verify(expired, "notes/one.md") == ""
    assert rig.VIEW_TTL_S <= 3600, "a 'short-lived' view token should not outlive an hour"
    assert time.time() > 0
