"""An optional tool provider (flows) that is down at boot does not take the meeting tools down.

The edge boots, serves the meeting tools, logs the missing domain, and picks its tools up when it
answers — under the same visibility allow-list as everything else."""
from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from vexa_mcp import create_app

from conftest import GATEWAY_URL, MEETINGS_MANIFEST, MEETINGS_OPENAPI
from test_assembled_surface import FLOWS_MANIFEST, FLOWS_OPENAPI
from test_tool_visibility import PROD, V012, _list, _session

FLOWS_TOOLS = {t["name"] for t in FLOWS_MANIFEST["tools"]}


def _app(flows_up, caplog=None, **tools_env):
    def domains(request: httpx.Request) -> httpx.Response:
        if request.url.host == "flows.test" and not flows_up["on"]:
            raise httpx.ConnectError("connection refused")
        manifests = {"meeting-api.test": (MEETINGS_MANIFEST, MEETINGS_OPENAPI),
                     "flows.test": (FLOWS_MANIFEST, FLOWS_OPENAPI)}
        if request.url.host in manifests:
            manifest, openapi = manifests[request.url.host]
            if request.url.path == "/.well-known/mcp-tools.json":
                return httpx.Response(200, json=manifest)
            if request.url.path == "/openapi.json":
                return httpx.Response(200, json=openapi)
        return httpx.Response(404)

    return create_app(
        GATEWAY_URL, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True})),
        assembly_env={"ADMIN_API_URL": "http://identity.test", "MEETING_API_URL": "http://meeting-api.test",
                      "FLOWS_API_URL": "http://flows.test", "VEXA_FLOWS_API_KEY": "test-operator-key",
                      "VEXA_MCP_BOOT_PROBE_PAUSE_SECONDS": "0", "VEXA_MCP_REFRESH_SECONDS": "3600"},
        assembly_transport=httpx.MockTransport(domains),
        features_env={"VEXA_FEATURES": ""}, visibility_env=tools_env)


def test_flows_down_at_boot_the_meeting_tools_are_served(caplog):
    app = _app({"on": False})
    with TestClient(app) as client:
        names = {t["name"] for t in _list(client, _session(client))}
    assert {"list_meetings", "get_meeting_transcript", "request_meeting_bot", "stop_bot"} <= names
    assert not (names & FLOWS_TOOLS)
    assert app.state.late_domains.pending == {"flows": "http://flows.test"}
    assert "flows" in caplog.text


def test_flows_returns_and_its_tools_appear():
    up = {"on": False}
    app = _app(up)
    with TestClient(app) as client:
        head = _session(client)
        assert app.state.late_domains.poll_once() == []        # still down
        up["on"] = True
        assert app.state.late_domains.poll_once() == ["flows"]
        names = {t["name"] for t in _list(client, head)}
        assert FLOWS_TOOLS <= names
        assert app.state.late_domains.pending == {}
        # and a late tool is callable, not just listed
        r = client.post("/mcp", headers=head, json={"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                                    "params": {"name": "flows_list", "arguments": {}}})
        assert r.json()["result"].get("isError") is not True, r.text


def test_late_tools_obey_the_allow_list_and_prod_is_exactly_the_012_list():
    up = {"on": False}
    app = _app(up, **PROD)
    with TestClient(app) as client:
        head = _session(client)
        before = sorted(t["name"] for t in _list(client, head))
        assert set(before) < set(V012)                        # flows' 0.12 tools wait for flows
        up["on"] = True
        app.state.late_domains.poll_once()
        after = sorted(t["name"] for t in _list(client, head))
    assert after == V012                                      # no extra tool for an existing key


def test_flows_up_at_boot_nothing_is_pending():
    app = _app({"on": True}, **PROD)
    assert app.state.late_domains is None
    with TestClient(app) as client:
        assert sorted(t["name"] for t in _list(client, _session(client))) == V012


# ── the meeting provider too (rehearsal re-run 2: 19 tools, not 21, after a rolling upgrade) ────
MEETINGS_TOOLS = {t["name"] for t in MEETINGS_MANIFEST["tools"]}


def _app_meetings(state, **tools_env):
    """meeting-api is `down` (refuses), `old` (an older pod: 404 for the manifest) or `up`."""
    def domains(request: httpx.Request) -> httpx.Response:
        if request.url.host == "meeting-api.test":
            if state["m"] == "down":
                raise httpx.ConnectError("connection refused")
            if state["m"] == "old":
                return httpx.Response(404)
        manifests = {"meeting-api.test": (MEETINGS_MANIFEST, MEETINGS_OPENAPI),
                     "flows.test": (FLOWS_MANIFEST, FLOWS_OPENAPI)}
        if request.url.host in manifests:
            manifest, openapi = manifests[request.url.host]
            if request.url.path == "/.well-known/mcp-tools.json":
                return httpx.Response(200, json=manifest)
            if request.url.path == "/openapi.json":
                return httpx.Response(200, json=openapi)
        return httpx.Response(404)

    return create_app(
        GATEWAY_URL, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True})),
        assembly_env={"ADMIN_API_URL": "http://identity.test", "MEETING_API_URL": "http://meeting-api.test",
                      "FLOWS_API_URL": "http://flows.test", "VEXA_FLOWS_API_KEY": "test-operator-key",
                      "VEXA_MCP_BOOT_PROBE_PAUSE_SECONDS": "0", "VEXA_MCP_REFRESH_SECONDS": "3600"},
        assembly_transport=httpx.MockTransport(domains),
        features_env={"VEXA_FEATURES": ""}, visibility_env=tools_env)


def _rolls_in(start):
    state = {"m": start}
    app = _app_meetings(state, **PROD)
    with TestClient(app) as client:
        head = _session(client)
        before = {t["name"] for t in _list(client, head)}
        assert not (before & MEETINGS_TOOLS), before          # booted without them, did not fail
        assert app.state.late_domains.poll_once() == []        # still not there: still pending
        state["m"] = "up"
        assert app.state.late_domains.poll_once() == ["meetings"]
        after = sorted(t["name"] for t in _list(client, head))
    assert after == V012                                       # the allowed 21, nothing more


def test_meeting_api_unavailable_at_boot_heals_to_the_allowed_21():
    _rolls_in("down")


def test_an_older_meeting_api_pod_answering_404_at_boot_heals_to_the_allowed_21():
    _rolls_in("old")
