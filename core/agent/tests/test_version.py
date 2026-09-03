"""`GET /api/version` — the fact a blue/green swap and an open tab both ask for (PRD decision 39).

The ritual it replaces was a human one: the founder was told to go "out" while containers were
recreated and "in" when they were back, because a container swapped under an open tab fails one
request (F20) and because the terminal and the server must move together (F55/F77). Neither reason
needs a person. This endpoint is what lets the machine do both jobs:

  * the swap script probes it on the NEW container, from the host, before any traffic moves;
  * the swap script compares its `api` to the terminal image's baked pairing label and REFUSES a
    terminal that would lead the server;
  * the terminal polls it and offers a reload when `sha` changes underneath.

So the tests below hold exactly the properties those three uses depend on: it answers without a
session, it answers before the company layer is written, it reports the CONTAINER's stamp rather
than anything read off a source tree, and `api` is a stable integer.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from control_plane import global_layer, version
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from shared.config import load_settings
from tests.test_api import _FakeIdentity, _FakeRuntime


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity())))


def test_version_reports_the_containers_stamp_and_the_contract(client, monkeypatch):
    monkeypatch.setenv("VEXA_BUILD_SHA", "line-deadbeef")
    r = client.get("/api/version")
    assert r.status_code == 200
    assert r.json() == {"service": "agent-api", "sha": "line-deadbeef", "api": version.API_VERSION}


def test_unstamped_build_is_unknown_not_empty(client, monkeypatch):
    """A consumer comparing two answers must be able to tell "nobody stamped this" from a build
    whose name is the empty string — otherwise every unstamped container looks identical to every
    other one and the terminal never offers a reload."""
    monkeypatch.setenv("VEXA_BUILD_SHA", "   ")
    assert client.get("/api/version").json()["sha"] == "unknown"
    monkeypatch.delenv("VEXA_BUILD_SHA", raising=False)
    assert client.get("/api/version").json()["sha"] == "unknown"


def test_api_is_an_integer_contract_number(client):
    assert isinstance(client.get("/api/version").json()["api"], int)


def test_no_session_needed(client):
    """Probed from the host before any traffic is switched onto the container: there is no user."""
    r = client.get("/api/version")
    assert r.status_code == 200


def test_answers_through_the_company_layer_gate(client, monkeypatch):
    """The gate refuses every `/api/*` path to a non-admin on an unwritten instance. That is right
    for everything a person does and wrong here: a swap probing a brand-new container has no
    admin, and a tab polling for "did the thing under me move" would be told 403 forever."""
    monkeypatch.setattr(global_layer, "instance_state",
                        lambda *_a, **_k: {"admin_exists": True, "global_setup": "missing", "company": None})
    monkeypatch.setattr(global_layer, "is_admin", lambda *_a, **_k: False)
    r = client.get("/api/version", headers={"X-User-Id": "u_stranger"})
    assert r.status_code == 200, "the gate swallowed the one endpoint that must answer from outside"
    # and the gate is otherwise still closed — this test must not be passing because it is off
    assert client.get("/api/models", headers={"X-User-Id": "u_stranger"}).status_code == 403
