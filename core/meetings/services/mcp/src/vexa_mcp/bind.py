"""BINDING — the manifest's promise, checked against the service that has to keep it.

A manifest binds a NAME to a ROUTE and carries no schema and no description of its own. Both are
DERIVED here, from the bound route's OpenAPI operation — the same mechanism this service already
runs on for its own fourteen tools (`operation_id` on a FastAPI route, read by `FastApiMCP`). One
place to write a tool's shape is why a tool and the route behind it cannot disagree.

That makes a manifest a CLAIM ABOUT ANOTHER SERVICE, and this module is where the claim is checked.
Two failures fail the boot:

  * a route the domain does not serve — a manifest lying about its own service, which would surface
    as a tool that appears in `tools/list` and 404s the first time an agent calls it;
  * an argument the operation does not take — an argument an agent can pass and the route silently
    ignores is the worst reply available, because the agent then reports success on something that
    did not happen.

Path parameters are arguments without being declared: `/reactions/{id}/{verb}` cannot be called
without them, and a manifest that had to restate them would be a second place to write the route.

AN ARGUMENT'S SCHEMA CAN COME FROM TWO PLACES ON THE SAME OPERATION, and both are read here: OpenAPI
`parameters` (query/path — the mechanism above already ran on) and OpenAPI `requestBody` (a route
whose input is a JSON body — `PUT`/`POST` with a pydantic model, published under
`content.application/json.schema`, `$ref`-resolved against the domain's own `components.schemas`).
A body schema with no NAMED properties — `body: dict = Body(...)`, which FastAPI publishes as
`{"type": "object", "additionalProperties": true}` with nothing under `properties` — has nothing to
derive, exactly as if the route took no body at all: a manifest cannot declare an argument against
it and every such argument fails the same "does not take" check a bad query argument would.
`BoundTool.body_params` names which declared arguments came from the body rather than the query
string, so `register.py` knows which half of the forward each argument belongs to.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List

from .manifest import Assembly, ManifestError, Tool

_PATH_PARAM = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class BoundTool:
    tool: Tool
    description: str
    parameters: Dict[str, dict] = field(default_factory=dict)
    path_params: tuple = ()
    #: the subset of `parameters` (excluding path_params) that travel in the JSON request body
    #: rather than the query string — derived from the route's OpenAPI `requestBody`, not asserted.
    body_params: tuple = ()

    @property
    def name(self) -> str:
        return self.tool.name


def _describe(op: dict) -> str:
    """The words an agent reads before it decides whether to call this tool.

    THE SUMMARY IS NOT ENOUGH, and taking it first was a defect (F-D12, F-D26). FastAPI
    SYNTHESISES `summary` from the endpoint's function name whenever the route does not set one —
    so a route called `report_friction` publishes `summary: "Report Friction"`, and preferring it
    published a tool whose whole description was its own title, while the route's docstring, which
    says what friction is and which words to use, sat one key away in `description`. An agent given
    a title guesses; twelve friction reports were lost on prod in twenty minutes to that guess.

    So the DESCRIPTION (the docstring) leads, and the summary stays in front of it only when it
    says something the description does not already say.
    """
    summary = str(op.get("summary") or "").strip()
    body = str(op.get("description") or "").strip()
    if not body:
        return summary
    if not summary or summary.lower() in body.lower():
        return body
    return f"{summary}\n\n{body}"


def _operation(openapi: dict, method: str, path: str) -> dict | None:
    item = (openapi.get("paths") or {}).get(path)
    if not isinstance(item, dict):
        return None
    op = item.get(method.lower())
    return op if isinstance(op, dict) else None


def _resolve_ref(schema: dict, openapi: dict) -> dict:
    """One level of `$ref` resolution against this domain's own `components.schemas` — enough for
    every body model here, which is a flat pydantic `BaseModel` and never one that nests a `$ref` at
    its own top level. A schema with no `$ref` is returned unchanged."""
    if not isinstance(schema, dict):
        return {}
    ref = schema.get("$ref")
    if not ref:
        return schema
    name = str(ref).rsplit("/", 1)[-1]
    return dict((openapi.get("components") or {}).get("schemas", {}).get(name) or {})


def _body_params(op: dict, openapi: dict) -> Dict[str, dict]:
    """The route's JSON request-body fields, by name -> schema — the `requestBody` twin of
    `parameters`. A body with no named `properties` (the untyped `body: dict = Body(...)` shape)
    contributes nothing, exactly as a route with no `requestBody` at all would — there is no field
    here for a manifest to declare an argument against."""
    rb = op.get("requestBody")
    if not isinstance(rb, dict):
        return {}
    content = (rb.get("content") or {}).get("application/json")
    if not isinstance(content, dict):
        return {}
    schema = _resolve_ref(dict(content.get("schema") or {}), openapi)
    props = schema.get("properties")
    if not isinstance(props, dict):
        return {}
    return {name: dict(prop_schema or {}) for name, prop_schema in props.items()}


def verify(assembly: Assembly, openapi_by_domain: Dict[str, dict]) -> List[BoundTool]:
    """Every routed tool in the assembly, bound to its operation. Raises :class:`ManifestError`."""
    out: List[BoundTool] = []
    for tool in assembly.tools:
        if tool.route is None:
            # Edge-owned: this service implements it, so there is no other service to check against.
            continue
        spec = openapi_by_domain.get(tool.domain)
        if not spec:
            raise ManifestError(
                f"{tool.domain}/{tool.name}: no OpenAPI from {tool.domain} — refusing to bind a "
                "tool blind; a surface nobody checked is a surface nobody can trust")
        method, path = tool.route["method"], tool.route["path"]
        op = _operation(spec, method, path)
        if op is None:
            raise ManifestError(
                f"{tool.domain}/{tool.name}: {tool.domain} does not serve {method} {path} — the "
                "manifest names a route its own service does not have")
        # The DESCRIPTION travels with the schema. It is the owning route's own words about that
        # argument, and it is the only thing an agent reads before deciding what to put there —
        # dropping it publishes a typed blank.
        query_params = {}
        for pspec in (op.get("parameters") or []):
            if not pspec.get("name"):
                continue
            schema = dict(pspec.get("schema") or {})
            if pspec.get("description") and "description" not in schema:
                schema["description"] = pspec["description"]
            query_params[pspec["name"]] = schema
        body_params = _body_params(op, spec)
        # ONE ROUTE, ONE PLACE TO LOOK AN ARGUMENT UP. A name published in both `parameters` and
        # `requestBody` is an OpenAPI shape this design has no rule for — refusing it is cheaper
        # than guessing which half of the forward it belongs to.
        collision = set(query_params) & set(body_params)
        if collision:
            raise ManifestError(
                f"{tool.domain}/{tool.name}: {method} {path} names {sorted(collision)} in both its "
                "query parameters and its JSON body — one route, one place to look an argument up")
        params = {**query_params, **body_params}
        path_params = tuple(_PATH_PARAM.findall(path))
        declared = {}
        declared_body = []
        for arg in tool_arguments(tool):
            if arg in path_params:
                continue
            if arg not in params:
                raise ManifestError(
                    f"{tool.domain}/{tool.name}: declares the argument {arg!r}, which "
                    f"{method} {path} does not take — an argument the route ignores is a success "
                    "the agent reports for something that did not happen")
            declared[arg] = params[arg]
            if arg in body_params:
                declared_body.append(arg)
        out.append(BoundTool(
            tool=tool,
            description=_describe(op),
            parameters=declared,
            path_params=path_params,
            body_params=tuple(declared_body),
        ))
    return out


def tool_arguments(tool: Tool) -> tuple:
    """The arguments a manifest declared for a tool. Kept as a function rather than a field so the
    manifest shape and the bind step have one reader between them."""
    return tuple(getattr(tool, "arguments", ()) or ())
