"""F-D20 (d) — `POST /friction`'s `severity` enum exists in the handler and not in the contract.

`FRICTION_SEVERITIES = ("blocker", "annoyance", "papercut", "idea")` and the handler 400s anything
else, naming the four in the error body. The ROUTE SCHEMA declared `severity` as a bare string
with a default — so the OpenAPI document flows-api serves, which is the contract every generated
client and every tool manifest is built from, said any string was acceptable. A caller reading the
contract learns the accepted set only by sending a wrong one and reading the refusal.

That is the same shape as a declaration that documents nothing (F-D20 b, one file up): the truth
lived in a Python tuple that no consumer could see.

The rows are parametrized off `FRICTION_SEVERITIES` itself, so the schema cannot drift from the
handler: adding a severity to the tuple adds a test that the schema carries it.
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


def test_the_route_schema_declares_the_severity_enum():
    schema = _severity_param()["schema"]
    # The enum may sit directly on the schema or behind an `allOf`/`anyOf` wrapper depending on
    # how the default is expressed — read whichever, and fail loudly if it is nowhere.
    found = schema.get("enum")
    if found is None:
        for branch in (schema.get("allOf") or []) + (schema.get("anyOf") or []):
            found = found or branch.get("enum")
    assert found is not None, f"no enum anywhere in the served schema: {schema}"
    assert sorted(found) == sorted(flows_api.FRICTION_SEVERITIES)


@pytest.mark.parametrize("severity", flows_api.FRICTION_SEVERITIES)
def test_every_value_the_handler_accepts_is_in_the_schema(severity):
    schema = _severity_param()["schema"]
    found = schema.get("enum") or []
    for branch in (schema.get("allOf") or []) + (schema.get("anyOf") or []):
        found = found or branch.get("enum") or []
    assert severity in found


def test_the_default_is_still_annoyance():
    """The handler's default is what a caller who says nothing gets; the contract must say the
    same thing, or a generated client sends `null` where the service assumed a word."""
    param = _severity_param()
    schema = param["schema"]
    default = schema.get("default")
    for branch in (schema.get("allOf") or []) + (schema.get("anyOf") or []):
        default = default if default is not None else branch.get("default")
    assert default == "annoyance"
