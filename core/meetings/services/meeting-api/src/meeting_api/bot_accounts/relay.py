"""The live view's two hops into a browser-session workload: its noVNC web client and its websockify.

The workload serves ``websockify --web /usr/share/novnc 6080 localhost:5900`` on the bots network:
``http://<host>:6080/<path>`` is noVNC's own files and ``ws://<host>:6080/websockify`` the RFB
stream. meeting-api relays both, for the session's owner only; the browser never reaches the
workload directly.

``VncUpstream`` is the port (``fetch`` · ``probe`` · ``connect``); ``HttpVncUpstream`` the adapter —
httpx for the files, the ``websockets`` library for the socket (in the production group through
``uvicorn[standard]``, imported lazily, so the test venv needs neither).

``view_path_refusal`` is the one rule for which paths the view proxy forwards: a relative path of
plain segments (no ``..``, ``.``, empty segment, backslash, ``%``, query or control character),
either a top-level noVNC page or a file under noVNC's own directories.
"""
from __future__ import annotations

import re
from typing import Any, Optional, Protocol

VNC_PORT = 6080
#: The noVNC client's own directories under /usr/share/novnc.
VIEW_DIRS = ("app", "core", "vendor", "include", "utils")
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TOP_LEVEL = re.compile(r"^[A-Za-z0-9_-]+\.(html|json|ico|png|svg)$")


def view_path_refusal(path: str) -> Optional[str]:
    """Why ``path`` is not one of the noVNC client's files, or ``None`` when it is."""
    if not isinstance(path, str) or not path:
        return "names no file"
    if len(path) > 512:
        return "is too long"
    if path.startswith("/") or "\\" in path or "%" in path or "?" in path or "#" in path:
        return "is not a plain relative path"
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return "has an empty, '.' or '..' segment"
    if not all(_SEGMENT.match(p) for p in parts):
        return "has a segment outside [A-Za-z0-9._-]"
    if len(parts) == 1:
        return None if _TOP_LEVEL.match(parts[0]) else "is not a noVNC page"
    return None if parts[0] in VIEW_DIRS else "is outside the noVNC client's directories"


class UpstreamSocket(Protocol):
    async def send(self, data: "bytes | str") -> None: ...
    async def recv(self) -> "bytes | str": ...
    async def close(self) -> None: ...


class VncUpstream(Protocol):
    async def fetch(self, url: str) -> tuple[int, str, bytes]:
        """``(status, content-type, body)`` of a GET."""
        ...

    async def probe(self, url: str) -> bool:
        """Does anything answer HTTP at ``url``?"""
        ...

    async def connect(self, url: str, *, subprotocols: list[str]) -> tuple[UpstreamSocket, Optional[str]]:
        """Open the upstream socket; returns it and the subprotocol it agreed."""
        ...


class _WebsocketsSocket:
    def __init__(self, ws: Any):
        self._ws = ws

    async def send(self, data):
        await self._ws.send(data)

    async def recv(self):
        return await self._ws.recv()

    async def close(self):
        try:
            await self._ws.close()
        except Exception:  # noqa: BLE001 — closing a dead socket is not a fault
            pass


class HttpVncUpstream:
    """``VncUpstream`` over httpx and ``websockets``."""

    #: The largest noVNC file meeting-api relays (its biggest bundle is well under this).
    MAX_FILE_BYTES = 8 * 1024 * 1024

    async def fetch(self, url):
        import httpx

        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            r = await client.get(url)
        body = r.content[: self.MAX_FILE_BYTES + 1]
        if len(body) > self.MAX_FILE_BYTES:
            return 502, "text/plain", b""
        return r.status_code, r.headers.get("content-type") or "application/octet-stream", body

    async def probe(self, url):
        import httpx

        try:
            async with httpx.AsyncClient(timeout=2.0, follow_redirects=False) as client:
                r = await client.get(url)
            return r.status_code < 500
        except Exception:  # noqa: BLE001 — not answering yet is the answer
            return False

    async def connect(self, url, *, subprotocols):
        try:
            from websockets.asyncio.client import connect
        except ImportError:  # websockets < 13
            from websockets import connect  # type: ignore[no-redef]

        ws = await connect(url, subprotocols=subprotocols or None, max_size=None, open_timeout=10)
        return _WebsocketsSocket(ws), getattr(ws, "subprotocol", None)


class FakeVncUpstream:
    """``VncUpstream`` for tests: ``files`` maps URL → (status, content-type, body); ``ready`` is what
    ``probe`` answers; ``connect`` returns an echo socket that also records what it was sent."""

    def __init__(self, files: Optional[dict] = None, *, ready: bool = True,
                 greeting: bytes = b"RFB 003.008\n"):
        self.files = dict(files or {})
        self.ready = ready
        self.greeting = greeting
        self.fetched: list[str] = []
        self.connected: list[str] = []
        self.sockets: list["FakeUpstreamSocket"] = []

    async def fetch(self, url):
        self.fetched.append(url)
        return self.files.get(url, (404, "text/plain", b"not found"))

    async def probe(self, url):
        return self.ready

    async def connect(self, url, *, subprotocols):
        self.connected.append(url)
        sock = FakeUpstreamSocket(self.greeting)
        self.sockets.append(sock)
        return sock, (subprotocols[0] if subprotocols else None)


class FakeUpstreamSocket:
    def __init__(self, greeting: bytes):
        import asyncio

        self._queue: "asyncio.Queue" = asyncio.Queue()
        self._queue.put_nowait(greeting)
        self.sent: list = []
        self.closed = False

    async def send(self, data):
        self.sent.append(data)
        await self._queue.put(b"echo:" + (data if isinstance(data, bytes) else data.encode()))

    async def recv(self):
        item = await self._queue.get()
        if item is None:
            raise ConnectionError("closed")
        return item

    async def close(self):
        self.closed = True
        await self._queue.put(None)
