"""L3 — the HTTP surface, enumerated: no route returns a secret, and no route auto-accepts.

Three levels of evidence, weakest to strongest:

1. The route table is written out. A new route fails this test until someone adds it here, which
   is the moment to ask what it returns.
2. Every response model is walked recursively for a field that could carry material.
3. A real key is set through the API, then **every readable route is called and its bytes are
   searched for it**. That is the check the settings-read defect needed: not "does this endpoint
   leak" but "does any of them".
"""

from __future__ import annotations

import ast
import inspect

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from context_stack import api
from context_stack.api import create_router
from context_stack.store import ContextStackStore

from conftest import MEMBER, OUTSIDER, OWNER

KEY = "sk-live-do-not-return-me-9999"

EXPECTED_ROUTES = {
    ("POST", "/workspaces"),
    ("GET", "/workspaces/{workspace_id}"),
    ("POST", "/workspaces/{workspace_id}/members"),
    ("DELETE", "/workspaces/{workspace_id}/members/{subject}"),
    ("GET", "/stack"),
    ("POST", "/context/deltas"),
    ("GET", "/workspaces/{workspace_id}/context"),
    ("GET", "/workspaces/{workspace_id}/proposals"),
    ("POST", "/proposals/{proposal_id}/accept"),
    ("POST", "/proposals/{proposal_id}/reject"),
    ("PUT", "/workspaces/{workspace_id}/secrets/{name}"),
    ("GET", "/workspaces/{workspace_id}/secrets"),
    ("GET", "/workspaces/{workspace_id}/secrets/{name}"),
    ("DELETE", "/workspaces/{workspace_id}/secrets/{name}"),
}

FORBIDDEN_FIELDS = {"material", "value", "secret", "plaintext", "credential", "api_key", "token"}


@pytest.fixture
def router():
    """The module's own surface. Enumerated directly, so FastAPI's documentation endpoints — the
    app's, not ours — never enter the count."""
    return create_router(lambda: None)


@pytest.fixture
def client(session_factory):
    """An app carrying only this router, with a fresh session per request."""

    async def get_store():
        async with session_factory() as session:
            yield ContextStackStore(session)

    app = FastAPI()
    app.include_router(create_router(get_store))
    with TestClient(app) as client:
        yield client


def _as(client, subject: str):
    client.headers.update({"X-User-Email": subject})
    return client


def _routes(router) -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in router.routes
        for method in getattr(route, "methods", ()) or ()
        if method not in {"HEAD", "OPTIONS"}
    }


def _fields(model, seen=None) -> set[str]:
    """Every field name reachable from a response model, recursively."""
    seen = seen if seen is not None else set()
    if model in seen or not (isinstance(model, type) and issubclass(model, BaseModel)):
        return set()
    seen.add(model)
    names: set[str] = set()
    for name, field in model.model_fields.items():
        names.add(name)
        annotation = field.annotation
        for candidate in (annotation, *getattr(annotation, "__args__", ())):
            names |= _fields(candidate, seen)
    return names


# ── 1. the table ──────────────────────────────────────────────────────────────────────────────


def test_the_route_table_is_exactly_this(router):
    """Adding a route means adding a row here, which is where its response gets looked at."""
    assert _routes(router) == EXPECTED_ROUTES


def test_there_is_exactly_one_accept_route_and_no_bulk_form(router):
    """No auto-accept, no accept-all: the API cannot drain a triage queue in one call."""
    accepting = [path for _, path in _routes(router) if "accept" in path]

    assert accepting == ["/proposals/{proposal_id}/accept"]
    assert not [p for _, p in _routes(router) if any(w in p for w in ("auto", "bulk", "batch"))]


def test_no_route_reaches_the_one_reader_of_material():
    """``api.py`` does not import ``context_stack.material``. Read from the parse tree, so the
    docstring may name the rule without breaking it."""
    tree = ast.parse(inspect.getsource(api))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    referenced = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }

    assert not [m for m in imported if m and "material" in m]
    assert "use_material" not in referenced


# ── 2. the response models ────────────────────────────────────────────────────────────────────


def test_no_response_model_has_a_field_a_secret_could_ride_in(router):
    """Walk every declared response model, recursively, for a field that could carry material."""
    offenders = {}
    for route in router.routes:
        model = getattr(route, "response_model", None)
        if model is None:
            continue
        for candidate in (model, *getattr(model, "__args__", ())):
            leaking = _fields(candidate) & FORBIDDEN_FIELDS
            if leaking:
                offenders[route.path] = leaking

    assert offenders == {}


