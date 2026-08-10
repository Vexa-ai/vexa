"""ASGI passthrough for the MCP streamable-HTTP mount (#921).

``fastapi-mcp`` 0.4.x's ``FastApiHttpSessionManager.handle_fastapi_request`` captures
the upstream ASGI ``send`` into an in-memory buffer and only returns a FastAPI
``Response`` after ``handle_request`` completes. That works for short JSON answers
(initialize, sessionless 400) but deadlocks a sessioned ``GET /mcp``: the MCP SDK
builds an open ``EventSourceResponse`` that never finishes, so headers never leave
the buffer — uvicorn never sees ``http.response.start``, and sse-starlette's keep-alive
ping never fires.

Fix at the point of introduction: relay the real ASGI ``send`` so the stream can
start immediately. We keep ``FastApiMCP`` for tool derivation; only the HTTP
request adapter is replaced.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi_mcp.transport.http import FastApiHttpSessionManager
from starlette.types import Receive, Scope, Send

logger = logging.getLogger(__name__)


class _ASGIPassthroughResponse(Response):
    """A Response whose body is the ASGI app itself (not a buffered byte string)."""

    def __init__(self, asgi_call: Any, scope: Scope, receive: Receive) -> None:
        # status/headers are owned by the upstream ASGI callable — placeholders only.
        super().__init__(content=b"", status_code=200)
        self._asgi_call = asgi_call
        self._scope = scope
        self._receive = receive

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Use Starlette's real ``send`` so ``http.response.start`` reaches the server.
        await self._asgi_call(self._scope, self._receive, send)


async def handle_fastapi_request_streaming(
    transport: FastApiHttpSessionManager, request: Request
) -> Response:
    """Drop-in replacement for ``FastApiHttpSessionManager.handle_fastapi_request``."""
    await transport._ensure_session_manager_started()
    if not transport._session_manager:
        raise HTTPException(status_code=500, detail="Session manager not initialized")

    logger.debug("Handling MCP streamable-HTTP (ASGI passthrough): %s %s",
                 request.method, request.url.path)
    return _ASGIPassthroughResponse(
        transport._session_manager.handle_request,
        request.scope,
        request.receive,
    )


def install_streaming_http_transport(mcp: Any) -> None:
    """Patch the mounted FastApiMCP HTTP transport so sessioned GET SSE can start (#921)."""
    http_transport = getattr(mcp, "_http_transport", None)
    if http_transport is None:
        raise RuntimeError("FastApiMCP has no _http_transport — call mount_http() first")

    async def _handle(request: Request) -> Response:
        return await handle_fastapi_request_streaming(http_transport, request)

    http_transport.handle_fastapi_request = _handle  # type: ignore[method-assign]
