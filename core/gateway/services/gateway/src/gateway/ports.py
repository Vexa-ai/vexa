"""Ports (Protocols) — the seams that let the SAME ``create_app`` run with real adapters in
production and injected fakes in tests.

The deployed gateway (``services/api-gateway/main.py``) talks to three collaborators:

  * the admin-api ``/internal/validate`` endpoint, to resolve ``x-api-key`` → user/scopes
    (``main._resolve_token``),
  * the downstream services (meeting-api / transcription-collector), which it proxies to
    verbatim (``main.forward_request``),
  * redis pub/sub, for the ``/ws`` multiplex fan-in (``main.websocket_multiplex``).

Each is expressed here as a ``typing.Protocol`` so the app depends on the BEHAVIOR, not a
concrete client. ``adapters.py`` supplies the production implementations (httpx.AsyncClient,
redis.asyncio); the conformance harness supplies in-process fakes (fake admin-api, port-fake
downstream, FakeRedis). Both satisfy these Protocols structurally — no inheritance required.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Optional, Protocol, runtime_checkable


class AuthUnavailable(Exception):
    """The authentication infrastructure could not be reached or answered a fault (#495).

    ``resolve`` raises this — as distinct from returning ``None`` — when it CANNOT DETERMINE
    whether the key is valid: the admin-api validation hop timed out, failed at the transport
    layer, or answered 5xx. ``None`` means the opposite: admin-api answered and the key is
    genuinely invalid. The app maps this exception to ``503`` (retry), NEVER to
    ``401 Invalid API key`` — telling a caller with a valid key that their key is bad, because
    OUR auth path is slow or down, is the #483/#495 failure this seam exists to prevent.
    """


@runtime_checkable
class Authorizer(Protocol):
    """Resolve identity + subscribe-authorization for the caller's ``x-api-key``.

    Two methods, matching the two authz hops in ``main.py``:

      * ``resolve(api_key)`` — mirrors ``main._resolve_token`` (admin-api ``/internal/validate``):
        a non-None result is a dict carrying at least ``user_id`` and ``scopes`` (and optionally
        ``max_concurrent``, ``email``, webhook config). A ``None`` return is the fail-closed
        signal for a GENUINELY INVALID key — the REST app rejects with 401. When the validation
        hop itself is unreachable/faulted, ``resolve`` raises ``AuthUnavailable`` instead (→ 503),
        so an infra failure is never reported to the caller as a bad key (#495).

      * ``authorize_subscribe(api_key, meetings)`` — mirrors the ``/ws`` subscribe hop to
        transcription-collector's ``/ws/authorize-subscribe`` (main.py:2257-2271): returns
        ``{"authorized": [{platform, native_id, user_id, meeting_id}, ...], "errors": [...]}``
        so the multiplex knows which redis channels to fan in.
    """

    async def resolve(self, api_key: str) -> Optional[dict]:
        ...

    async def authorize_subscribe(self, api_key: str, meetings: list) -> dict:
        ...


@runtime_checkable
class DownstreamResponse(Protocol):
    """The minimal shape the app reads back from a downstream call: status + body bytes +
    headers (the app returns the body verbatim, ``main.forward_request``)."""

    @property
    def status_code(self) -> int: ...

    @property
    def content(self) -> bytes: ...

    @property
    def headers(self) -> Any: ...


@runtime_checkable
class DownstreamClient(Protocol):
    """Forward an HTTP request to a downstream service (meeting-api / transcription-collector)
    and return its response. Mirrors the ``client.request(...)`` call in ``main.forward_request``.
    """

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[dict] = None,
        params: Optional[dict] = None,
        content: Optional[bytes] = None,
    ) -> DownstreamResponse:
        ...

    def stream(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[dict] = None,
        params: Optional[dict] = None,
        content: Optional[bytes] = None,
    ) -> AsyncIterator[bytes]:
        """Forward a request and yield the downstream response body as it arrives (the SSE path —
        agent chat). An async generator: ``async for chunk in downstream.stream(...)``. Used so a
        streamed turn is relayed token-by-token instead of buffered."""
        ...


@runtime_checkable
class PubSub(Protocol):
    """A redis-style pub/sub subscription used by the ``/ws`` fan-in (``main.fan_in``)."""

    async def subscribe(self, *channels: str) -> None: ...

    async def unsubscribe(self, *channels: str) -> None: ...

    async def close(self) -> None: ...

    def listen(self) -> AsyncIterator[dict]:
        """Yield ``{"type": "message"|"subscribe", "data": <str>}`` dicts (redis-py shape)."""
        ...


@runtime_checkable
class RedisBus(Protocol):
    """The pub/sub bus the ``/ws`` multiplex fans in from. ``pubsub()`` returns a fresh
    subscription; the app subscribes to ``tc:…:mutable`` / ``bm:…:status`` / ``va:…:chat`` and
    forwards every raw payload to the socket (``main.fan_in`` — main.py:2195-2212)."""

    def pubsub(self) -> PubSub: ...
