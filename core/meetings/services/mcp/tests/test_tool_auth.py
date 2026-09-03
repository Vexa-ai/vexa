"""A TOOL SAYS WHICH CREDENTIAL ITS DOOR TAKES — issue #1468 C2.

The manifest could say who the CALLER must be (`identity`) and nothing at all about what this edge
has to PRESENT to the door behind the tool. So the edge did the only thing it could: forward the
caller's own credential to everything, and find out at call time. Flows' four tools were behind a
deployment-wide operator key this edge does not hold and must never hold, and the result reached the
agent as a JSON-RPC answer with a 401 inside it — a tool that is in `tools/list` and cannot work.

`auth` closes that at BOOT, where every other rule in this assembler already lives:

    subject   the caller's own credential travels, which is what this edge does today
    admin     a deployment-held key travels instead — and the deployment must actually hold it
    none      nothing travels

A tool whose `auth` this deployment cannot satisfy is REFUSED AT ASSEMBLY, naming it. Not published
and 401ing later: an agent that cannot see a tool recovers; an agent told a tool exists and handed a
refusal tells the person the product is broken. That is the same fail-direction as every other rule
in `manifest.py`, applied to the one fact the contract could not express.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vexa_mcp import bind, register
from vexa_mcp import manifest as m

OPENAPI = {"paths": {
    "/flows": {"get": {"summary": "Every flow version", "parameters": []}},
    "/admin/thing": {"post": {"summary": "An operator verb", "parameters": []}},
    "/open": {"get": {"summary": "No credential at all", "parameters": []}},
}}


def _tool(name="flows_list", auth="subject", path="/flows", method="GET", **kw):
    t = {"name": name, "identity": "user", "requires": ["identity", "flows"],
         "route": {"method": method, "path": path}}
    if auth is not ...:
        t["auth"] = auth
    t.update(kw)
    return t


def _manifest(tools=None, **kw):
    doc = {"contract": "mcp.tools.v1", "domain": "flows", "source": "oss", "owner": "core/flows",
           "base_url_env": "FLOWS_API_URL", "served_at": "/.well-known/mcp-tools.json",
           "depends_on": ["identity"],
           "tools": tools if tools is not None else [_tool()]}
    doc.update(kw)
    return doc


DEPLOYED = {"identity", "flows"}


# ── the field exists, and a manifest may not stay silent about it ───────────────────────────────

def test_a_tool_must_say_how_it_is_authenticated():
    """Optional-with-a-default would reproduce the hole this field is for: the next manifest would
    say nothing and the edge would guess, exactly as it guessed for flows."""
    with pytest.raises(m.ManifestError, match="auth"):
        m.validate(_manifest(tools=[_tool(auth=...)]))


def test_the_three_values_and_no_others():
    for good in ("subject", "admin", "none"):
        m.validate(_manifest(tools=[_tool(auth=good)],
                             admin_auth={"header": "X-Flows-Operator-Key",
                                         "key_env": "VEXA_MCP_FLOWS_ADMIN_KEY"}))
    with pytest.raises(m.ManifestError, match="auth"):
        m.validate(_manifest(tools=[_tool(auth="operator")]))


def test_an_admin_tool_must_say_which_header_and_which_variable():
    """`auth: admin` is a promise the DEPLOYMENT has to be able to keep, and it cannot keep one it
    cannot spell: which header the door reads, and which variable holds the key."""
    with pytest.raises(m.ManifestError, match="admin_auth"):
        m.validate(_manifest(tools=[_tool(auth="admin", path="/admin/thing", method="POST")]))


# ── satisfiable, or refused at assembly ─────────────────────────────────────────────────────────

ADMIN_MANIFEST = _manifest(
    tools=[_tool(name="flows_retire", auth="admin", path="/admin/thing", method="POST")],
    admin_auth={"header": "X-Flows-Operator-Key", "key_env": "VEXA_MCP_FLOWS_ADMIN_KEY"})


def test_an_admin_tool_whose_key_this_deployment_does_not_hold_refuses_the_boot():
    with pytest.raises(m.ManifestError) as e:
        m.assemble([ADMIN_MANIFEST], deployed=DEPLOYED, env={})
    assert "flows_retire" in str(e.value) and "VEXA_MCP_FLOWS_ADMIN_KEY" in str(e.value)


def test_the_same_manifest_assembles_when_the_deployment_holds_the_key():
    a = m.assemble([ADMIN_MANIFEST], deployed=DEPLOYED,
                   env={"VEXA_MCP_FLOWS_ADMIN_KEY": "an-operator-key"})
    assert [t.name for t in a.tools] == ["flows_retire"]
    assert a.tools[0].auth == "admin"


def test_a_placeholder_is_not_holding_the_key():
    """A published literal authenticates nobody and everybody — the same refusal list flows-api and
    the services' config.v1 already keep."""
    with pytest.raises(m.ManifestError) as e:
        m.assemble([ADMIN_MANIFEST], deployed=DEPLOYED, env={"VEXA_MCP_FLOWS_ADMIN_KEY": "changeme"})
    assert "flows_retire" in str(e.value)


