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
use for a person's, and forwarding both would let the weaker one look like it was checked. Because
that key is the deployment's, it is spent only for the instance admin calling with their own
credential, confirmed with the gateway at call time (`_require_instance_admin`).
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

A DECLARED ARGUMENT THAT CAME FROM `requestBody` (`bind.BoundTool.body_params`) is published as a
`Body(..., embed=True)` parameter instead of `Query`, so THIS edge's own OpenAPI shows it under
`requestBody` rather than `parameters` — which is what tells `fastapi-mcp`'s own call-execution
(`_execute_api_tool`) to send it as a JSON body field rather than a query string parameter when an
agent calls the tool. `embed=True` on every one of them, regardless of count, because FastAPI's rule
for a SINGLE singular `Body` parameter is to treat the whole request body AS that one value —
`{"name": "x"}` becomes bare `"x"` — and every body model bound here (`WorkspaceNewBody`, one field)
would silently hit exactly that rule without it. Multiple `Body` parameters already embed by
themselves; passing it unconditionally keeps the one-field and many-field cases identical rather
than depending on a count nobody is watching.
"""
from __future__ import annotations

import inspect
import os
from typing import Dict, List, Optional
from urllib.parse import quote

import httpx
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from . import reentry as reentry_mod
from .bind import BoundTool

#: JSON Schema type -> the Python annotation FastAPI needs to publish it again. Anything else is a
#: string: a wrong-but-honest type is recoverable, a guessed structure is not. `array`/`object` cover
#: the requestBody-derived fields query parameters never needed (`FlowSubmission.steps: list[str]`,
#: `.params: dict`) — a query-origin field never publishes either shape, so widening the map here
#: changes nothing for a query-declared argument.
#: How long an assembled tool waits for its owning route — the gateway's buffered-forward bound.
TOOL_TIMEOUT_S = 30

_PY_TYPE = {"integer": int, "number": float, "boolean": bool, "string": str,
           "array": list, "object": dict}


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
             env: Optional[dict] = None, gateway_url: Optional[str] = None) -> List[str]:
    """Add one route per bound tool. Returns the names registered, in order."""
    env = os.environ if env is None else env
    names: List[str] = []
    for bt in bound:
        names.append(_add(app, bt, base_urls[bt.tool.domain], transport, env, gateway_url))
    return names


#: The answer to a caller who is not the instance admin speaking for themselves.
ADMIN_REFUSAL = ("this tool acts with the deployment's own operator key, so only the instance "
                 "admin, calling with their own credential, may use it")


async def _require_instance_admin(caller_key: str, gateway_url: Optional[str],
                                  transport: Optional[httpx.AsyncBaseTransport]) -> None:
    """An `auth: admin` tool spends a key the DEPLOYMENT holds, so this edge spends it only for the
    instance admin calling with their own credential — asked of the gateway at call time (`/auth/me`
    answers `is_admin` from identity), never read from anything the caller sent. For a worker's
    delegation token the gateway answers only on this edge's re-entry (the identity it signed onto
    the `/mcp` request, `reentry.py`), and says admin only when the person the worker acts for is
    the instance admin and is in the loop (regime `human`). No gateway to ask is a refusal too."""
    if not gateway_url or not caller_key:
        raise HTTPException(status_code=403, detail=ADMIN_REFUSAL)
    try:
        async with httpx.AsyncClient(timeout=TOOL_TIMEOUT_S, transport=transport) as client:
            r = await client.get(gateway_url.rstrip("/") + "/auth/me",
                                 headers={"X-API-Key": caller_key, **reentry_mod.headers()})
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="could not confirm who is calling; try again")
    try:
        me = r.json() if r.status_code == 200 else {}
    except Exception:  # noqa: BLE001 — an unreadable answer confirms nothing
        me = {}
    if not isinstance(me, dict) or me.get("is_admin") is not True:
        raise HTTPException(status_code=403, detail=ADMIN_REFUSAL)


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
    declared argument is optional, carrying the owning route's own type and description — as a query
    parameter, unless `bind.py` derived it from the route's `requestBody`, in which case it is a
    `Body` parameter instead so this edge's own OpenAPI keeps the same query/body split the owning
    route has (see the module docstring for why `embed=True` is unconditional).
    """
    params = [inspect.Parameter("request", inspect.Parameter.KEYWORD_ONLY, annotation=Request)]
    for name in bt.path_params:
        params.append(inspect.Parameter(
            name, inspect.Parameter.KEYWORD_ONLY, annotation=str,
            default=Query(..., description=f"part of this tool's address: {bt.tool.route['path']}")))
    for name, schema in bt.parameters.items():
        if name in bt.path_params:
            continue
        annotation = Optional[_PY_TYPE.get(_json_type(schema), str)]
        description = schema.get("description") or None
        vocabulary = _vocabulary(schema) or None
        # QUERY OR BODY, decided by where the OWNING ROUTE publishes the argument (bind.py read its
        # `requestBody`), never guessed here — see the module docstring for why `embed=True` is
        # unconditional. The vocabulary annotation rides either one: an agent needs the words
        # whichever half of the forward the argument travels in.
        if name in bt.body_params and _declares_keys(schema):
            # A NESTED MODEL TRAVELS WHOLE: its keys, their types and `additionalProperties: false`,
            # exactly as the owning route publishes it (`bind._inline`), so the tool's input schema
            # tells an agent every key it may write and the MCP refuses any other one by name.
            default = Body(None, embed=True, description=description,
                           json_schema_extra=_publish_as(schema, description))
        elif name in bt.body_params:
            default = Body(None, embed=True, description=description, json_schema_extra=vocabulary)
        else:
            default = Query(None, description=description, json_schema_extra=vocabulary)
        params.append(inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY,
                                        annotation=annotation, default=default))
    return inspect.Signature(params)


