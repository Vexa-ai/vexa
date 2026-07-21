"""``build_gateway()`` — construct the PRODUCTION gateway (`gateway.create_app`) wired to the
conformance fakes, so every O-API-1 assertion drives the SHIPPED app.

Before the carve this module was a hand-port of `services/api-gateway/main.py`. It is now a
**thin shim**: it imports `create_app` from the production `gateway` package and injects the
in-process fakes —

  * a port-fake DOWNSTREAM (meeting-api + transcription-collector) replaying the api.v1
    goldens (`fake_meeting_api.build_fake_downstream`), reached over an httpx ASGITransport;
  * an AUTHORIZER that resolves `x-api-key` by POSTing the fake admin-api's `/internal/validate`
    (same transport) — exactly `main._resolve_token`;
  * (the `/ws` multiplex uses the harness fakes — see `ws_harness.py`).

The single source of the proxy + auth + scope logic is now `gateway.app`; this file only
supplies the fakes that satisfy the gateway's ports. Allowed import direction: conformance
(test) → gateway (prod). The gateway package imports nothing from here.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI

from gateway import create_app

from .fake_meeting_api import build_fake_downstream
from .obs import TRACE_HEADER, get_trace_id

# Sentinel bases the production app forwards to. The path is what matters; the conformance
# DownstreamClient routes by PATH to the single in-process fake (ASGITransport ignores the host).
_DOWNSTREAM_BASE = "http://downstream"


class _ConformanceDownstream:
    """Satisfies `gateway.ports.DownstreamClient`: forward by PATH to the in-process fake
    (meeting-api + transcription-collector) over an httpx ASGITransport. The production app
    hands a full URL (`http://meeting-api/bots`); we keep only the path + query so the one
    fake app serves every route, mirroring how a real httpx client would reach two hosts."""

    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def request(self, method, url, *, headers=None, params=None, content=None, timeout=None):
        # timeout is accepted for port parity and ignored: the in-process ASGI hop has no wire.
        parts = urlsplit(url)
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        return await self._client.request(
            method, path, headers=headers, params=params or None, content=content
        )


class _ConformanceAuthorizer:
    """Satisfies `gateway.ports.Authorizer`. `resolve` POSTs the fake admin-api's
    `/internal/validate` (carved from `main._resolve_token`); `authorize_subscribe` is unused
    on the REST path (the `/ws` multiplex is exercised via `ws_harness.WSMultiplexHarness`)."""

    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def resolve(self, api_key: str) -> Optional[dict]:
        try:
            resp = await self._client.post(
                "/internal/validate",
                json={"token": api_key},
                headers={TRACE_HEADER: get_trace_id() or ""},
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            return None
        return None

    async def authorize_subscribe(self, api_key: str, meetings: list) -> dict:  # pragma: no cover
        # REST conformance never hits this; the /ws path uses the harness's FakeAuthorizer.
        return {"authorized": [], "errors": ["not used on the REST conformance path"]}


class _NullRedis:
    """Satisfies `gateway.ports.RedisBus`. The REST conformance never opens `/ws`, so the bus
    is never used; the `/ws` protocol conformance drives `ws_harness.WSMultiplexHarness` (which
    runs the production `_run_multiplex` against `FakeRedis`)."""

    def pubsub(self):  # pragma: no cover
        raise RuntimeError("RedisBus is not used on the REST conformance path")


def build_gateway() -> FastAPI:
    """The SHIPPED gateway under test: `gateway.create_app` injected with the conformance fakes.

    Holds an in-process httpx client bound to the fake downstream + fake admin-api so the auth
    and proxy hops never leave the process (no docker, no real backend, no sockets)."""
    downstream_app = build_fake_downstream()
    transport = httpx.ASGITransport(app=downstream_app)
    client = httpx.AsyncClient(transport=transport, base_url=_DOWNSTREAM_BASE)

    return create_app(
        _ConformanceAuthorizer(client),
        _ConformanceDownstream(client),
        _NullRedis(),
    )
