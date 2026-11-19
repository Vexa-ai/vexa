"""POST /internal/signin-links/redeem — an emailed sign-in link signs in once, whichever terminal
replica redeems it (app/signin_links.py, signin.v1 SigninLinkRedeemRequest/Response).

The ledger used to live in each terminal process: with two replicas a link could be redeemed once
per replica, and a restart forgot it. These cases hold the shared record's rules: the first redeem
of a jti is the only one admitted, the record lasts no longer than the link, a store that cannot be
written refuses, and the door is the internal tier with no dev-mode escape.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
import re

from admin_api.app import signin_links
from admin_api.app.main import create_app

SECRET = "internal-secret-for-links"
JTI = "0b6f2f3e-6a2c-4c7e-9f1d-2d3b4a5c6e7f"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", SECRET)
    monkeypatch.setenv("DEV_MODE", "false")
    with TestClient(create_app()) as c:
        yield c


def _redeem(c, jti=JTI, expires_in=600, secret=SECRET):
    headers = {"X-Internal-Secret": secret} if secret is not None else {}
    return c.post("/internal/signin-links/redeem", headers=headers,
                  json={"jti": jti, "expires_at": int(time.time()) + expires_in})


def test_the_first_redeem_is_admitted_and_every_later_one_refused(client, link_ledger):
    first = _redeem(client)
    assert first.status_code == 200 and first.json() == {"first": True}
    # a second terminal replica, or the same one after a restart, asks again: refused
    for _ in range(2):
        again = _redeem(client)
        assert again.status_code == 409, again.text
    assert list(link_ledger.keys) == [signin_links.REDEEMED_PREFIX + JTI]


def test_two_links_are_independent(client):
    assert _redeem(client, jti="a-1").status_code == 200
    assert _redeem(client, jti="b-2").status_code == 200


def test_the_record_lasts_as_long_as_the_link_and_never_longer(client, link_ledger):
    assert _redeem(client, jti="short", expires_in=120).status_code == 200
    assert _redeem(client, jti="long", expires_in=10 * 24 * 3600).status_code == 200
    ttl = {k.rsplit(":", 1)[1]: v for k, v in link_ledger.keys.items()}
    assert 110 <= ttl["short"] <= 120
    assert ttl["long"] == signin_links.MAX_RECORD_SEC


def test_an_expired_link_is_refused_without_a_record(client, link_ledger):
    assert _redeem(client, expires_in=-1).status_code == 409
    assert link_ledger.keys == {}


def test_a_store_that_cannot_be_written_refuses(client, link_ledger):
    link_ledger.down = True
    r = _redeem(client)
    assert r.status_code == 503 and "first" not in r.text


@pytest.mark.parametrize("secret", [None, "wrong"])
def test_the_door_needs_the_internal_secret(client, link_ledger, secret):
    assert _redeem(client, secret=secret).status_code == 403
    assert link_ledger.keys == {}


def test_no_secret_configured_is_closed_even_in_dev_mode(monkeypatch, link_ledger):
    monkeypatch.delenv("INTERNAL_API_SECRET", raising=False)
    monkeypatch.setenv("DEV_MODE", "true")
    with TestClient(create_app()) as c:
        assert _redeem(c, secret=None).status_code == 503
    assert link_ledger.keys == {}


@pytest.mark.parametrize("body", [
    {"jti": "../../x", "expires_at": 2_000_000_000},
    {"jti": "", "expires_at": 2_000_000_000},
    {"jti": "x" * 65, "expires_at": 2_000_000_000},
    {"jti": JTI},
    {"jti": JTI, "expires_at": 2_000_000_000, "extra": 1},
])
def test_a_malformed_request_is_refused(client, link_ledger, body):
    r = client.post("/internal/signin-links/redeem", headers={"X-Internal-Secret": SECRET}, json=body)
    assert r.status_code == 422
    assert link_ledger.keys == {}


def _contract():
    for parent in Path(__file__).resolve().parents:
        p = parent / "contracts" / "signin.v1" / "signin.schema.json"
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    raise FileNotFoundError("signin.v1")


def test_request_and_answer_are_the_sealed_shapes(client):
    """The route takes and answers the signin.v1 shapes: the request's fields and jti pattern, and a
    first redemption's literal ``{"first": true}``. (No schema library in this package; the checks
    read the sealed schema itself.)"""
    defs = _contract()["$defs"]
    req_shape, resp_shape = defs["SigninLinkRedeemRequest"], defs["SigninLinkRedeemResponse"]
    req = {"jti": JTI, "expires_at": int(time.time()) + 600}
    assert set(req) == set(req_shape["required"]) == set(req_shape["properties"])
    assert re.fullmatch(req_shape["properties"]["jti"]["pattern"].strip("^$"), JTI)
    assert signin_links.SigninLinkRedeemRequest.model_fields["jti"].metadata[0].pattern == \
        req_shape["properties"]["jti"]["pattern"]
    r = client.post("/internal/signin-links/redeem", headers={"X-Internal-Secret": SECRET}, json=req)
    assert set(r.json()) == set(resp_shape["required"])
    assert r.json()["first"] is resp_shape["properties"]["first"]["const"]