def test_a_subject_tool_is_always_satisfiable():
    """It travels the caller's own credential, and the caller brought it."""
    a = m.assemble([_manifest()], deployed=DEPLOYED, env={})
    assert [t.auth for t in a.tools] == ["subject"]


def test_an_unsatisfiable_tool_never_reaches_the_surface():
    """The refusal is at ASSEMBLY, before the app has a route for it — never a published tool that
    401s on first use."""
    app = FastAPI()
    with pytest.raises(m.ManifestError):
        a = m.assemble([ADMIN_MANIFEST], deployed=DEPLOYED, env={})
        register.register(app, bind.verify(a, {"flows": OPENAPI}), {"flows": "http://flows"})
    assert not [r for r in app.routes if getattr(r, "operation_id", None) == "flows_retire"]


# ── and what actually travels ───────────────────────────────────────────────────────────────────

def _wire(manifest, env):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    app = FastAPI()
    a = m.assemble([manifest], deployed=DEPLOYED, env=env)
    register.register(app, bind.verify(a, {"flows": OPENAPI}), {"flows": "http://flows"},
                      transport=httpx.MockTransport(handler), env=env)
    return TestClient(app), seen


def test_a_subject_tool_forwards_the_callers_own_credential():
    client, seen = _wire(_manifest(), {})
    client.get("/tools/flows_list", headers={"Authorization": "Bearer person-key"})
    assert seen[-1].headers["X-API-Key"] == "person-key"


def test_an_admin_tool_sends_the_deployments_key_and_not_the_callers():
    """The caller is still authenticated at this edge — they just do not get to present their own
    credential to a door that reads an operator key."""
    env = {"VEXA_MCP_FLOWS_ADMIN_KEY": "an-operator-key"}
    client, seen = _wire(ADMIN_MANIFEST, env)
    client.post("/tools/flows_retire", headers={"Authorization": "Bearer person-key"}, json={})
    sent = seen[-1].headers
    assert sent["X-Flows-Operator-Key"] == "an-operator-key"
    assert "person-key" not in str(dict(sent))


def test_a_none_tool_sends_no_credential_at_all():
    client, seen = _wire(_manifest(tools=[_tool(name="open_thing", auth="none", path="/open",
                                                identity="none")]), {})
    client.get("/tools/open_thing")
    sent = {k.lower(): v for k, v in seen[-1].headers.items()}
    assert "x-api-key" not in sent and "authorization" not in sent


# ── the manifest in this repository declares it ─────────────────────────────────────────────────

def test_every_flows_tool_declares_the_credential_its_own_door_actually_reads():
    """Most of flows' doors take the person's own credential (C1) — `subject_or_operator` on
    `/reactions`, `/timeline`, `/friction`, `GET /flows` — so those tools are `auth: subject`, and
    the edge forwarding it is correct rather than a guess that happened to be wrong.

    `flows_submit` and `flow_lifecycle` are the one real exception, not a drift: `POST /flows` and
    `POST /flows/{name}/{version}/{action}` are gated by flows-api's own plain `auth` dependency,
    which checks ONLY the operator key (`VEXA_FLOWS_API_KEY`) — a caller's own credential 401s
    there, so `auth: subject` would publish a tool this edge could list and never actually call on
    the caller's behalf. Asserted over WHATEVER the manifest holds beyond this one named exception,
    not a list written here — a further tool added by another change silently drifting to `admin`
    is exactly the case a hard-coded exception set would miss."""
    import json
    import pathlib
    doc = json.loads((pathlib.Path(__file__).resolve().parents[5]
                      / "core/flows/mcp.tools.v1.json").read_text())
    assert doc["tools"], "the flows manifest declares no tools"
    admin_gated = {"flows_submit", "flow_lifecycle"}
    assert {t["name"]: t.get("auth") for t in doc["tools"]} == {
        t["name"]: ("admin" if t["name"] in admin_gated else "subject") for t in doc["tools"]}
