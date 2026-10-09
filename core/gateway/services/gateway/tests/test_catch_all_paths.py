"""A catch-all tail is a path UNDER the prefix it matched, and nothing else.

`/agent/{path:path}` forwards to agent-api's `/api/<path>` and `/mcp/{path:path}` to the MCP's
`/mcp/<path>`. Starlette hands the tail over percent-DECODED, and httpx resolves a dot segment
against the downstream base, so a tail carrying `..` would leave the prefix on the downstream hop.
The edge refuses a `.`/`..` segment — written plainly or encoded — and an encoded slash or
backslash with 400, before authentication and before any forward, and re-encodes every other
segment so it stays data.

TestClient (httpx) normalises dot segments out of a URL before it sends one, which is exactly the
behaviour under test on the far side; these requests are therefore driven into the ASGI app with
the raw target a real server would hand it.
"""
from urllib.parse import unquote

import httpx
import pytest

from gateway import create_app
from conftest import VALID_KEY, FakeAuthorizer, FakeDownstream, FakeRedis, needs_agent


def _app():
    downstream = FakeDownstream()
    app = create_app(FakeAuthorizer(), downstream, FakeRedis(), agent_api_url="http://agent-api")
    return app, downstream


async def _send(app, method: str, raw_target: str) -> int:
    """One request, as uvicorn would deliver it: `path` is the decoded target, dot segments and
    all; `raw_path` is the bytes on the wire."""
    raw = raw_target.encode("latin-1")
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "root_path": "", "query_string": b"",
        "path": unquote(raw_target), "raw_path": raw,
        "headers": [(b"host", b"testserver"), (b"x-api-key", VALID_KEY.encode()),
                    (b"content-type", b"application/json")],
        "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
    }
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


REFUSED = [
    "/agent/../invocations",
    "/agent/%2E%2E/invocations",
    "/agent/%2e%2e/events",
    "/agent/.%2E/invocations",
    "/agent/workspace/../../invocations",
    "/agent/workspace/./tree",
    "/agent/./sessions",
    "/agent/..",
    "/agent/workspace%2F..%2F..%2Finvocations",
    "/agent/workspace%2ftree",
    "/agent/workspace%5C..%5Cinvocations",
]


@needs_agent
@pytest.mark.parametrize("target", REFUSED)
@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
async def test_a_dot_segment_or_encoded_separator_in_the_agent_tail_is_400_before_any_forward(
        method, target):
    app, downstream = _app()
    assert await _send(app, method, target) == 400
    assert downstream.last is None


@pytest.mark.parametrize("target", [
    "/mcp/../tools/report_issue",
    "/mcp/%2E%2E/tools/report_issue",
    "/mcp/x%2F..%2F..%2Ftools",
])
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_the_mcp_tail_is_held_to_the_same_rule(method, target):
    app, downstream = _app()
    assert await _send(app, method, target) == 400
    assert downstream.last is None


@needs_agent
async def test_a_double_encoded_dot_segment_stays_data_under_the_prefix():
    """`%252E%252E` decodes ONCE at the edge to the text `%2E%2E`; it is re-encoded and arrives
    downstream as that text, not as a segment httpx would resolve."""
    app, downstream = _app()
    assert await _send(app, "POST", "/agent/%252E%252E/invocations") == 200
    url = downstream.last["url"]
    assert url == "http://agent-api/api/%252E%252E/invocations"
    assert httpx.URL(url).raw_path == b"/api/%252E%252E/invocations"


@needs_agent
async def test_ordinary_tails_forward_unchanged_and_odd_characters_stay_in_their_segment():
    app, downstream = _app()
    assert await _send(app, "GET", "/agent/workspace/tree") == 200
    assert downstream.last["url"] == "http://agent-api/api/workspace/tree"
    assert await _send(app, "GET", "/agent/sessions/meet-12/history") == 200
    assert downstream.last["url"] == "http://agent-api/api/sessions/meet-12/history"
    assert await _send(app, "GET", "/agent/flows/name@1.md") == 200
    assert httpx.URL(downstream.last["url"]).path == "/api/flows/name@1.md"
    assert await _send(app, "GET", "/agent/workspace/a%3Fb%23c") == 200
    assert httpx.URL(downstream.last["url"]).path == "/api/workspace/a?b#c"
    assert httpx.URL(downstream.last["url"]).query == b""
