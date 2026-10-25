"""ws_auth.py — where a websocket's API key comes from (ws.v1 § Connect).

A browser's `WebSocket` cannot set a header, so ws.v1 offers it the SUBPROTOCOL list instead:

    new WebSocket("wss://<host>/ws", ["vexa.v1", "vexa.key." + key])

The key then travels in `Sec-WebSocket-Protocol`, a request header — never in the URL, so no access
log, proxy log or browser history holds it. The edge answers with `vexa.v1` (never with the key's
own entry). The order a key is taken in:

  1. the `x-api-key` header (servers, the terminal's own proxy);
  2. a `vexa.key.<key>` subprotocol entry (browsers).

A key in the URL (`?api_key=`) is never a credential: the socket is refused with
`changed.QUERY_KEY_REFUSAL` and close 4401, whatever else it carries (log_scrub.py keeps the value
out of the access log). A key whose characters cannot form a subprotocol token uses the header.
"""
from __future__ import annotations

from typing import Iterable, NamedTuple, Optional

#: What a client names to speak ws.v1 with the key in the subprotocol list, and what the edge answers.
SUBPROTOCOL = "vexa.v1"
#: The subprotocol entry that carries the key.
KEY_SUBPROTOCOL_PREFIX = "vexa.key."


class WsCredential(NamedTuple):
    key: Optional[str]
    #: the subprotocol the edge accepts with (`vexa.v1` when the client offered it), or None
    subprotocol: Optional[str]
    #: "header" | "subprotocol" | "query" (a key in the URL: refused, `key` is None) | None
    source: Optional[str]


def offered(scope_subprotocols: Optional[Iterable[str]]) -> list:
    return [str(p).strip() for p in (scope_subprotocols or []) if str(p).strip()]


def without_vexa(protocols: Iterable[str]) -> list:
    """The client's subprotocols minus ws.v1's own (what a relay may pass on upstream)."""
    return [p for p in protocols if p != SUBPROTOCOL and not p.startswith(KEY_SUBPROTOCOL_PREFIX)]


async def accept(ws, subprotocol: Optional[str]) -> None:
    """Accept, answering `subprotocol` when there is one."""
    if subprotocol:
        await ws.accept(subprotocol=subprotocol)
    else:
        await ws.accept()


def requested_subprotocols(ws) -> list:
    scope = getattr(ws, "scope", None)
    return offered(scope.get("subprotocols") if isinstance(scope, dict) else None)


def ws_credential(ws) -> WsCredential:
    protocols = requested_subprotocols(ws)
    answer = SUBPROTOCOL if SUBPROTOCOL in protocols else None
    if "api_key" in ws.query_params:
        return WsCredential(None, answer, "query")
    header = ws.headers.get("x-api-key")
    if header:
        return WsCredential(header, answer, "header")
    for p in protocols:
        if p.startswith(KEY_SUBPROTOCOL_PREFIX) and len(p) > len(KEY_SUBPROTOCOL_PREFIX):
            return WsCredential(p[len(KEY_SUBPROTOCOL_PREFIX):], answer, "subprotocol")
    return WsCredential(None, answer, None)
