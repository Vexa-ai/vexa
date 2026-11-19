"""One rule for "is a person in the loop", read from the delegation headers the identity door stamps.

`control_plane/ceiling.py` holds it: `is_delegated` (any delegation header is on the identity, an
empty one included) and `is_unwatched` (delegated, and the regime is not `human`). `require_person`
refuses an unwatched caller with `REFUSAL`, the body meeting-api's `regime.py` answers with, and the
chat door refuses every delegated caller. Each refusal is logged once.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from control_plane import ceiling
from control_plane.identity_token import DELEGATION_HEADERS

REGIME = DELEGATION_HEADERS["regime"]
WORKSPACES = DELEGATION_HEADERS["workspaces"]
TARGET = DELEGATION_HEADERS["target"]


def _app() -> TestClient:
    app = FastAPI()

    @app.get("/probe")
    def probe(request: Request):
        return {"delegated": ceiling.is_delegated(request), "unwatched": ceiling.is_unwatched(request)}

    @app.post("/person")
    def person(request: Request):
        ceiling.require_person(request)
        return {"ok": True}

    return TestClient(app)


@pytest.mark.parametrize("headers,delegated,unwatched", [
    ({}, False, False),                                   # a person's own credential
    ({REGIME: "human"}, True, False),
    ({REGIME: "Human "}, True, False),
    ({REGIME: "autonomous"}, True, True),
    ({REGIME: "routine"}, True, True),                    # an unknown regime runs unwatched
    ({REGIME: ""}, True, True),                           # present but empty: delegated, not human
    ({WORKSPACES: "ws_a"}, True, True),                   # a ceiling with no regime
    ({TARGET: "ws_a"}, True, True),
    ({WORKSPACES: "*", REGIME: "human"}, True, False),
])
def test_the_one_rule(headers, delegated, unwatched):
    r = _app().get("/probe", headers=headers)
    assert r.json() == {"delegated": delegated, "unwatched": unwatched}


def test_an_empty_regime_is_refused_a_person_verb():
    """meeting-api refuses an identity that carries the regime header with nothing in it; agent-api
    used to let it through because it tested the header's VALUE for truth. One rule now."""
    r = _app().post("/person", headers={REGIME: ""})
    assert r.status_code == 403
    assert r.json()["detail"] == ceiling.REFUSAL


def test_the_refusal_is_meeting_api_s_refusal():
    """Both services answer with the vendored gateway-identity.v1 body (fact identity-token holds the
    copies byte-identical), and meeting-api spells no refusal of its own."""
    from control_plane import identity_token
    assert ceiling.REFUSAL is identity_token.REFUSAL
    regime_py = (Path(__file__).resolve().parents[2] / "meetings" / "services" / "meeting-api" / "src"
                 / "meeting_api" / "regime.py")
    if not regime_py.is_file():
        pytest.skip("meeting-api is not in this checkout")
    src = regime_py.read_text()
    assert "from .identity_token import REFUSAL" in src
    assert not re.search(r"^REFUSAL = ", src, re.M), "meeting-api regime.py spells its own REFUSAL again"


def test_a_refusal_is_logged_once(caplog):
    with caplog.at_level(logging.WARNING, logger="agent_api.ceiling"):
        _app().post("/person", headers={REGIME: "autonomous", "x-user-id": "7"})
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "agent_api.ceiling"]
    assert len(lines) == 1
    assert lines[0]["event"] == "refused"
    assert lines[0]["reason"] == "human_session_required"
    assert lines[0]["subject"] == "7" and lines[0]["path"] == "/person"


def test_the_person_s_own_credential_is_not_refused():
    assert _app().post("/person").status_code == 200


def test_refuse_delegated_refuses_every_regime_with_its_own_reason():
    app = FastAPI()

    @app.post("/turn")
    def turn(request: Request):
        ceiling.refuse_delegated(request, reason="delegated_dispatch", instruction="say it and stop")
        return {"ok": True}

    c = TestClient(app)
    assert c.post("/turn").status_code == 200
    for headers in ({REGIME: "human"}, {REGIME: ""}, {WORKSPACES: "*"}):
        r = c.post("/turn", headers=headers)
        assert r.status_code == 403
        assert r.json()["detail"] == {"status": "refused", "reason": "delegated_dispatch",
                                      "instruction": "say it and stop"}


OWN = ("control_plane/ceiling.py", "control_plane/api.py", "control_plane/api_shared.py",
       "control_plane/routers/chats.py", "control_plane/routers/workspaces.py")


@pytest.mark.parametrize("rel", OWN)
def test_the_delegation_header_names_are_spelled_once(rel):
    """The names come from the vendored `identity_token.DELEGATION_HEADERS`, the copy the gateway
    signs from; these modules never spell them again."""
    src = (Path(__file__).resolve().parents[1] / rel).read_text()
    for name in DELEGATION_HEADERS.values():
        assert f'"{name}"' not in src and f"'{name}'" not in src, (rel, name)


def test_require_person_raises_http_403():
    class _Req:
        headers = {REGIME: "autonomous"}
        url = type("U", (), {"path": "/x"})()
        method = "POST"
    with pytest.raises(HTTPException) as ei:
        ceiling.require_person(_Req())
    assert ei.value.status_code == 403
