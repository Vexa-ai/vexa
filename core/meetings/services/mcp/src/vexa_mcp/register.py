"""REGISTRATION — an assembled tool becomes one route on this service, and therefore one MCP tool.

`FastApiMCP` derives the MCP surface from this app's OpenAPI: a route with `operation_id=<name>` IS
a tool called `<name>`. That is how the fourteen built-in tools work, so assembling through the same
mechanism makes an assembled tool and a built-in one indistinguishable to a client — which is the
whole point of assembling rather than proxying, and the reason there is no second code path to keep
in step.

WHAT TRAVELS: whichever credential the tool's `auth` names, and nothing else (issue #1468).
`subject` sends the caller's own, as `X-API-Key`, exactly as the fourteen do — the case this edge
was built for. `admin` sends the key the DEPLOYMENT holds, in the header the owning domain named,
and the caller's own credential does NOT travel with it: a door that reads an operator key has no
use for a person's, and forwarding both would let the weaker one look like it was checked.
`none` sends neither.

There is one authentication path INTO this edge (PRD 40.8) — a bearer in the header, the session
bound by `Mcp-Session-Id` — so a tool cannot take a credential as an argument and this forward
cannot invent one. What goes out of the edge is a different question, and it is the manifest that
answers it; whether the deployment can answer it at all was already settled at assembly.

WHAT DOES NOT TRAVEL: anything the manifest did not declare. An argument the owning route ignores is
the worst reply available to an agent — it reports success for something that did not happen — so an
undeclared parameter is dropped here rather than forwarded and silently discarded there.
"""
from __future__ import annotations

import os
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
             transport: Optional[httpx.AsyncBaseTransport] = None,
             env: Optional[dict] = None) -> List[str]:
    """Add one route per bound tool. Returns the names registered, in order."""
    env = os.environ if env is None else env
    names: List[str] = []
    for bt in bound:
        names.append(_add(app, bt, base_urls[bt.tool.domain], transport, env))
    return names


def _outbound(bt: BoundTool, caller_key: str, env: dict) -> Dict[str, str]:
    """The credential headers this hop carries — decided by the manifest, not by what is available.

    Read at request time rather than captured at boot so a rotated operator key takes effect on a
    restart of the service that HOLDS it, not only of this one; assembly already proved it is set.
    """
    headers = {"Content-Type": "application/json"}
    if bt.tool.auth == "subject":
        headers["X-API-Key"] = caller_key
    elif bt.tool.auth == "admin" and bt.tool.admin_auth:
        headers[bt.tool.admin_auth["header"]] = str(env.get(bt.tool.admin_auth["key_env"]) or "")
    return headers


def _add(app: FastAPI, bt: BoundTool, base: str,
         transport: Optional[httpx.AsyncBaseTransport], env: dict) -> str:
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
                    headers=_outbound(bt, key, env),
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
