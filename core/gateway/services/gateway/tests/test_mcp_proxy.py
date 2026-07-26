"""#795 — the gateway's ``/mcp`` front door: the streamable-HTTP MCP transport.

MCP streamable-HTTP splits the transport in two: ``POST /mcp`` carries messages (short
request/response JSON) and ``GET /mcp`` opens the server→client **SSE stream**, which sends its
headers and then idles until the server has something to push. A buffered proxy forward waits on
the next body read of that idle stream, hits its read timeout, and answers the caller a
gateway-manufactured 5xx that the MCP service never sees — the hosted-prod shape reported in #795
(8 × 503 on ``GET`` at the edge, 0 at the service; ``POST`` 116/116 healthy on the same client).

These rows drive the SHIPPED ``create_app`` with injected fakes (no network, no MCP service):

  1. ``GET /mcp`` is STREAMED — chunks relay as they arrive, the connection holds open past the
     buffered client's bound, and the buffered ``request`` port is never touched;
  2. ``POST /mcp`` is buffered and verbatim (status + body) — the negative control that stays
     green on both sides of the stream-leg change;
  3. a stream leg whose upstream is unreachable answers a TYPED 502/504, never a blanket 503;
  4. the downstream STATUS is carried verbatim on the stream leg (the 0.10 ``400 Missing session
     ID → 200`` laundering is dead — the gateway never rewrites an upstream status);
  5. the transport's ``mcp-session-id`` / ``mcp-protocol-version`` response headers survive BOTH
     legs (drop them and the handshake cannot complete);
  6. fail-closed auth at the edge, with the key accepted from ``x-api-key`` or the MCP transport's
     ``Authorization: Bearer`` carrier.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway import create_app
from conftest import VALID_KEY, FakeAuthorizer, FakeDownstream, FakeRedis

AUTH = {"x-api-key": VALID_KEY}
SSE_ACCEPT = {"accept": "text/event-stream"}
MCP_URL = "http://mcp:8010"


class _FakeStreamedResponse:
    """Satisfies ``ports.StreamedResponse``: a head (status + headers) readable BEFORE the body,
    then the body chunks. ``hold=True`` makes the stream go SILENT after its frames — the MCP
    SSE leg's normal resting state, and precisely what kills a buffered forward."""

    def __init__(self, status_code: int, headers: dict, chunks, hold: bool):
        self.status_code = status_code
        self.headers = headers
        self._chunks = list(chunks)
        self._hold = hold

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk
        if self._hold:
            await asyncio.sleep(3600)  # silence — the stream never completes on its own


class StreamingDownstream:
    """A ``ports.DownstreamClient`` that records WHICH leg the gateway used.

    ``open_stream`` is the head-aware streaming forward; ``request`` is the buffered one. The
    rows below assert the GET took the stream leg and NEVER the buffered one — the buffered
    forward of a silent SSE stream is the defect #795 reports.
    """

    def __init__(self, status_code: int = 200, headers=None, chunks=(), hold: bool = False,
                 raises: Exception = None, buffered_status: int = 200, buffered_body=None):
        self.status_code = status_code
        self.headers = headers or {"content-type": "text/event-stream"}
        self.chunks = list(chunks)
        self.hold = hold
        self.raises = raises
        self.buffered_status = buffered_status
        self.buffered_body = buffered_body if buffered_body is not None else {"ok": True}
        self.opened = False
        self.closed = False
        self.buffered_calls: list = []
        self.last = None

    @asynccontextmanager
    async def open_stream(self, method, url, *, headers=None, params=None, content=None):
        self.last = {"method": method, "url": url, "headers": headers or {},
                     "params": params, "content": content}
        if self.raises is not None:
            raise self.raises
        self.opened = True
        try:
            yield _FakeStreamedResponse(self.status_code, self.headers, self.chunks, self.hold)
        finally:
            self.closed = True

    async def stream(self, method, url, *, headers=None, params=None, content=None):
        async with self.open_stream(method, url, headers=headers, params=params, content=content) as resp:
            async for chunk in resp.aiter_bytes():
                yield chunk

    async def request(self, method, url, *, headers=None, params=None, content=None):
        call = {"method": method, "url": url, "headers": headers or {},
                "params": params, "content": content}
        self.buffered_calls.append(call)
        self.last = call

        class _R:
            status_code = self.buffered_status
            content = json.dumps(self.buffered_body).encode()
            headers = {"content-type": "application/json", **{
                k: v for k, v in self.headers.items() if k.startswith("mcp-")}}

        return _R()