def _declares_keys(schema: dict) -> bool:
    """Is this an object with a closed set of named keys (directly, or as a nullable `anyOf`)?"""
    if not isinstance(schema, dict):
        return False
    if isinstance(schema.get("properties"), dict) and schema.get("additionalProperties") is False:
        return True
    return any(_declares_keys(b) for b in (schema.get("anyOf") or schema.get("oneOf") or [])
               if isinstance(b, dict))


def _publish_as(schema: dict, description: Optional[str]):
    """A `json_schema_extra` that replaces what FastAPI derived from the edge's loose annotation with
    the owning route's own schema for the argument. Any `enum` inside it is the route's own
    validation (a `Literal` it enforces), so the edge refuses exactly what the route would — never
    more (the reason `_vocabulary` keeps a route's SUGGESTED words out of `enum`)."""
    declared = {k: v for k, v in schema.items() if k != "title"}
    if description and "description" not in declared:
        declared["description"] = description

    def replace(generated: dict) -> None:
        title = generated.get("title")
        generated.clear()
        generated.update(declared)
        if title:
            generated["title"] = title
    return replace


def _json_type(schema: dict) -> str:
    """The JSON type an owning route published for one argument.

    A pydantic `Optional[X]` publishes `anyOf: [X, {"type": "null"}]` with no top-level `type`, and
    a nested model publishes a `$ref`; reading only `type` turned `setup: dict | None` and
    `receipts: list[Receipt]` into strings an agent could not fill. The first non-null branch is
    the argument's type, and a `$ref` is an object."""
    if schema.get("type"):
        return str(schema["type"])
    if schema.get("$ref"):
        return "object"
    for key in ("anyOf", "oneOf"):
        for branch in schema.get(key) or []:
            if isinstance(branch, dict) and branch.get("type") != "null":
                return _json_type(branch)
    return "string"


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

    BOTH KEYS ARE READ off the owning route, and that is a real case rather than defensiveness: a
    route that has decided its own vocabulary is a suggestion publishes `examples`, and a route
    that validates against a closed set publishes `enum`. An agent needs the words either way, and
    either way this edge must not be the thing that refuses the call for not using them.
    """
    values = schema.get("enum") or schema.get("examples")
    return {"examples": list(values)} if isinstance(values, (list, tuple)) and values else {}


def _add(app: FastAPI, bt: BoundTool, base: str,
         transport: Optional[httpx.AsyncBaseTransport], env: dict, gateway_url: Optional[str] = None) -> str:
    method = bt.tool.route["method"]
    template = bt.tool.route["path"]
    declared = tuple(n for n in bt.parameters if n not in bt.path_params)
    body_declared = tuple(n for n in declared if n in bt.body_params)
    query_declared = tuple(n for n in declared if n not in bt.body_params)
    path_params = tuple(bt.path_params)

    async def endpoint(*, request: Request, **argument):
        key = _caller_key(request)
        if key == "" and bt.tool.identity != "none":
            raise HTTPException(status_code=401, detail="this tool needs your Vexa credential")
        if bt.tool.auth == "admin":
            await _require_instance_admin(key, gateway_url, transport)
        raw_body = {}
        if method in ("POST", "PUT", "PATCH"):
            try:
                raw_body = await request.json()
            except Exception:  # noqa: BLE001 — an empty body is a legitimate call
                raw_body = {}
        raw_body = raw_body if isinstance(raw_body, dict) else {}

        path = template
        for name in path_params:
            # Declared REQUIRED in the signature, so FastAPI has already refused a call without it;
            # empty-but-present is the one thing that reaches here, and it addresses nothing.
            value = argument.get(name)
            if value in (None, ""):
                raise HTTPException(status_code=422, detail=f"{bt.name} needs {name}")
            # PERCENT-ENCODED, EVERY CHARACTER, `/` INCLUDED. A path parameter is one segment of the
            # tool's own route and nothing else; substituted raw it is a caller-supplied fragment of
            # URL. `reaction_id="../../admin/keys"` composed `/reactions/../../admin/keys/retry`,
            # which httpx resolves before it goes out — so an agent could address ANY route on the
            # owning domain's internal address, under whichever credential the tool's `auth` names.
            # `safe=""` leaves nothing that can end the segment, so the request stays under the
            # route the manifest declared and a traversal attempt arrives as a literal 404 id.
            path = path.replace("{" + name + "}", quote(str(value), safe=""))

        params = {n: argument[n] for n in query_declared if argument.get(n) is not None}
        for n in query_declared:
            if n not in params and n in raw_body:
                params[n] = raw_body.pop(n)

        # A TYPED body (bind.py derived it from the route's own `requestBody`): only the DECLARED
        # fields travel — the manifest's promise, kept both ways. Anything else the caller sent is
        # dropped here, never guessed at, same as an undeclared query argument always was.
        #
        # No declared body fields at all (every tool before this change, and every path-only verb
        # like `reaction_signal` today): the caller's raw JSON passes through untouched, exactly as
        # it always has — `signal_reaction`'s own `body: dict = Body(default={})` is the freeform
        # shape this preserves.
        if body_declared:
            body = {n: argument[n] for n in body_declared if argument.get(n) is not None}
        else:
            body = raw_body

        headers = _outbound(bt, key, env)
        if bt.tool.forward and gateway_url:
            # Back through the gateway, at the path the domain's `forward` declares, carrying the
            # identity the gateway signed onto this request so it can admit a worker's tool call
            # (`reentry.py`). Never sent to a domain directly.
            edge, upstream = bt.tool.forward
            url = gateway_url.rstrip("/") + edge + path[len(upstream):]
            headers.update(reentry_mod.headers())
        else:
            url = f"{base}{path}"
        try:
            # The forward crosses the gateway, whose buffered leg allows 30 s; a tool that waits
            # less than its own door gives up on calls the door would have answered (a mailbox
            # read through the credential broker is routinely several seconds).
            async with httpx.AsyncClient(timeout=TOOL_TIMEOUT_S, transport=transport) as client:
                r = await client.request(
                    method, url,
                    headers=headers,
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
    # ONE TEXT, ONE KEY. This used to pass `bt.description` as BOTH `summary` and `description`,
    # and `fastapi_mcp` composes a tool's description as `summary + "\n\n" + description`
    # (`openapi/convert.py`) with no check that the two differ — so every assembled tool published
    # its whole text TWICE, back to back. On `whats_waiting` that was ~250 words, then the same
    # ~250 words, and the second copy said nothing the first had not.
    #
    # The description is the words. A summary that merely restates them is not a summary, so this
    # sets none and lets FastAPI title the route from its name — two words, in front of the text,
    # which is the shape `fastapi_mcp` is written for.
    app.add_api_route(f"/tools/{bt.name}", endpoint, methods=[method],
                      operation_id=bt.name, name=bt.name,
                      description=bt.description or None)
    return bt.name
