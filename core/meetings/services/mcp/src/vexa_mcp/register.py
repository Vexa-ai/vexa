"""REGISTRATION — an assembled tool becomes one route on this service, and therefore one MCP tool.

`FastApiMCP` derives the MCP surface from this app's OpenAPI: a route with `operation_id=<name>` IS
a tool called `<name>`. That is how the fourteen built-in tools work, so assembling through the same
mechanism makes an assembled tool and a built-in one indistinguishable to a client — which is the
whole point of assembling rather than proxying, and the reason there is no second code path to keep
in step.

WHAT TRAVELS: the caller's own credential, as `X-API-Key`, exactly as the fourteen send it. Nothing
else. There is one authentication path into this edge (PRD 40.8) — a bearer in the header, the
session bound by `Mcp-Session-Id` — so a tool cannot take a credential as an argument and this
forward cannot invent one.

WHAT DOES NOT TRAVEL: anything the manifest did not declare. An argument the owning route ignores is
the worst reply available to an agent — it reports success for something that did not happen — so an
undeclared parameter is dropped here rather than forwarded and silently discarded there.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .bind import BoundTool


def _caller_key(request: Request) -> str:
    """The credential the edge already resolved. One carrier, no fallbacks: `x-api-key`, or the
    bearer the MCP transport contract uses, adapted at this boundary exactly as `_mcp_key` does at
    the gateway."""
    key = (request.headers.get("x-api-key") or "").strip()
    if key:
        return key
    auth = (request.headers.get("authorization") or "").strip()
    if not auth:
        return ""
    scheme, _, token = auth.partition(" ")
    return (token.strip() if scheme.lower() == "bearer" else auth) or ""


def register(app: FastAPI, bound: List[BoundTool], base_urls: Dict[str, str], *,
             transport: Optional[httpx.AsyncBaseTransport] = None) -> List[str]:
    """Add one route per bound tool. Returns the names registered, in order."""
    names: List[str] = []
    for bt in bound:
        names.append(_add(app, bt, base_urls[bt.tool.domain], transport))
    return names


def _add(app: FastAPI, bt: BoundTool, base: str,
         transport: Optional[httpx.AsyncBaseTransport]) -> str:
    method = bt.tool.route["method"]
    template = bt.tool.route["path"]
    declared = tuple(bt.parameters)
    path_params = tuple(bt.path_params)

    async def endpoint(request: Request):
        key = _caller_key(request)
        if key == "" and bt.tool.identity != "none":
            raise HTTPException(status_code=401, detail="this tool needs your Vexa credential")
        body = {}
        if method in ("POST", "PUT", "PATCH"):
            try:
                body = await request.json()
            except Exception:  # noqa: BLE001 — an empty body is a legitimate call
                body = {}
        body = body if isinstance(body, dict) else {}

        path = template
        for name in path_params:
            value = body.pop(name, None)
            if value is None:
                value = request.query_params.get(name)
            if value in (None, ""):
                raise HTTPException(status_code=422,
                                    detail=f"{bt.name} needs {name}")
            path = path.replace("{" + name + "}", str(value))

        params = {n: request.query_params[n] for n in declared if n in request.query_params}
        for n in declared:
            if n not in params and n in body:
                params[n] = body.pop(n)

        try:
            async with httpx.AsyncClient(timeout=10, transport=transport) as client:
                r = await client.request(
                    method, f"{base}{path}",
                    headers={"X-API-Key": key, "Content-Type": "application/json"},
                    params=params or None,
                    json=body if method in ("POST", "PUT", "PATCH") else None)
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail=f"{bt.tool.domain} timed out")
        except httpx.RequestError as e:
            raise HTTPException(status_code=503, detail=f"{bt.tool.domain} is unreachable: {e}")
        # THE DOMAIN'S OWN ANSWER, UNCHANGED. A 403 from flows is flows' answer and an agent needs
        # to see it; rewriting it here would turn "you may not do that" into "we are broken".
        try:
            payload = r.json() if r.content else {}
        except Exception:  # noqa: BLE001
            payload = {"detail": r.text[:2000]}
        return JSONResponse(status_code=r.status_code, content=payload)

    endpoint.__name__ = bt.name
    endpoint.__doc__ = bt.description or f"{bt.tool.domain}: {method} {template}"
    app.add_api_route(f"/tools/{bt.name}", endpoint, methods=[method],
                      operation_id=bt.name, name=bt.name,
                      summary=bt.description or None,
                      description=bt.description or None)
    return bt.name