def _app(downstream=None):
    downstream = downstream or StreamingDownstream()
    return create_app(FakeAuthorizer(), downstream, FakeRedis(), mcp_url=MCP_URL), downstream


def _client(downstream=None):
    app, downstream = _app(downstream)
    return TestClient(app), downstream


class AsgiDrive:
    """Drive the ASGI app DIRECTLY, one message at a time.

    ``TestClient`` cannot witness a response that has not ENDED: its transport runs the app to
    completion and hands back the whole body at once (starlette ``testclient.py``:
    ``raw_kwargs["stream"] = httpx.ByteStream(raw_kwargs["stream"].read())``). A held-open SSE
    stream never ends, so the only way to observe "headers out + first frame relayed while the
    upstream is still open" is at the ASGI message level — which is exactly the property #795 is
    about. Collected ``send`` messages are the evidence; the request task stays alive until the
    test disconnects the client.
    """

    def __init__(self, app, method: str, path: str, headers: dict, query: bytes = b"", body: bytes = b""):
        self._app = app
        self._scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": method, "path": path, "raw_path": path.encode(),
            "query_string": query, "root_path": "", "scheme": "http",
            "server": ("testserver", 80), "client": ("1.2.3.4", 5000),
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        }
        self._body = body
        self._request_sent = False
        self._disconnect = asyncio.Event()
        self._arrived = asyncio.Event()
        self.messages: list = []
        self.task = None

    async def _receive(self):
        if not self._request_sent:
            self._request_sent = True
            return {"type": "http.request", "body": self._body, "more_body": False}
        await self._disconnect.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message):
        self.messages.append(message)
        self._arrived.set()

    async def __aenter__(self):
        self.task = asyncio.create_task(self._app(self._scope, self._receive, self._send))
        return self

    async def __aexit__(self, *exc):
        self._disconnect.set()
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    async def wait_for(self, count: int, timeout: float = 2.0):
        """Wait until ``count`` ASGI messages have been sent (or fail the row)."""
        async with asyncio.timeout(timeout):
            while len(self.messages) < count:
                self._arrived.clear()
                await self._arrived.wait()
        return self.messages

    @property
    def start(self) -> dict:
        return next(m for m in self.messages if m["type"] == "http.response.start")

    def header(self, name: str):
        raw = dict(self.start["headers"])
        return raw.get(name.lower().encode(), b"").decode()

    @property
    def body_chunks(self) -> list:
        return [m.get("body", b"") for m in self.messages if m["type"] == "http.response.body"]


# ---------------------------------------------------------------- row 1: the stream leg
async def test_get_mcp_is_streamed_and_holds_open_on_a_silent_upstream():
    """Row 1: on an SSE upstream that emits one frame and then goes SILENT FOREVER, the gateway
    emits its response head and relays the frame — and the request is STILL RUNNING, downstream
    stream still open, with the buffered forward never touched. The buffered forward cannot reach
    this state at all: it is still waiting on the next body read, which is where #795's 30-second
    ReadTimeout → `503 MCP service unavailable` was manufactured."""
    ds = StreamingDownstream(
        headers={"content-type": "text/event-stream", "mcp-session-id": "sess-1"},
        chunks=[b'event: message\ndata: {"jsonrpc":"2.0","id":1}\n\n'],
        hold=True,
    )
    app, _ = _app(ds)
    async with AsgiDrive(app, "GET", "/mcp", {**AUTH, **SSE_ACCEPT}) as drive:
        await drive.wait_for(2)  # response.start + the first body chunk
        assert drive.start["status"] == 200
        assert drive.header("content-type").startswith("text/event-stream")
        assert drive.header("mcp-session-id") == "sess-1"
        assert b'"jsonrpc"' in b"".join(drive.body_chunks)
        # the load-bearing part: nothing has ended.
        assert drive.task.done() is False, "the SSE leg must stay open while the upstream is silent"
        assert ds.opened is True and ds.closed is False
        assert ds.buffered_calls == [], "the SSE leg must never take the buffered forward (#795)"
        assert ds.last["url"] == f"{MCP_URL}/mcp"


