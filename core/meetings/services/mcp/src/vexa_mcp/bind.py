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
        params = {}
        for spec in (op.get("parameters") or []):
            if not spec.get("name"):
                continue
            schema = dict(spec.get("schema") or {})
            if spec.get("description") and "description" not in schema:
                schema["description"] = spec["description"]
            params[spec["name"]] = schema
        path_params = tuple(_PATH_PARAM.findall(path))
        declared = {}
        for arg in tool_arguments(tool):
            if arg in path_params:
                continue
            if arg not in params:
                raise ManifestError(
                    f"{tool.domain}/{tool.name}: declares the argument {arg!r}, which "
                    f"{method} {path} does not take — an argument the route ignores is a success "
                    "the agent reports for something that did not happen")
            declared[arg] = params[arg]
        out.append(BoundTool(
            tool=tool,
            description=_describe(op),
            parameters=declared,
            path_params=path_params,
        ))
    return out


def tool_arguments(tool: Tool) -> tuple:
    """The arguments a manifest declared for a tool. Kept as a function rather than a field so the
    manifest shape and the bind step have one reader between them."""
    return tuple(getattr(tool, "arguments", ()) or ())
