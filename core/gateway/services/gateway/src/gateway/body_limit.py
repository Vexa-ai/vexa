"""Request-body size limit at the edge.

Every JSON route behind this gateway takes small bodies (bot requests, chat turns, settings).
Unbounded, a 12 MB ``POST /bots`` body was read whole, forwarded to meeting-api, and failed
only in the runtime (a 502 after a container was already created). The edge refuses an
over-size body with a typed **413** before anything downstream sees it.

The cap applies to structured bodies only: JSON, text, url-encoded forms, or a body that names no
type. File bodies (multipart uploads, a `meeting-bundle.v1` zip on `POST /meetings/import`, audio)
are the routes built to take large bodies, and the services behind them bound them.
"""

from __future__ import annotations

import json

#: The largest structured request body the edge accepts.
MAX_BODY_BYTES = 1024 * 1024


def _capped(content_type: bytes) -> bool:
    """Whether a body of this Content-Type is held to MAX_BODY_BYTES (structured, not a file)."""
    media = content_type.split(b";", 1)[0].strip().lower()
    return (not media or media.startswith(b"text/") or media.endswith(b"json")
            or media == b"application/x-www-form-urlencoded")


def _too_large_body() -> bytes:
    return json.dumps({"detail": f"request body is larger than {MAX_BODY_BYTES} bytes"}).encode()


class BodyLimitMiddleware:
    """Pure ASGI: refuse on a declared Content-Length, and count streamed bytes otherwise."""

    def __init__(self, app, max_bytes: int = MAX_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.lower(): v for k, v in scope.get("headers") or []}
        if not _capped(headers.get(b"content-type", b"")):
            return await self.app(scope, receive, send)
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    return await self._refuse(send)
            except ValueError:
                pass

        # No length declared (chunked): count as the body streams. Past the limit the edge answers
        # 413 itself and tells the route the client went away, so nothing is forwarded; whatever
        # the route then tries to send is dropped.
        seen = 0
        started = False
        refused = False

        async def counting_receive():
            nonlocal seen, refused
            if refused:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    refused = True
                    if not started:
                        await self._refuse(send)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            nonlocal started
            if refused:
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except Exception:
            if not refused:
                raise

    async def _refuse(self, send):
        body = _too_large_body()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

