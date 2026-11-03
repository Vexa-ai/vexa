"""One route, one tool name — unless the second name says, on the record, that it is an alias.

A second name for a door is sometimes deliberate (a name an agent already knows from somewhere
else). Undeclared, it is indistinguishable from a copy-paste that will drift: one name gains an
argument, the other does not, and the same door answers two different tools. So a manifest may
bind one route to two names only when the second declares `"alias_of": "<first name>"` and takes
exactly the same arguments.
"""
from __future__ import annotations

import pytest

from vexa_mcp import manifest as m


def _tool(name, path="/api/read", arguments=("message_id",), **kw):
    return {"name": name, "identity": "user", "auth": "subject", "requires": ["identity", "agent"],
            "route": {"method": "POST", "path": path}, "arguments": list(arguments), **kw}


def _doc(*tools):
    return {"contract": "mcp.tools.v1", "domain": "agent", "source": "oss", "owner": "core/agent",
            "base_url_env": "AGENT_API_URL", "served_at": "/.well-known/mcp-tools.json",
            "depends_on": ["identity"], "tools": list(tools)}


def test_two_names_for_one_route_are_refused():
    with pytest.raises(m.ManifestError, match="alias_of"):
        m.validate(_doc(_tool("gmail_read"), _tool("mail_read")))


def test_a_declared_alias_is_accepted():
    m.validate(_doc(_tool("gmail_read"), _tool("mail_read", alias_of="gmail_read")))


@pytest.mark.parametrize("alias", [
    _tool("mail_read", alias_of="nope"),
    _tool("mail_read", path="/api/other", alias_of="gmail_read"),
    _tool("mail_read", arguments=("message_id", "extra"), alias_of="gmail_read"),
    _tool("mail_read", alias_of="mail_read"),
])
def test_an_alias_names_a_tool_on_the_same_route_with_the_same_arguments(alias):
    with pytest.raises(m.ManifestError, match="alias_of"):
        m.validate(_doc(_tool("gmail_read"), alias))


def test_the_shipped_agent_manifest_declares_its_alias():
    import json
    import pathlib

    root = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "core" / "agent").is_dir())
    doc = json.loads((root / "core" / "agent" / "mcp.tools.v1.json").read_text())
    m.validate(doc)
    aliases = {t["name"]: t["alias_of"] for t in doc["tools"] if "alias_of" in t}
    assert aliases == {"mail_read": "gmail_read"}