async def test_get_mcp_subpath_is_streamed_too():
    """The transport may be mounted at a subpath; the GET leg streams there as well."""
    ds = StreamingDownstream(chunks=[b"data: hi\n\n"], hold=True)
    app, _ = _app(ds)
    async with AsgiDrive(app, "GET", "/mcp/messages", {**AUTH, **SSE_ACCEPT}) as drive:
        await drive.wait_for(2)
        assert drive.start["status"] == 200
        assert b"data: hi" in b"".join(drive.body_chunks)
        assert drive.task.done() is False
    assert ds.last["url"] == f"{MCP_URL}/mcp/messages"
    assert ds.buffered_calls == []


async def test_client_disconnect_closes_the_downstream_stream():
    """A held-open relay must not leak the downstream connection: when the caller goes away, the
    gateway closes its upstream stream (the exit stack the body iterator holds)."""
    ds = StreamingDownstream(chunks=[b"data: hi\n\n"], hold=True)
    app, _ = _app(ds)
    async with AsgiDrive(app, "GET", "/mcp", {**AUTH, **SSE_ACCEPT}) as drive:
        await drive.wait_for(2)
        assert ds.closed is False
    for _ in range(50):  # the cancellation unwinds on the loop
        if ds.closed:
            break
        await asyncio.sleep(0.01)
    assert ds.closed is True


def test_stream_leg_carries_query_and_injected_identity():
    ds = StreamingDownstream(chunks=[b"data: hi\n\n"])
    client, _ = _client(ds)
    with client.stream("GET", "/mcp?sessionId=abc", headers={**AUTH, **SSE_ACCEPT}) as r:
        list(r.iter_bytes())
    assert ds.last["params"] == {"sessionId": "abc"}
    assert ds.last["headers"]["x-user-id"] == "7"  # resolved user, injected by the edge
    assert ds.last["headers"]["accept"] == "text/event-stream"  # client's Accept forwarded verbatim


# ---------------------------------------------------------------- row 2: the buffered leg
def test_post_mcp_is_buffered_and_verbatim():
    """Row 2 (negative control): POST carries MCP messages — short request/response JSON. It stays
    on the buffered forward with status + body verbatim, before AND after the stream-leg change."""
    ds = StreamingDownstream(buffered_status=202, buffered_body={"jsonrpc": "2.0", "result": {}})
    client, _ = _client(ds)
    r = client.post("/mcp", headers=AUTH, json={"jsonrpc": "2.0", "method": "initialize", "id": 1})
    assert r.status_code == 202
    assert r.json() == {"jsonrpc": "2.0", "result": {}}
    assert ds.opened is False, "the message leg must not open a stream"
    assert ds.buffered_calls[-1]["url"] == f"{MCP_URL}/mcp"
    assert ds.buffered_calls[-1]["method"] == "POST"
    assert json.loads(ds.buffered_calls[-1]["content"])["method"] == "initialize"


def test_post_mcp_subpath_and_delete_forward_buffered():
    """DELETE /mcp terminates a session in streamable-HTTP; every non-GET method forwards buffered."""
    ds = StreamingDownstream(buffered_status=200)
    client, _ = _client(ds)
    assert client.post("/mcp/messages", headers=AUTH, json={}).status_code == 200
    assert ds.buffered_calls[-1]["url"] == f"{MCP_URL}/mcp/messages"
    assert client.delete("/mcp", headers=AUTH).status_code == 200
    assert ds.buffered_calls[-1]["method"] == "DELETE"
    assert ds.opened is False


