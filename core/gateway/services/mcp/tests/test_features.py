"""VEXA_FEATURES at the edge: a release install (empty) lists no tool of an unproven feature.

``browser_bots`` off ⇒ browser_session_open, bot_accounts_list and bot_account_verify are not
registered; ``crm`` off ⇒ no assembled tool of domain crm is, even if crm-api's manifest names them.
``all`` (or the feature's name) serves them as before. The parse is agent-api's
(``core/agent/control_plane/features.py`` ``install_defaults``), copied — read here by path only.
"""
from __future__ import annotations

import json
import pathlib
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from vexa_mcp import create_app
from vexa_mcp import features
from vexa_mcp import manifest as m

from conftest import GATEWAY_URL, meetings_assembly

CORE = pathlib.Path(__file__).resolve().parents[4]
AGENT_FEATURES = CORE / "agent" / "control_plane" / "features.py"
CRM = json.loads((CORE / "crm" / "mcp.tools.v1.json").read_text())
CRM_TOOLS = {t["name"] for t in CRM["tools"]}


def _crm_openapi() -> dict:
    """The slice of crm-api's OpenAPI its manifest's routes occupy (each argument a body field)."""
    paths = {}
    for t in CRM["tools"]:
        props = {a: {"type": "string"} for a in t.get("arguments", [])}
        paths.setdefault(t["route"]["path"], {})[t["route"]["method"].lower()] = {
            "operationId": t["name"], "description": t["name"],
            "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": props}}}},
        }
    return {"openapi": "3.1.0", "paths": paths}


def _crm_answer(request: httpx.Request) -> httpx.Response:
    if request.url.host == "crm-api.test":
        if request.url.path == "/.well-known/mcp-tools.json":
            return httpx.Response(200, json=CRM)        # the full manifest: the edge must drop it itself
        if request.url.path == "/openapi.json":
            return httpx.Response(200, json=_crm_openapi())
    return httpx.Response(404)


def _tools(raw, *, crm=False):
    env = {} if raw is None else {"VEXA_FEATURES": raw}
    kwargs = meetings_assembly({"CRM_API_URL": "http://crm-api.test"} if crm else None,
                               answer=_crm_answer if crm else None)
    app = create_app(GATEWAY_URL, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
                     features_env=env, **kwargs)
    return app, {t.name for t in app.state.mcp.tools}


@pytest.mark.parametrize("raw,name,on", [
    (None, "crm", False), ("", "browser_bots", False), ("all", "crm", True), ("crm", "crm", True),
    ("crm", "browser_bots", False), ("browser_bots, crm", "browser_bots", True),
    ("all,-crm", "crm", False), ("all,-crm", "browser_bots", True), ("-crm,all", "crm", True),
])
def test_the_parse_is_agent_apis(raw, name, on):
    assert features.switched_on(name, {} if raw is None else {"VEXA_FEATURES": raw}) is on


def test_parity_with_the_registry_it_copies():
    if not AGENT_FEATURES.is_file():
        pytest.skip("agent-api is not in this tree")
    text = AGENT_FEATURES.read_text()
    assert re.search(r'^ENV = "VEXA_FEATURES"$', text, re.M)
    for name in (features.CRM, features.BROWSER_BOTS):
        block = re.search(rf'"{name}": \{{(.*?)\n    \}}', text, re.S)
        assert block and '"scope": "install"' in block.group(1), name
    bots = re.search(r'"browser_bots": \{(.*?)\n    \}', text, re.S).group(1)
    assert "browser_session_open" in bots and "bot_accounts_" in bots


@pytest.mark.parametrize("raw", [None, "", "crm"])
def test_off_the_bot_account_tools_are_not_listed_or_served(raw):
    app, names = _tools(raw)
    assert not names & features.BOT_ACCOUNT_TOOLS
    assert "request_meeting_bot" in names
    assert "Signed-in bots:" not in app.state.mcp.server.instructions
    assert TestClient(app).get("/bot-accounts", headers={"Authorization": "Bearer k"}).status_code in (404, 405)


@pytest.mark.parametrize("raw", ["all", "browser_bots"])
def test_on_the_bot_account_tools_are_listed(raw):
    app, names = _tools(raw)
    assert features.BOT_ACCOUNT_TOOLS <= names
    assert "Signed-in bots:" in app.state.mcp.server.instructions


@pytest.mark.parametrize("raw", [None, "", "all,-crm", "browser_bots"])
def test_off_no_crm_tool_is_assembled(raw):
    app, names = _tools(raw, crm=True)
    assert not names & CRM_TOOLS
    assert "crm" not in app.state.assembly.by_domain


@pytest.mark.parametrize("raw", ["all", "crm"])
def test_on_the_crm_tools_are_assembled(raw):
    _, names = _tools(raw, crm=True)
    assert CRM_TOOLS <= names


def test_dropping_is_only_the_switched_off_domain():
    assembly = m.assemble([CRM], deployed={"identity", "crm"})
    features.drop_switched_off_domains(assembly, {"VEXA_FEATURES": ""})
    assert not [t for t in assembly.tools if t.domain == "crm"]
    kept = m.assemble([CRM], deployed={"identity", "crm"})
    features.drop_switched_off_domains(kept, {"VEXA_FEATURES": "all"})
    assert {t.name for t in kept.tools} >= CRM_TOOLS
