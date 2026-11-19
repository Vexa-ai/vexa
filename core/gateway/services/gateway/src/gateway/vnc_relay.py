"""vnc_relay.py — the live view of a bot-account sign-in session, relayed to meeting-api (ADR-0049).

A person signs a bot account in by driving a short-lived browser through noVNC in the page. The
browser's websocket opens ``/bot-accounts/sessions/{session_id}/vnc`` at this edge; the edge
resolves the caller exactly as ``/ws`` does (``ws_auth.ws_credential``: the ``x-api-key`` header,
or the ``vexa.key.<key>`` subprotocol a browser socket can carry; a key in the URL is refused), holds the key to the scopes of the session's own REST row
(``GET /bot-accounts/sessions/{session_id}`` in ``core/meetings/routes.v1.json``), and then opens
a websocket to the SAME path on meeting-api carrying the SAME identity headers an HTTP forward
stamps — the gateway-identity.v1 claims and their ``X-Vexa-Identity`` signature. meeting-api
decides whether the caller owns the session (it refuses another person's with 404 / close 4404)
and relays on to the workload's websockify. This module only moves frames.

    no key, a key this edge does not accept,     ->  accept, close 4401
      or a worker's delegation token
    auth infrastructure unreachable              ->  accept, close 4503 (retry), as /ws
    a key without the row's scope                ->  accept, close 4403
    a session id that is not 32 hex              ->  accept, close 4404
    meeting-api refuses the upgrade              ->  accept, close 4404 for its 404, 4401/4403 for
                                                     401/403, 1011 otherwise
    meeting-api unreachable                      ->  accept, close 1011

Frames go both ways verbatim (binary stays binary, text stays text) until either side closes; the
other side is then closed too, with the upstream's close code when it was the one that closed.
Nothing here logs frame contents: a VNC stream carries the person's sign-in keystrokes.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
from typing import Any, Callable, Mapping, Optional

from fastapi import WebSocket

from . import identity_token
from .delegation import is_delegated
from .obs import TRACE_HEADER, get_trace_id, log_event, set_user_id
from .ports import Authorizer, AuthUnavailable
from .changed import QUERY_KEY_REFUSAL
from .ws_auth import accept, requested_subprotocols, without_vexa, ws_credential

#: The REST row whose scopes the socket is held to: whoever may read the session may watch it.
SESSION_ROUTE = ("GET", "/bot-accounts/sessions/{session_id}")
PATH = "/bot-accounts/sessions/{session_id}/vnc"
_SESSION_ID = re.compile(r"^[a-f0-9]{32}$")

#: ``connect(url, headers, subprotocols)`` -> an async context manager yielding an upstream
#: connection: ``await conn.send(str | bytes)``, ``async for frame in conn`` (str | bytes), and
#: ``conn.subprotocol`` / ``conn.close_code``. The default is the ``websockets`` client; tests
#: inject a fake.
Connector = Callable[[str, Mapping[str, str], list], Any]


class UpstreamRefused(Exception):
    """meeting-api answered the upgrade with an HTTP status instead of switching protocols."""

    def __init__(self, status: int):
        super().__init__(status)
        self.status = status


@contextlib.asynccontextmanager
async def websockets_connect(url: str, headers: Mapping[str, str], subprotocols: list):
    """The production connector: the ``websockets`` asyncio client, frames unbounded in size
    (a framebuffer update is routinely larger than its 1 MiB default)."""
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidHandshake, InvalidStatus

    try:
        conn = await connect(url, additional_headers=dict(headers), max_size=None,
                             subprotocols=list(subprotocols) or None, open_timeout=10)
    except InvalidStatus as e:
        raise UpstreamRefused(e.response.status_code) from None
    except InvalidHandshake as e:
        raise ConnectionError(type(e).__name__) from None
    try:
        yield conn
    finally:
        await conn.close()


def upstream_url(meeting_api_url: str, session_id: str) -> str:
    base = meeting_api_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return base + PATH.replace("{session_id}", session_id)


def _refusal_code(status: int) -> int:
    return {401: 4401, 403: 4403, 404: 4404}.get(status, 1011)


async def _refuse(ws: WebSocket, code: int, error: "str | dict") -> None:
    with contextlib.suppress(Exception):  # already accepted when the relay itself failed
        await accept(ws, ws_credential(ws).subprotocol)
    with contextlib.suppress(Exception):
        await ws.send_json(error if isinstance(error, dict) else {"type": "error", "error": error})
    with contextlib.suppress(Exception):
        await ws.close(code=code)


async def run_vnc_relay(ws: WebSocket, session_id: str, *, authorizer: Authorizer,
                        route_scopes: Mapping, meeting_api_url: str, identity_key=None,
                        connect: Optional[Connector] = None) -> None:
    credential = ws_credential(ws)
    # A key in the URL is refused WHATEVER ELSE the request carries (a header or subprotocol key
    # does not make a URL key acceptable here): the URL lands in logs and browser history.
    if "api_key" in ws.query_params or credential.source == "query":
        log_event("ws_query_key_refused", audience="system", level="warning", span="ws",
                  fields={"path": PATH})
        return await _refuse(ws, 4401, QUERY_KEY_REFUSAL)
    api_key = credential.key
    if not api_key:
        return await _refuse(ws, 4401, "missing_api_key")
    try:
        user_data = await authorizer.resolve(api_key)
    except AuthUnavailable as e:
        log_event("auth_infra_unavailable", audience="system", level="error", span="ws",
                  fields={"reason": type(e).__name__, "detail": str(e)})
        return await _refuse(ws, 4503, "auth_unavailable")
    if not user_data or is_delegated(api_key, user_data):
        return await _refuse(ws, 4401, "invalid_api_key")
    user_id = user_data["user_id"]
    set_user_id(user_id)

    required = frozenset(route_scopes.get(SESSION_ROUTE, frozenset()))
    held = {str(s) for s in (user_data.get("scopes") or [])}
    if not required or not held & required:
        log_event("request_denied_scope", audience="user", level="warning", span="ws",
                  user_id=user_id, fields={"path": PATH, "required": sorted(required)})
        return await _refuse(ws, 4403, "insufficient_scope")
    if not _SESSION_ID.match(session_id or ""):
        return await _refuse(ws, 4404, "not_found")

    # The identity a forward stamps (`app._authorize`), and nothing the client sent.
    headers = {"x-api-key": api_key}
    if identity_key is not None:
        headers.update(identity_token.signed_headers(identity_key, user_data))
    else:
        headers.update(identity_token.headers_from_claims(
            identity_token.claims_from_validation(user_data)))
    headers[TRACE_HEADER] = get_trace_id() or ""

    url = upstream_url(meeting_api_url, session_id)
    # ws.v1's own entries (the key above all) never travel upstream; the viewer's own (noVNC's
    # `binary`) do, and the answer is upstream's choice — or `vexa.v1` when the viewer offered only it.
    requested = without_vexa(requested_subprotocols(ws))
    opener = connect or websockets_connect
    try:
        async with opener(url, headers, requested) as upstream:
            await accept(ws, getattr(upstream, "subprotocol", None) or credential.subprotocol)
            log_event("vnc_relay_opened", audience="system", level="debug", span="ws",
                      user_id=user_id, fields={"path": PATH})
            code = await _pump(ws, upstream)
        with contextlib.suppress(Exception):
            await ws.close(code=code)
    except UpstreamRefused as e:
        log_event("vnc_relay_refused", audience="system", level="info", span="ws",
                  user_id=user_id, fields={"path": PATH, "downstream_status": e.status})
        await _refuse(ws, _refusal_code(e.status), "refused")
    except (OSError, asyncio.TimeoutError) as e:
        log_event("vnc_relay_unreachable", audience="system", level="error", span="ws",
                  user_id=user_id, fields={"path": PATH, "reason": type(e).__name__})
        await _refuse(ws, 1011, "upstream_unreachable")


async def _pump(ws: WebSocket, upstream) -> int:
    """Relay both ways until either side ends; the close code to answer the client with."""

    async def client_to_upstream() -> None:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("bytes") is not None:
                await upstream.send(message["bytes"])
            elif message.get("text") is not None:
                await upstream.send(message["text"])

    async def upstream_to_client() -> None:
        async for frame in upstream:
            if isinstance(frame, (bytes, bytearray, memoryview)):
                await ws.send_bytes(bytes(frame))
            else:
                await ws.send_text(frame)

    down = asyncio.ensure_future(upstream_to_client())
    up = asyncio.ensure_future(client_to_upstream())
    done, pending = await asyncio.wait({down, up}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in pending | done:
        with contextlib.suppress(BaseException):
            await task
    code = getattr(upstream, "close_code", None) if down in done else None
    return code if isinstance(code, int) and (code == 1000 or 3000 <= code <= 4999) else 1000