# ---------------------------------------------------------------- row 3: typed refusals
def test_stream_leg_unreachable_upstream_is_502_not_503():
    """Row 3: a genuinely unreachable MCP service answers the SAME typed vocabulary the buffered
    forward uses — 502 `upstream unreachable: <Type>`. Never the blanket 503 the 0.10 gateway
    manufactured for a stream that merely outlived a read timeout."""
    ds = StreamingDownstream(raises=httpx.ConnectError("no route to host"))
    client, _ = _client(ds)
    r = client.get("/mcp", headers={**AUTH, **SSE_ACCEPT})
    assert r.status_code == 502
    assert r.json()["detail"] == "upstream unreachable: ConnectError"
    assert ds.opened is False


def test_stream_leg_connect_timeout_is_504():
    ds = StreamingDownstream(raises=httpx.ConnectTimeout("connect timed out"))
    client, _ = _client(ds)
    r = client.get("/mcp", headers={**AUTH, **SSE_ACCEPT})
    assert r.status_code == 504
    assert r.json()["detail"] == "upstream timeout"


# ---------------------------------------------------------------- row 4: no status laundering
def test_stream_leg_carries_downstream_status_verbatim():
    """Row 4: the 0.10 gateway rewrote MCP's `400 Missing session ID` handshake answer to 200. That
    laundering is dead — whatever the MCP service answers is what the caller sees (#698 fail-loud)."""
    ds = StreamingDownstream(
        status_code=400,
        headers={"content-type": "application/json", "mcp-session-id": "sess-9"},
        chunks=[b'{"jsonrpc":"2.0","error":{"code":-32600,"message":"Missing session ID"}}'],
    )
    client, _ = _client(ds)
    r = client.get("/mcp", headers={**AUTH, **SSE_ACCEPT})
    assert r.status_code == 400
    assert b"Missing session ID" in r.content
    assert r.headers["content-type"].startswith("application/json")


# ---------------------------------------------------------------- row 5: transport headers
def test_mcp_session_id_survives_both_legs():
    """Row 5: `mcp-session-id` is how streamable-HTTP binds a client to its session — the client
    must echo it on every later request. Dropping it at the edge breaks the transport."""
    ds = StreamingDownstream(
        headers={"content-type": "text/event-stream", "mcp-session-id": "sess-42",
                 "mcp-protocol-version": "2025-03-26"},
        chunks=[b"data: open\n\n"],
    )
    client, _ = _client(ds)
    with client.stream("GET", "/mcp", headers={**AUTH, **SSE_ACCEPT}) as r:
        list(r.iter_bytes())
        assert r.headers["mcp-session-id"] == "sess-42"
        assert r.headers["mcp-protocol-version"] == "2025-03-26"

    r = client.post("/mcp", headers=AUTH, json={})
    assert r.headers["mcp-session-id"] == "sess-42"

    # and the client's session id travels UP on the next request
    with client.stream("GET", "/mcp", headers={**AUTH, **SSE_ACCEPT, "mcp-session-id": "sess-42"}) as r2:
        list(r2.iter_bytes())
    assert ds.last["headers"]["mcp-session-id"] == "sess-42"


# ---------------------------------------------------------------- row 6: fail-closed auth
def test_mcp_without_credentials_is_401_before_any_downstream_hop():
    client, ds = _client()
    r = client.get("/mcp", headers=SSE_ACCEPT)
    assert r.status_code == 401
    assert r.json()["detail"] == "Missing API key"
    assert ds.opened is False and ds.buffered_calls == []

    r = client.post("/mcp", json={})
    assert r.status_code == 401
    assert ds.buffered_calls == []


def test_mcp_invalid_key_is_401():
    client, ds = _client()
    assert client.get("/mcp", headers={"x-api-key": "nope"}).status_code == 401
    assert client.get("/mcp", headers={"authorization": "Bearer nope"}).status_code == 401
    assert ds.opened is False


