"""The agent role acts only for a person the gateway signed for (gateway-identity.v1).

agent-api holds the agent key, so the assertion alone would let agent-api — or anything that took its
key — name any actor it liked. Every agent-role call must therefore also carry the gateway's
X-Vexa-Identity, forwarded unchanged: the broker verifies it with the gateway's PUBLIC key and
requires its subject to be the assertion's actor. The human role (the terminal, which resolves the
person from the sign-in cookie against identity) and the git role are unchanged.
"""
import json
import time

import pytest

from conftest import gateway_identity
from credential_broker import identity_token


def _events(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()
            if '"assertion_refused"' in line]


def test_the_valid_path_is_served(signed, connection):
    cid = connection(actor="u1")
    r = signed("agent", "GET", "/api/connections", actor="u1")
    assert r.status_code == 200
    assert [c["id"] for c in r.json()["connections"]] == [cid]


@pytest.mark.parametrize("identity,kind", [
    (None, "identity_missing"),
    ("", "identity_missing"),
    ("v1.not.a-token", "identity_invalid"),
])
def test_an_agent_call_without_a_valid_gateway_signature_is_refused(signed, capsys, identity, kind):
    capsys.readouterr()
    r = signed("agent", "GET", "/api/connections", actor="u1", identity=identity)
    assert r.status_code == 401
    assert r.json() == {"detail": "Product identity refused"}
    assert _events(capsys)[-1]["fields"]["kind"] == kind


def test_a_signature_by_any_key_but_the_gateways_is_refused(signed, capsys):
    capsys.readouterr()
    forged = gateway_identity("u1", key=identity_token.generate_signing_key())
    assert signed("agent", "GET", "/api/connections", actor="u1", identity=forged).status_code == 401
    assert _events(capsys)[-1]["fields"]["kind"] == "identity_invalid"


def test_an_expired_signature_is_refused(signed):
    stale = gateway_identity("u1", now=int(time.time()) - 600, ttl_sec=60)
    assert signed("agent", "GET", "/api/connections", actor="u1", identity=stale).status_code == 401


def test_a_signature_for_another_person_is_refused(signed, connection, capsys):
    """agent-api holding a real signature for u2 cannot use it to read u1's connections: the signed
    subject must be the assertion's actor."""
    connection(actor="u1")
    capsys.readouterr()
    r = signed("agent", "GET", "/api/connections", actor="u1", identity=gateway_identity("u2"))
    assert r.status_code == 401
    assert _events(capsys)[-1]["fields"]["kind"] == "identity_mismatch"


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/api/setup", {"provider": "custom_secret", "label": "X"}),
    ("POST", "/api/connections/" + "a" * 32 + "/read", {"action": "gmail.search"}),
    ("POST", "/api/connections/" + "a" * 32 + "/call", {"parameters": {}}),
    ("POST", "/api/connections/" + "a" * 32 + "/draft",
     {"request_id": "req-00001", "recipient": "a@b.c", "subject": "s", "body": "b"}),
])
def test_every_agent_route_needs_the_signature(signed, method, path, body):
    assert signed("agent", method, path, body, actor="u1", identity=None).status_code == 401


def test_the_human_and_git_roles_do_not_carry_a_gateway_signature(signed):
    assert signed("human", "GET", "/api/connections", actor="u1").status_code == 200
    r = signed("git", "POST", "/api/internal/git-secret", {"name": "pat/u1", "action": "get"}, actor="u1")
    assert r.status_code == 200


def test_an_unusable_identity_key_refuses_agent_calls(signed, broker, tmp_path):
    """A deployment whose key file was replaced by the private key (or anything else) refuses the
    agent role rather than trusting it; read per use, so this takes effect without a restart."""
    bad = tmp_path / "now-a-private-key.pem"
    bad.write_bytes(identity_token.private_key_pem(identity_token.generate_signing_key()))
    object.__setattr__(broker.settings, "identity_public_key_file", str(bad))
    assert signed("agent", "GET", "/api/connections", actor="u1").status_code == 401


def test_the_broker_holds_a_key_that_cannot_sign(keys):
    key = identity_token.read_verify_key(keys["identity"])
    with pytest.raises(TypeError):
        identity_token.sign(key, {"sub": "u1"})
