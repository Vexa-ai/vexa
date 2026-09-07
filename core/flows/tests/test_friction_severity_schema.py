"""F-D20 (d) — `POST /friction`'s severity vocabulary must reach the caller from the CONTRACT.

The original defect stands and is still what this file is about: `FRICTION_SEVERITIES` lived in a
Python tuple that no consumer could see, so a caller reading the served OpenAPI — the document
every generated client and every tool manifest is built from — learned the accepted words only by
sending a wrong one and reading the refusal.

WHERE THE WORDS TRAVEL CHANGED IN 0.12.27, and the change is a founder ruling rather than a drift.
F-D26 (prod, 2026-09-04) lost twelve friction reports in twenty minutes because the route 400ed a
word outside the tuple, and F-D27 generalised the fix: no value a caller sends or omits is refused
on this route. So `severity` and `kind` are FREE TEXT, stored as sent, and the vocabulary is
guidance rather than a gate.

That makes an `enum` the one shape the schema must NOT use, and not as a matter of taste: the MCP
SDK validates a call against the tool's `inputSchema` before the tool is ever entered
(`mcp/server/lowlevel/server.py`), so an enum here would destroy the report one hop EARLIER than
the 400 did — the fix for the defect, becoming the defect. The words ride in the argument's own
DESCRIPTION instead, which reaches `tools/list` and refuses nothing.

The rows are still parametrized off `FRICTION_SEVERITIES` itself, so the served contract cannot
drift from the tuple: adding a severity adds a test that the description carries it.
"""
from __future__ import annotations

import os

import pytest

_KEYS = ("VEXA_FLOWS_API_KEY", "VEXA_FLOWS_ADMIN_KEY", "INTERNAL_API_SECRET", "VEXA_FLOWS_DB_URL")
_PRIOR_ENV = {k: os.environ.get(k) for k in _KEYS}
os.environ.setdefault("VEXA_FLOWS_API_KEY", "test-flows-key")
os.environ.setdefault("VEXA_FLOWS_ADMIN_KEY", "test-admin-key-not-a-placeholder")
os.environ.setdefault("INTERNAL_API_SECRET", "test-internal-secret")
os.environ["VEXA_FLOWS_DB_URL"] = "postgresql+psycopg://friction:unreachable@127.0.0.1:1/flows"
try:
    from flows_integrations import flows_api  # noqa: E402
finally:
    for _k, _v in _PRIOR_ENV.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v


def _severity_param() -> dict:
    """`severity` as the served OpenAPI document describes it."""
    spec = flows_api.app.openapi()
    params = spec["paths"]["/friction"]["post"]["parameters"]
    return next(p for p in params if p["name"] == "severity")


def _enum_anywhere(schema: dict):
    """Any `enum` the served schema carries, directly or behind an `allOf`/`anyOf` wrapper."""
    found = schema.get("enum")
    for branch in (schema.get("allOf") or []) + (schema.get("anyOf") or []):
        found = found or branch.get("enum")
    return found


def test_the_route_schema_states_the_severity_vocabulary():
    schema = _severity_param()["schema"]
    described = (schema.get("description") or "")
    assert described, f"severity carries no description at all: {schema}"
    missing = [w for w in flows_api.FRICTION_SEVERITIES if w not in described]
    assert not missing, f"the served contract never names {missing}: {described!r}"


@pytest.mark.parametrize("severity", flows_api.FRICTION_SEVERITIES)
def test_every_value_the_handler_suggests_is_in_the_served_contract(severity):
    assert severity in (_severity_param()["schema"].get("description") or "")


def test_the_vocabulary_is_never_an_enum():
    """THE SHAPE IT MUST NOT ARRIVE IN (F-D26). An `enum` on this argument is validated by the MCP
    SDK against the tool's own `inputSchema` and the call is refused before the route is entered —
    so a report using an unlisted word would be destroyed one hop earlier than the 400 that lost
    twelve of them. Guidance in front of the agent; the decision about an unrecognised word belongs
    to the route that stores it, and that route refuses nothing."""
    for name in ("severity", "kind"):
        param = next(p for p in flows_api.app.openapi()["paths"]["/friction"]["post"]["parameters"]
                     if p["name"] == name)
        assert _enum_anywhere(param["schema"]) is None, (
            f"{name} is published as an enum — the MCP SDK would refuse the call instead of "
            "forwarding the report (F-D26)")


def test_the_default_is_still_annoyance():
    """The handler's default is what a caller who says nothing gets; the contract must say the
    same thing, or a generated client sends `null` where the service assumed a word."""
    param = _severity_param()
    schema = param["schema"]
    default = schema.get("default")
    for branch in (schema.get("allOf") or []) + (schema.get("anyOf") or []):
        default = default if default is not None else branch.get("default")
    assert default == "annoyance"
