"""The git role acts only for the person the gateway signed for (gateway-identity.v1).

agent-api holds the git key. Without a second signature the key alone would let it — or anything
that took the key — read and write any person's Git token and any workspace's deploy key, because
the name it asks for is the only thing that says whose it is. So every git-role call carries the
gateway's X-Vexa-Identity for the person the request acts for, forwarded unchanged: the broker
verifies it with the gateway's PUBLIC key, requires its subject to be the assertion's actor, and
then authorizes the named credential against that person:

* ``pat/<sub>`` and ``deploy/user-<sub>.(priv|pub)`` — the person's own;
* ``deploy/ws-<id>.(priv|pub)`` — a shared workspace in the person's signed memberships.
"""
import json
import time

import pytest

from conftest import gateway_identity
from credential_broker import identity_token

PATH = "/api/internal/git-secret"


def git(signed, name, *, actor="u1", identity="signed", action="get", value=None):
    return signed("git", "POST", PATH, {"name": name, "action": action, "value": value}, actor=actor, identity=identity)


def refused_kind(capsys):
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if '"assertion_refused"' in line]
    return events[-1]["fields"]["kind"] if events else None


@pytest.mark.parametrize("name", ["pat/u1", "deploy/user-u1.priv", "deploy/user-u1.pub"])
def test_the_person_reaches_their_own_credentials(signed, name):
    assert git(signed, name, action="put", value="fixture").json() == {"found": True, "value": "fixture"}
    assert git(signed, name).json() == {"found": True, "value": "fixture"}


def test_a_shared_workspace_key_needs_the_signed_membership(signed):
    member = gateway_identity("u1", workspaces=["team", "other"])
    assert git(signed, "deploy/ws-team.priv", identity=member, action="put", value="k").status_code == 200
    assert git(signed, "deploy/ws-team.priv", identity=member).json()["value"] == "k"
    outsider = gateway_identity("u1", workspaces=["other"])
    assert git(signed, "deploy/ws-team.priv", identity=outsider).status_code == 403
    assert git(signed, "deploy/ws-team.priv", identity=gateway_identity("u1")).status_code == 403


@pytest.mark.parametrize("name", ["pat/u2", "deploy/user-u2.priv", "deploy/user-u2.pub"])
def test_another_persons_credentials_are_refused(signed, name):
    r = git(signed, name)
    assert r.status_code == 403 and r.json() == {"detail": "Git credential scope refused"}


def test_naming_the_owner_as_the_actor_is_no_longer_enough(signed, capsys):
    """The old check compared the actor agent-api named with the owner in the name it asked for —
    two values from one caller. An actor the gateway did not sign for is refused before any name
    is looked at."""
    capsys.readouterr()
    r = git(signed, "deploy/user-u2.priv", actor="user-u2", identity=gateway_identity("u1"))
    assert r.status_code == 401 and refused_kind(capsys) == "identity_mismatch"


def test_a_call_without_the_gateway_signature_is_refused(signed, capsys):
    capsys.readouterr()
    assert git(signed, "pat/u1", identity=None).status_code == 401
    assert refused_kind(capsys) == "identity_missing"


def test_a_signature_by_any_key_but_the_gateways_is_refused(signed, capsys):
    capsys.readouterr()
    forged = gateway_identity("u1", key=identity_token.generate_signing_key(), workspaces=["team"])
    assert git(signed, "deploy/ws-team.priv", identity=forged).status_code == 401
    assert refused_kind(capsys) == "identity_invalid"


def test_an_expired_signature_is_refused(signed, capsys):
    capsys.readouterr()
    stale = gateway_identity("u1", now=int(time.time()) - 600, ttl_sec=60)
    assert git(signed, "pat/u1", identity=stale).status_code == 401
    assert refused_kind(capsys) == "identity_invalid"


def test_a_signature_for_another_person_is_refused(signed, capsys):
    capsys.readouterr()
    assert git(signed, "pat/u1", identity=gateway_identity("u2")).status_code == 401
    assert refused_kind(capsys) == "identity_mismatch"


def test_the_owner_is_read_from_the_name_exactly(signed):
    """`deploy/user-a.pub.priv` is the private half of subject `a.pub`'s key, not of `a`'s."""
    assert git(signed, "deploy/user-a.pub.priv", actor="a").status_code == 403
    assert git(signed, "deploy/user-a.pub.priv", actor="a.pub").status_code == 200


def test_the_audit_names_the_signed_person(signed, broker):
    git(signed, "pat/u1", action="put", value="fixture")
    rows = broker.sql("SELECT actor, action, outcome FROM audit WHERE connection='pat/u1'", rows=True)
    assert rows and {r["actor"] for r in rows} == {"u1"}
    assert "fixture" not in json.dumps(rows)
