"""A WARM WORKER SURVIVES A DEPLOY (perf, app.dev 2026-10-17). When the gateway or the MCP service
restarts, the server forgets every streamable-http session and answers 404 to a request that carries
the old ``Mcp-Session-Id``. The worker used to fail every tool call from then on — a flow turn burned
its whole 40-call budget on those 404s. Now the client starts a new session once and retries."""
import json

import httpx

from llm.openai_agent import _MCPServer


def _server(state):
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        method = body.get("method")
        sid = request.headers.get("mcp-session-id")
        state["seen"].append((method, sid))
        if method == "initialize":
            assert sid is None, "a new session is asked for without the old id"
            state["n"] += 1
            state["live"] = f"s{state['n']}"
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {}},
                                  headers={"mcp-session-id": state["live"]})
        if method == "notifications/initialized":
            return httpx.Response(202)
        if sid != state["live"]:
            return httpx.Response(404, json={"jsonrpc": "2.0", "error": {"code": -32001,
                                                                       "message": "Session not found"}})
        if method == "tools/list":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                             "result": {"tools": [{"name": "timeline"}]}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                         "result": {"content": [{"type": "text", "text": "ok"}]}})
    return handle


def _attached(state):
    srv = _MCPServer("vexa", {"type": "http", "url": "http://gateway.test/mcp"},
                     client=httpx.Client(transport=httpx.MockTransport(_server(state))))
    assert [t["name"] for t in srv.start()] == ["timeline"]
    return srv


def test_a_forgotten_session_is_restarted_once_and_the_call_succeeds():
    state = {"n": 0, "live": None, "seen": []}
    srv = _attached(state)
    state["live"] = "after-restart"                      # the deploy: the server forgot s1
    assert srv.call("timeline", {}) == (True, "ok")
    assert state["n"] == 2 and srv._session_header["Mcp-Session-Id"] == "s2"
    assert srv.call("timeline", {}) == (True, "ok")      # and stays on the new session
    assert state["n"] == 2


def test_a_server_that_keeps_forgetting_is_an_ordinary_failed_tool_not_a_loop():
    state = {"n": 0, "live": None, "seen": []}
    srv = _attached(state)
    real = srv._initialize

    def reinit_then_forget():
        real()
        state["live"] = "gone-again"
    srv._initialize = reinit_then_forget
    state["live"] = "after-restart"
    ok, text = srv.call("timeline", {})
    assert not ok and "404" in text
    assert [m for m, _ in state["seen"]].count("initialize") == 2  # one retry, no more