# ── 3. the live sweep ─────────────────────────────────────────────────────────────────────────


def test_a_real_key_comes_back_from_no_route(client):
    """Set a key through the API, then call every readable route and search the bytes for it."""
    owner = _as(client, OWNER)
    owner.post(
        "/workspaces",
        json={
            "workspace_id": "acme",
            "name": "Acme",
            "address": "acme@vexa.ai",
            "policy": "group",
            "member_emails": [MEMBER],
        },
    ).raise_for_status()
    set_response = owner.put(
        "/workspaces/acme/secrets/llm_api_key", json={"material": KEY}
    )
    assert set_response.status_code == 200
    assert set_response.json()["last4"] == "9999"

    owner.post(
        "/context/deltas",
        json={"workspace_id": "acme", "path": "kg/x.md", "body": "a delta"},
    ).raise_for_status()

    readable = [
        "/workspaces/acme",
        "/workspaces/acme/secrets",
        "/workspaces/acme/secrets/llm_api_key",
        "/workspaces/acme/proposals",
        "/workspaces/acme/context?path=kg/x.md",
        "/stack",
        "/stack?mode=free",
        "/openapi.json",
    ]

    for path in readable:
        response = owner.get(path)
        assert KEY not in response.text, f"{path} returned the secret"
        assert KEY.encode() not in response.content, f"{path} returned the secret"

    # The set route is the only place a value appears on this surface, and it appears going in.
    assert KEY not in set_response.text


def test_every_readable_route_is_swept(router):
    """The sweep above is only as good as its coverage: assert it hit every GET route there is."""
    swept = {"/workspaces/{workspace_id}", "/workspaces/{workspace_id}/secrets",
             "/workspaces/{workspace_id}/secrets/{name}", "/workspaces/{workspace_id}/proposals",
             "/workspaces/{workspace_id}/context", "/stack"}

    assert {path for method, path in _routes(router) if method == "GET"} == swept


# ── the surface behaves, too ──────────────────────────────────────────────────────────────────


def test_the_full_triage_round_trip_over_http(client):
    """Member proposes → it is not in context → owner accepts → it is."""
    owner = _as(client, OWNER)
    owner.post(
        "/workspaces",
        json={
            "workspace_id": "acme",
            "name": "Acme",
            "address": "acme@vexa.ai",
            "policy": "group",
            "member_emails": [MEMBER],
        },
    ).raise_for_status()

    landed = _as(client, MEMBER).post(
        "/context/deltas",
        json={"workspace_id": "acme", "path": "kg/pricing.md", "body": "Renewal Q3"},
    ).json()
    assert landed["destination"] == "proposal"
    assert _as(client, MEMBER).get("/workspaces/acme/context?path=kg/pricing.md").status_code == 404

    assert _as(client, MEMBER).post(f"/proposals/{landed['proposal_id']}/accept").status_code == 403

    accepted = _as(client, OWNER).post(
        f"/proposals/{landed['proposal_id']}/accept", json={"note": "checked"}
    )
    assert accepted.status_code == 200
    document = _as(client, MEMBER).get("/workspaces/acme/context?path=kg/pricing.md").json()
    assert document["body"] == "Renewal Q3"


def test_an_outsider_gets_403_from_the_group_routes(client):
    """The enforcement case over HTTP, with the machine-readable reason preserved."""
    owner = _as(client, OWNER)
    owner.post(
        "/workspaces",
        json={
            "workspace_id": "acme",
            "name": "Acme",
            "address": "acme@vexa.ai",
            "policy": "group",
            "member_emails": [MEMBER],
        },
    ).raise_for_status()
    owner.put("/workspaces/acme/secrets/llm_api_key", json={"material": KEY}).raise_for_status()

    outsider = _as(client, OUTSIDER)
    for path in (
        "/workspaces/acme",
        "/workspaces/acme/proposals",
        "/workspaces/acme/secrets",
        "/workspaces/acme/secrets/llm_api_key",
    ):
        response = outsider.get(path)
        assert response.status_code == 403, path
        assert response.json()["detail"]["code"] in {"not-member", "not-owner"}
        assert KEY not in response.text


def test_a_caller_can_only_resolve_their_own_stack(router):
    """There is no subject parameter on /stack — a composition is built for whoever is asking."""
    signature = inspect.signature(
        next(r.endpoint for r in router.routes if getattr(r, "path", "") == "/stack")
    )

    assert "subject" not in signature.parameters
