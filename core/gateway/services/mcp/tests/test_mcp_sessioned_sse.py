"""#921 — sessioned GET /mcp must start the SSE response (headers + keep-alive ping).

Asserts at the ASGI ``send`` boundary (the same altitude as the original measurement:
uvicorn logs at ``http.response.start``). httpx's ASGI transport does not reliably
surface headers for a never-finishing EventSourceResponse, so we drive the app as
ASGI and capture ``send`` messages directly.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Dict, List

import httpx
import pytest

from vexa_mcp import create_app

JsonHeaders = Dict[str, str]


@pytest.fixture
def app():
    return create_app(
        "http://gateway.test",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
    )


async def _asgi_exchange(
    app,
    method: str,
    path: str,
    headers: JsonHeaders,
    body: bytes = b"",
    *,
    hold_seconds: float = 0.3,
) -> List[dict]:
    """Run one HTTP exchange; for long-lived streams, hold then disconnect."""
    messages: List[dict] = []
    body_sent = False
    disconnect = asyncio.Event()

    async def receive() -> dict:
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }
    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.sleep(hold_seconds)
        if task.done():
            await task
            return messages
        disconnect.set()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        return messages
    finally:
        if not task.done():
            disconnect.set()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


def _header_map(start_msg: dict) -> Dict[str, str]:
    return {k.decode().lower(): v.decode() for k, v in start_msg.get("headers", [])}


def _first_start(messages: List[dict]) -> dict:
    for m in messages:
        if m["type"] == "http.response.start":
            return m
    raise AssertionError(f"no http.response.start in {messages!r}")


async def _handshake(app) -> str:
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
    ).encode()
    msgs = await _asgi_exchange(
        app,
        "POST",
        "/mcp",
        {
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
        },
        body,
    )
    start = _first_start(msgs)
    assert start["status"] == 200
    sid = _header_map(start).get("mcp-session-id")
    assert sid, start
    await _asgi_exchange(
        app,
        "POST",
        "/mcp",
        {
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            "mcp-session-id": sid,
        },
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode(),
    )
    return sid


@pytest.mark.asyncio
async def test_sessionless_get_still_400(app):
    """Negative control: sessionless GET still returns MCP's own 400 promptly (#920)."""
    msgs = await _asgi_exchange(app, "GET", "/mcp", {"accept": "text/event-stream"})
    start = _first_start(msgs)
    assert start["status"] == 400


@pytest.mark.asyncio
async def test_sessioned_get_starts_sse_headers_promptly(app):
    """A valid mcp-session-id GET must emit status + text/event-stream before any push."""
    sid = await _handshake(app)
    msgs = await _asgi_exchange(
        app,
        "GET",
        "/mcp",
        {"accept": "text/event-stream", "mcp-session-id": sid},
        hold_seconds=0.5,
    )
    start = _first_start(msgs)
    assert start["status"] == 200
    headers = _header_map(start)
    assert "text/event-stream" in headers.get("content-type", "")
    assert headers.get("mcp-session-id") == sid


@pytest.mark.asyncio
async def test_sessioned_get_idle_ping(app):
    """sse-starlette keep-alive comment must appear on an idle sessioned stream (~15s)."""
    sid = await _handshake(app)
    msgs = await _asgi_exchange(
        app,
        "GET",
        "/mcp",
        {"accept": "text/event-stream", "mcp-session-id": sid},
        hold_seconds=16.0,
    )
    start = _first_start(msgs)
    assert start["status"] == 200
    bodies = [m.get("body", b"") for m in msgs if m["type"] == "http.response.body"]
    joined = b"".join(bodies)
    assert b"ping" in joined.lower(), f"expected keep-alive ping in {bodies!r}"
