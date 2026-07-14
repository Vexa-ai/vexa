"""Production adapters — the real implementations of the ``ports.py`` Protocols.

These are the wiring used when the gateway runs for real: an ``httpx.AsyncClient`` for the
admin-api token-validation hop, the meeting-api forward, and the ``/ws`` subscribe-authorization
hop; a ``redis.asyncio`` client for the ``/ws`` fan-in.

v0.12 P2 folded the transcription-collector INTO meeting-api (one modular monolith), so both the
proxy forward AND the ``/ws/authorize-subscribe`` hop target meeting-api — there is no longer a
separate collector URL.

They are deliberately thin — the carved behavior lives in ``app.py``; these only translate
the port calls to the concrete clients, exactly as ``services/api-gateway/main.py`` does
(``_resolve_token`` → admin-api ``/internal/validate``; the ``/ws/authorize-subscribe`` POST;
``client.request`` for the proxy; ``redis.pubsub()`` for fan-in). They carry NO test logic.

Importing this module requires ``httpx`` and ``redis`` (both pinned in ``pyproject.toml``);
the conformance harness never imports it — it injects its own in-process fakes.
"""
from __future__ import annotations

import os
from typing import Optional

from .obs import TRACE_HEADER, get_trace_id


class HttpxDownstreamClient:
    """``DownstreamClient`` over an ``httpx.AsyncClient`` — forwards to meeting-api /
    transcription-collector and returns the response (status + content + headers) verbatim."""

    def __init__(self, client):
        self._client = client

    async def request(self, method, url, *, headers=None, params=None, content=None):
        return await self._client.request(
            method, url, headers=headers, params=params or None, content=content
        )

    async def stream(self, method, url, *, headers=None, params=None, content=None):
        """Open a streaming downstream request and yield the body chunks (SSE — agent chat).
        The httpx stream is a context manager; we hold it open for the life of the generator so
        the gateway can relay each chunk to its client as it arrives."""
        async with self._client.stream(
            method, url, headers=headers, params=params or None, content=content
        ) as resp:
            async for chunk in resp.aiter_bytes():
                yield chunk


class AdminApiAuthorizer:
    """``Authorizer`` over the admin-api + meeting-api hops.

    ``resolve`` POSTs ``/internal/validate`` to admin-api (carrying ``X-Internal-Secret`` when
    configured, and forwarding the request trace_id); ``authorize_subscribe`` POSTs
    ``/ws/authorize-subscribe`` to meeting-api (which now hosts the folded-in collector, P2) with
    the resolved user identity.
    """

    def __init__(self, client, admin_api_url: str, meeting_api_url: str):
        self._client = client
        self._admin_api_url = admin_api_url.rstrip("/")
        self._meeting_api_url = meeting_api_url.rstrip("/")

    async def resolve(self, api_key: str) -> Optional[dict]:
        try:
            headers = {TRACE_HEADER: get_trace_id() or ""}
            internal_secret = os.getenv("INTERNAL_API_SECRET", "")
            if internal_secret:
                headers["X-Internal-Secret"] = internal_secret
            resp = await self._client.post(
                f"{self._admin_api_url}/internal/validate",
                json={"token": api_key},
                headers=headers,
                timeout=5.0,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            return None
        return None

    async def authorize_subscribe(self, api_key: str, meetings: list) -> dict:
        auth_headers = {"X-API-Key": api_key, TRACE_HEADER: get_trace_id() or ""}
        user_data = await self.resolve(api_key)
        if user_data:
            auth_headers["x-user-id"] = str(user_data["user_id"])
            auth_headers["x-user-scopes"] = ",".join(user_data.get("scopes", []))
            auth_headers["x-user-limits"] = str(user_data.get("max_concurrent", 3))
        try:
            resp = await self._client.post(
                f"{self._meeting_api_url}/ws/authorize-subscribe",
                headers=auth_headers,
                json={"meetings": meetings},
            )
            if resp.status_code != 200:
                return {"authorized": [], "errors": [f"authorization_service_error:{resp.status_code}"]}
            return resp.json()
        except Exception as e:
            return {"authorized": [], "errors": [f"authorization_call_failed:{e}"]}


def build_production_app(
    *,
    admin_api_url: Optional[str] = None,
    meeting_api_url: Optional[str] = None,
    redis_url: Optional[str] = None,
):
    """Construct the gateway with real httpx + redis adapters from env (the prod entrypoint).

    Lazy-imports ``httpx`` and ``redis`` so the package can be imported (and unit-tested with
    fakes) without those runtime deps installed in the test venv.

    v0.12 P2: there is ONE downstream control plane — meeting-api (it hosts the folded-in
    collector). Both the proxy forward and the ``/ws/authorize-subscribe`` hop target it.
    """
    import httpx
    import redis.asyncio as aioredis

    from .app import create_app
    from .config_preflight import preflight

    # #526: refuse to boot a misconfigured deploy — a missing INTERNAL_API_SECRET makes admin-api
    # reject every /internal/validate hop (503 on every API-key check), the 2026-04-23 shape. Fail
    # loud at boot with one message naming the missing key, instead of coming up green and 503ing.
    preflight()

    admin_api_url = admin_api_url or os.getenv("ADMIN_API_URL", "http://admin-api:8001")
    meeting_api_url = meeting_api_url or os.getenv("MEETING_API_URL", "http://meeting-api:8080")
    agent_api_url = os.getenv("AGENT_API_URL", "http://agent-api:8100")
    redis_url = redis_url or os.getenv("REDIS_URL", "redis://redis:6379/0")

    http_client = httpx.AsyncClient(timeout=30.0)
    redis_client = aioredis.from_url(
        redis_url, encoding="utf-8", decode_responses=True,
        socket_timeout=10, socket_connect_timeout=5, socket_keepalive=True,
        health_check_interval=30, retry_on_timeout=True,
    )

    authorizer = AdminApiAuthorizer(http_client, admin_api_url, meeting_api_url)
    downstream = HttpxDownstreamClient(http_client)

    from .ratelimit import from_env as _rate_limiter_from_env

    app = create_app(
        authorizer,
        downstream,
        redis_client,
        meeting_api_url=meeting_api_url,
        agent_api_url=agent_api_url,  # P20·Stage 2: the agent control plane fronted under /api/*
        admin_api_url=admin_api_url,  # /user/webhook self-serve proxies to identity (admin-api)
        rate_limiter=_rate_limiter_from_env(),  # WS-6: per-user DoS guard (generous defaults; env-tunable)
    )

    # --- fastapi-guard: per-IP rate limiting, IP allow/deny + auto-ban (edge_guard.py) ---
    # Prod-only wiring; create_app stays pure so the conformance harness drives it directly
    # and observes zero change. Complementary to the per-user limiter above (per-key catches
    # one-token-many-IPs; guard's per-IP catches many-tokens-from-one-IP + auto-bans).
    from .edge_guard import apply_guard

    apply_guard(app)
    return app


# ── ASGI entrypoint (P4) ─────────────────────────────────────────────────────────────────────
# ``uvicorn gateway.adapters:app`` (the compose CMD) resolves this. Exposed LAZILY via PEP 562 so
# merely importing this module never constructs the production app (which needs httpx + redis — the
# latter is NOT in the offline gate venv). uvicorn touches ``adapters.app`` at startup → the app is
# built then, with the real adapters, from env (ADMIN_API_URL / MEETING_API_URL / REDIS_URL).
def __getattr__(name: str):
    if name == "app":
        return build_production_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
