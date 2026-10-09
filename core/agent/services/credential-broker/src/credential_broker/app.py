"""The credential broker's HTTP front door (credential-broker.v1): the app factory.

Every route except GET /health requires an X-Vexa-Assertion signed with a role key; the role decides
what the caller may do (contract `x-routes`). An agent-role call must ALSO carry the gateway's signed
identity (gateway-identity.v1 `X-Vexa-Identity`) naming the same person as the assertion's actor:
agent-api holds the agent key, and the agent key alone must not let it act for anybody it likes. The broker owns three things nobody else writes:
connection metadata and its audit trail (metadata.sqlite), the credential store (ADR-0040), and
the OAuth state that binds a consent to the browser session that started it.

This module wires the assertion middleware, the error handlers and the health probe, and includes
the routes (`routes_connections.py`, `routes_git.py`); the shared state and checks are `broker.py`,
the request bodies `models.py`.

No route returns a stored credential to the `agent` or `human` roles. Errors are fixed sentences
and never echo input. Every refusal and every fault is logged as a typed line — source, kind,
route — and never with a value.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import assertion, identity_token, providers, routes_connections, routes_git
from .broker import Broker, route_of
from .faults import UpstreamFault
from .obs import TraceMiddleware, log_event


def create_app(broker: Broker) -> FastAPI:
    app = FastAPI(title="credential-broker", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.broker = broker
    b = broker

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        # Validation errors must not echo credential-bearing input.
        return JSONResponse({"detail": "Invalid request fields"}, 422)

    @app.exception_handler(providers.ProviderError)
    async def provider_refusal(request, exc):
        # The person can act on it (reconnect, grant a permission, fix an argument).
        b.refused("provider", "refused", route_of(request.url.path))
        return JSONResponse({"detail": str(exc)}, 409)

    @app.exception_handler(UpstreamFault)
    async def upstream_fault(request, exc):
        # Google or a custom service is down or answered unusably: 503/502, never a reconnect.
        b.fault(exc.source, exc.kind, route=route_of(request.url.path))
        return JSONResponse({"detail": str(exc)}, exc.status)

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        if request.method == "GET" and request.url.path == "/health":
            return await call_next(request)
        header = request.headers.get(assertion.HEADER.lower(), "")
        body = await request.body()
        path = request.url.path + ("?" + request.url.query if request.url.query else "")
        try:
            if not header:
                raise assertion.AssertionRefused("missing")
            claims = assertion.verify(header, key_for=b.key_for, method=request.method, path=path,
                                      body=body, remember=b.remember)
            if claims["role"] == "agent":
                b.person_signed(request.headers.get(identity_token.HEADER, "").strip(), claims["actor"])
        except assertion.AssertionRefused as exc:
            log_event("assertion_refused", level="warning",
                      fields={"kind": exc.kind, "method": request.method, "route": route_of(path)})
            return JSONResponse({"detail": "Product identity refused"}, 401)
        request.state.who = {"actor": claims["actor"], "session": claims["session"], "role": claims["role"]}
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    app.add_middleware(TraceMiddleware)

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "credential-broker", "store": b.store.name}

    app.include_router(routes_connections.build(b))
    app.include_router(routes_git.build(b))
    return app
