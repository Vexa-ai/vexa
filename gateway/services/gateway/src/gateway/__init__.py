"""gateway — the v0.12 PRODUCTION edge: auth · routing · WS fan-out.

The single source of the gateway lane's proxy + ``/ws`` multiplex logic (the v0.12 carve of
the deployed ``services/api-gateway/main.py``). Collaborators are injected as PORTS so the
SAME app runs with real adapters in prod and in-process fakes in the conformance harness.

Public surface (the front door):
  - ``create_app(authorizer, downstream, redis, ...)`` — the FastAPI gateway (REST proxy,
    fail-closed auth + scope 403, verbatim body passthrough; the ``/ws`` multiplex;
    ``/health``).
  - ``run_multiplex(ws, authorizer, redis)`` — the ``/ws`` control loop + fan-in, exposed so the
    conformance ws-harness drives the SHIPPED multiplex without reaching for a private.
  - ``ports`` — the Protocols: ``Authorizer``, ``DownstreamClient``, ``RedisBus`` (+ helpers).
  - ``adapters.build_production_app(...)`` — wire ``create_app`` with real httpx + redis.
  - ``obs`` — the lane's ``logevent.v1`` trace emitter (``TraceMiddleware``, ``log_event``).

Import direction is one-way: the conformance harness imports THIS package to drive the
shipped app; this package imports nothing from conformance.
"""
from __future__ import annotations

from .app import ROUTE_SCOPES, create_app, run_multiplex
from .ports import Authorizer, DownstreamClient, PubSub, RedisBus

__all__ = [
    "create_app",
    "run_multiplex",
    "ROUTE_SCOPES",
    "Authorizer",
    "DownstreamClient",
    "RedisBus",
    "PubSub",
]
