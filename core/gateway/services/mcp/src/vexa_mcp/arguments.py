"""arguments.py — an array or object argument sent as its JSON text is read as that value.

A model calling a tool with several nested arguments sometimes serialises one of them: `facts`
arrives as the string `'["…"]'` instead of the array. The MCP library checks every call against the
tool's input schema before this edge sees it, so the call was refused with `'["…"]' is not of type
'array'` — true, and impossible for the agent to act on, because what it sent and what it meant
print the same (Vexa-ai/vexa#1783, an `entity_upsert` for a meeting refused on every retry).

So, before that check and only for an argument the schema types as an array or an object:

* a string that parses as JSON of that type is replaced by the value it spells;
* for an array of strings, a string that is not a JSON array is ONE item — the owning route's own
  rule (`EntityUpsertBody._one_is_a_list`: "a single fact given as a string is one fact").

Nothing else changes: a value of any other shape still meets the schema check exactly as before,
and a string argument is never touched.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from mcp import types as mcp_types


def _types_of(schema: Any) -> set:
    """The JSON types a property schema admits (its own `type`, or its `anyOf`/`oneOf` branches)."""
    if not isinstance(schema, dict):
        return set()
    out = set()
    t = schema.get("type")
    if isinstance(t, str):
        out.add(t)
    elif isinstance(t, list):
        out.update(x for x in t if isinstance(x, str))
    for b in (schema.get("anyOf") or schema.get("oneOf") or []):
        out |= _types_of(b)
    return out


def _array_of_strings(schema: Any) -> bool:
    if not isinstance(schema, dict):
        return False
    if schema.get("type") == "array":
        items = _types_of(schema.get("items") or {})
        return not items or "string" in items       # untyped items admit a string
    return any(_array_of_strings(b) for b in (schema.get("anyOf") or schema.get("oneOf") or []))


def coerce(arguments: Optional[Dict[str, Any]], input_schema: Any) -> Optional[Dict[str, Any]]:
    """``arguments`` with each stringified array/object argument decoded (see the module header)."""
    if not isinstance(arguments, dict) or not isinstance(input_schema, dict):
        return arguments
    props = input_schema.get("properties") or {}
    out = dict(arguments)
    for name, value in arguments.items():
        if not isinstance(value, str) or name not in props:
            continue
        kinds = _types_of(props[name])
        if "string" in kinds or not ({"array", "object"} & kinds):
            continue
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            decoded = None
        if isinstance(decoded, list) and "array" in kinds:
            out[name] = decoded
        elif isinstance(decoded, dict) and "object" in kinds:
            out[name] = decoded
        elif decoded is None and value.strip() and _array_of_strings(props[name]):
            out[name] = [value]
    return out


def install(mcp: Any) -> None:
    """Decode stringified arguments ahead of the library's schema check, on every tool call."""
    server = mcp.server
    original = server.request_handlers.get(mcp_types.CallToolRequest)
    if original is None:
        return
    async def handler(req: mcp_types.CallToolRequest):
        # Read per call: a domain that joins after boot (`late.py`) re-derives `mcp.tools`.
        schema = next((t.inputSchema for t in mcp.tools if t.name == req.params.name), None)
        if schema is not None and req.params.arguments:
            req.params.arguments = coerce(req.params.arguments, schema)
        return await original(req)

    server.request_handlers[mcp_types.CallToolRequest] = handler
