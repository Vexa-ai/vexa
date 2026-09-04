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

AND THE ROUTE IS BUILT WITH A REAL SIGNATURE, which is not a detail (issue #1468). The MCP tool's
input schema is derived from THIS app's OpenAPI, and FastAPI derives that from the endpoint's
parameters — so an endpoint taking only `request` published a tool with NO arguments at all. Every
declared argument disappeared between `bind`, which had just verified each one against the owning
route, and the surface. With the schema closed (`additionalProperties: false`, correctly) the effect
was not a silent drop but a refusal: `reactions_list` could not filter, and `reaction_signal` could
not be called at all, because `/reactions/{reaction_id}/{verb}` cannot be addressed without its two
path parameters and an agent could not see that they existed.

So the signature is BUILT — one query parameter per path parameter (required: the route cannot be
addressed without them) and one per declared argument (optional, carrying the owning route's own
type and description). It also re-arms the app-wide unknown-argument guard, which reads a route's
declared query parameters and would otherwise refuse every argument to every assembled tool.
"""
from __future__ import annotations

import inspect
import os
from typing import Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .bind import BoundTool

#: JSON Schema type -> the Python annotation FastAPI needs to publish it again. Anything else is a
#: string: a wrong-but-honest type is recoverable, a guessed structure is not.
_PY_TYPE = {"integer": int, "number": float, "boolean": bool, "string": str}


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


def _signature(bt: BoundTool) -> inspect.Signature:
    """The endpoint's parameters, as FastAPI has to see them to publish them again.

    Path parameters are REQUIRED and declared arguments are optional, which is what the owning route
    already says: `/reactions/{reaction_id}/{verb}` cannot be addressed without both, and every
    declared argument is a query parameter with a default.
    """
    params = [inspect.Parameter("request", inspect.Parameter.KEYWORD_ONLY, annotation=Request)]
    for name in bt.path_params:
        params.append(inspect.Parameter(
            name, inspect.Parameter.KEYWORD_ONLY, annotation=str,
            default=Query(..., description=f"part of this tool's address: {bt.tool.route['path']}")))
    for name, schema in bt.parameters.items():
        if name in bt.path_params:
            continue
        params.append(inspect.Parameter(
            name, inspect.Parameter.KEYWORD_ONLY,
            annotation=Optional[_PY_TYPE.get(str(schema.get("type")), str)],
            default=Query(None, description=schema.get("description") or None,
                          json_schema_extra=_vocabulary(schema) or None)))
    return inspect.Signature(params)


def _vocabulary(schema: dict) -> dict:
    """The owning route's allowed values, republished so an agent can read them before it guesses —
    AS AN ANNOTATION (`examples`), NEVER AS `enum`.

    An argument published as a bare `string` tells an agent nothing about which words the route
    understands, and an agent that has to guess a word guesses wrong: twelve friction reports were
    thrown away on prod in twenty minutes because `kind` reached `tools/list` as an open string
    (F-D26). So the vocabulary has to travel. The question is in which key.

    IT MUST NOT TRAVEL AS `enum`, and that was caught on the station rather than reasoned out. The
    MCP SDK's own dispatcher validates a call's arguments against the tool's `inputSchema`
    (`mcp/server/lowlevel/server.py`: `jsonschema.validate(instance=arguments,
    schema=tool.inputSchema)`) and returns `isError` without ever calling the tool. A first cut
    published `enum` here; `report_friction` with `kind="broke"` came back "Input validation error:
    'broke' is not one of [...]" and the report was destroyed one hop EARLIER than before, by the
    fix for the defect. The edge would have become a stricter gate than the route it fronts.

    `examples` is a JSON Schema ANNOTATION: it reaches `tools/list`, an agent reads it, and no
    validator anywhere rejects a value for not being in it. The words themselves are also spelled
    out in the argument's own description, which the owning route writes. Guidance belongs in front
    of the agent; the decision about an unrecognised word belongs to the route that stores it.
    """
    values = schema.get("enum")
    return {"examples": list(values)} if isinstance(values, (list, tuple)) and values else {}


def _add(app: FastAPI, bt: BoundTool, base: str,
         transport: Optional[httpx.AsyncBaseTransport], env: dict) -> str:
    method = bt.tool.route["method"]
    template = bt.tool.route["path"]
    declared = tuple(n for n in bt.parameters if n not in bt.path_params)
    path_params = tuple(bt.path_params)

    async def endpoint(*, request: Request, **argument):
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
            # Declared REQUIRED in the signature, so FastAPI has already refused a call without it;
            # empty-but-present is the one thing that reaches here, and it addresses nothing.
            value = argument.get(name)
            if value in (None, ""):
                raise HTTPException(status_code=422, detail=f"{bt.name} needs {name}")
            path = path.replace("{" + name + "}", str(value))

        params = {n: argument[n] for n in declared if argument.get(n) is not None}
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
    endpoint.__signature__ = _signature(bt)
    app.add_api_route(f"/tools/{bt.name}", endpoint, methods=[method],
                      operation_id=bt.name, name=bt.name,
                      summary=bt.description or None,
                      description=bt.description or None)
    return bt.name
