"""voice_relay.py — the edge's WebSocket relay for a voice note's audio (#1783).

``WS /voice-notes/{native_meeting_id}/stream`` is the meetings domain's socket: the person's browser
streams capture.v1 audio frames to meeting-api, which owns the note and its stream. The edge does for
it what it does for every forward, once, at the upgrade: resolve the caller's key, refuse a key the
row's scopes do not admit (the row is declared as the upgrade's ``GET`` in
``core/meetings/routes.v1.json``), refuse a worker's delegation token (the row does not admit one),
and sign the identity it resolved onto the hop (gateway-identity.v1). After that it relays frames
both ways, verbatim, and passes either side's close code to the other.

Refusals mirror ``/ws``: the socket is accepted, an ``{"type": "error", "error": <code>}`` frame is
sent, then the close — 4401 (no key, or a key this edge does not accept), 4403 (the key holds none
of the row's scopes), 4429 (the caller's per-user request budget is spent — the same limiter every
forwarded request spends from; one socket open costs one request), 4503 (the identity service could not be reached; retry), 4502 (meeting-api
could not be reached).

The upstream dial is a port (``SocketDialer``) so the relay is driven offline in tests; production
dials with the ``websockets`` client (``adapters.WebsocketsDialer``).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncContextManager, Callable, Mapping, Optional, Protocol, Union, runtime_checkable

from fastapi import WebSocket, WebSocketDisconnect

from .delegation import is_delegated
from .obs import log_event, set_user_id
from .ports import AuthUnavailable, Authorizer

PATH = "/voice-notes/{native_meeting_id}/stream"
ROW = ("GET", PATH)
#: The largest frame the relay carries either way: one second of 16 kHz Float32 audio and its header,
#: with room to spare. meeting-api refuses anything longer than a second itself.
MAX_FRAME_BYTES = 128 * 1024

Frame = Union[bytes, str]


@runtime_checkable
class UpstreamSocket(Protocol):
    async def send(self, data: Frame) -> None: ...

    async def recv(self) -> Frame:
        """The next frame; raises ``UpstreamClosed`` when the upstream closed."""
        ...

    async def close(self, code: int = 1000) -> None: ...


class UpstreamClosed(Exception):
    def __init__(self, code: Optional[int]) -> None:
        super().__init__(f"upstream closed ({code})")
        self.code = code


@runtime_checkable
class SocketDialer(Protocol):
    def connect(self, url: str, headers: Mapping[str, str]) -> AsyncContextManager[UpstreamSocket]: ...


def _client_key(ws: WebSocket) -> Optional[str]:
    key = ws.headers.get("x-api-key")
    if key:
        return key
    auth = (ws.headers.get("authorization") or "").strip()
    scheme, _, token = auth.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return None


async def _refuse(ws: WebSocket, error: str, code: int) -> None:
    try:
        await ws.send_text(json.dumps({"type": "error", "error": error}))
    finally:
        await ws.close(code=code)


def _close_code(code: Optional[int]) -> int:
    # Only an application close code (1000, 3000-4999) can be sent; anything else becomes 1000.
    return code if code is not None and (code == 1000 or 3000 <= code <= 4999) else 1000


async def run_voice_note_relay(
    ws: WebSocket,
    native_meeting_id: str,
    *,
    authorizer: Authorizer,
    route_scopes: Mapping,
    sign_headers: Callable[[dict], dict],
    dialer: SocketDialer,
    upstream_base: str,
    rate_limiter: Optional[Any] = None,
) -> None:
    await ws.accept()
    api_key = _client_key(ws)
    if not api_key:
        return await _refuse(ws, "missing_api_key", 4401)
    try:
        user_data = await authorizer.resolve(api_key)
    except AuthUnavailable as e:
        log_event("auth_infra_unavailable", audience="system", level="error", span="voice-note",
                  fields={"reason": type(e).__name__})
        return await _refuse(ws, "auth_unavailable", 4503)
    if not user_data or is_delegated(api_key, user_data):
        return await _refuse(ws, "invalid_api_key", 4401)
    user_id = user_data["user_id"]
    set_user_id(user_id)
    if rate_limiter is not None and not rate_limiter.allow(str(user_id)):
        log_event("request_rate_limited", audience="user", level="warning", span="voice-note",
                  user_id=user_id, fields={"path": PATH})
        return await _refuse(ws, "rate_limited", 4429)
    required = route_scopes.get(ROW) or frozenset()
    if not set(user_data.get("scopes") or []) & set(required):
        log_event("request_denied_scope", audience="user", level="warning", span="voice-note",
                  user_id=user_id, fields={"path": PATH, "required": sorted(required)})
        return await _refuse(ws, "insufficient_scope", 4403)

    url = upstream_base.rstrip("/").replace("http://", "ws://", 1).replace("https://", "wss://", 1) \
        + PATH.replace("{native_meeting_id}", native_meeting_id)
    headers = sign_headers(user_data)
    log_event("request_accepted", audience="user", span="voice-note", user_id=user_id,
              fields={"path": PATH})
    try:
        async with dialer.connect(url, headers) as upstream:
            await _pump(ws, upstream)
    except (OSError, asyncio.TimeoutError, ConnectionError) as e:
        log_event("voice_note_upstream_unreachable", audience="system", level="error",
                  span="voice-note", user_id=user_id, fields={"error": type(e).__name__})
        try:
            await _refuse(ws, "upstream_unavailable", 4502)
        except Exception:  # noqa: BLE001 — the client may already be gone
            pass


async def _pump(ws: WebSocket, upstream: UpstreamSocket) -> None:
    """Relay frames both ways until one side closes, then close the other with its code."""
    done = asyncio.Event()
    outcome: dict[str, Any] = {}

    async def client_to_upstream() -> None:
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    outcome["client_closed"] = msg.get("code")
                    return
                data = msg.get("bytes")
                if data is None:
                    data = msg.get("text")
                if data is None:
                    continue
                if len(data) > MAX_FRAME_BYTES:
                    outcome["refuse"] = ("frame_too_large", 4400)
                    return
                await upstream.send(data)
        except WebSocketDisconnect as e:
            outcome["client_closed"] = e.code
        finally:
            done.set()

    async def upstream_to_client() -> None:
        try:
            while True:
                data = await upstream.recv()
                if isinstance(data, (bytes, bytearray)):
                    await ws.send_bytes(bytes(data))
                else:
                    await ws.send_text(data)
        except UpstreamClosed as e:
            outcome["upstream_closed"] = e.code
        except Exception:  # noqa: BLE001 — the client went away mid-send
            outcome.setdefault("client_closed", None)
        finally:
            done.set()

    tasks = [asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())]
    await done.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    if "refuse" in outcome:
        error, code = outcome["refuse"]
        await upstream.close(1000)
        try:
            await _refuse(ws, error, code)
        except Exception:  # noqa: BLE001
            pass
        return
    if "upstream_closed" in outcome:
        try:
            await ws.close(code=_close_code(outcome["upstream_closed"]))
        except Exception:  # noqa: BLE001
            pass
        return
    await upstream.close(_close_code(outcome.get("client_closed")))