def test_mcp_accepts_the_bearer_carrier_the_transport_actually_sends():
    """MCP clients carry the credential as `Authorization: Bearer <key>` (mcp-remote, Claude
    connectors), not `x-api-key`. The edge resolves EITHER to the same Vexa API key, and forwards
    both spellings downstream so the MCP service authorizes identically."""
    ds = StreamingDownstream(chunks=[b"data: open\n\n"])
    client, _ = _client(ds)
    with client.stream("GET", "/mcp", headers={"authorization": f"Bearer {VALID_KEY}", **SSE_ACCEPT}) as r:
        assert r.status_code == 200
        list(r.iter_bytes())
    assert ds.last["headers"]["x-user-id"] == "7"
    assert ds.last["headers"]["x-api-key"] == VALID_KEY
    assert ds.last["headers"]["authorization"] == f"Bearer {VALID_KEY}"


def test_mcp_accepts_a_raw_authorization_value():
    """0.10 back-compat (and the MCP service's own parser): a raw `Authorization: <key>` with no
    scheme is the key itself."""
    ds = StreamingDownstream(buffered_status=200)
    client, _ = _client(ds)
    r = client.post("/mcp", headers={"authorization": VALID_KEY}, json={})
    assert r.status_code == 200
    assert ds.buffered_calls[-1]["headers"]["x-user-id"] == "7"


def test_mcp_client_supplied_user_id_is_stripped_then_reinjected():
    """Anti-spoof parity with every other gateway forward."""
    ds = StreamingDownstream(buffered_status=200)
    client, _ = _client(ds)
    client.post("/mcp", headers={**AUTH, "x-user-id": "999"}, json={})
    assert ds.buffered_calls[-1]["headers"]["x-user-id"] == "7"


# ------------------------------------------------- the agent SSE leg is untouched (row 4 anchor)
def test_agent_sse_still_uses_the_byte_relay_stream_port():
    """The agent chat SSE keeps its own envelope-minting forward (`stream`), unchanged by the
    head-aware `open_stream` the MCP leg introduced."""
    ds = FakeDownstream(stream_chunks=[b'data: {"type":"token","text":"hi"}\n\n'])
    app = create_app(FakeAuthorizer(), ds, FakeRedis(), agent_api_url="http://agent-api")
    client = TestClient(app)
    r = client.post("/agent/chat", headers=AUTH, json={"prompt": "hi"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert '"type":"token"' in r.text


# ------------------------------------------------- the mechanism, against a REAL silent upstream
async def test_real_adapter_stream_leg_survives_a_silent_sse_upstream():
    """#795's mechanism, driven through the REAL ``HttpxDownstreamClient`` against a local socket
    server that answers SSE headers + one frame and then goes silent:

      * the BUFFERED forward (bounded read timeout — correct for request/response legs) raises
        ``ReadTimeout``. ``httpx.TimeoutException ⊂ httpx.RequestError``: that is the exception the
        0.10 gateway mapped to ``503 MCP service unavailable``;
      * the STREAM leg (``read=None`` — a silent stream is a healthy stream) relays the first frame
        immediately and stays open.
    """
    from gateway.adapters import HttpxDownstreamClient

    stop = asyncio.Event()

    async def handle(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                     b"Transfer-Encoding: chunked\r\n\r\n")
        frame = b"data: open\n\n"
        writer.write(hex(len(frame))[2:].encode() + b"\r\n" + frame + b"\r\n")
        try:
            await writer.drain()
            await stop.wait()  # silence — the stream never completes on its own
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            try:
                writer.close()  # else Server.wait_closed() blocks on the open connection
            except Exception:
                pass

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}/mcp"

    buffered = httpx.AsyncClient(timeout=1.0)
    streaming = httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=None, write=5.0, pool=5.0))
    client = HttpxDownstreamClient(buffered, stream_client=streaming)
    try:
        with pytest.raises(httpx.RequestError) as ei:
            await client.request("GET", url)
        assert isinstance(ei.value, httpx.TimeoutException)  # → the 0.10 `503` mapping

        async with client.open_stream("GET", url) as resp:
            assert resp.status_code == 200
            async for chunk in resp.aiter_bytes():
                assert b"data: open" in chunk
                break
    finally:
        await buffered.aclose()
        await streaming.aclose()
        stop.set()
        server.close()
        await server.wait_closed()
