"""The Git credential RPC: git role only, names the signed person owns, CAS writes, tombstones, store faults."""
import pytest

from conftest import gateway_identity


def git(signed, action, value=None, *, name="pat/2", actor="2", identity="signed"):
    return signed("git", "POST", "/api/internal/git-secret", {"name": name, "action": action, "value": value},
                  actor=actor, identity=identity)


def test_migrate_write_revoke_and_no_resurrection(signed, store, broker):
    assert git(signed, "get").json() == {"found": False, "value": None}
    assert git(signed, "migrate", "fixture").json()["value"] == "fixture"
    assert git(signed, "migrate", "other").json()["value"] == "fixture"
    assert git(signed, "put").json() == {"found": True, "value": None}
    assert git(signed, "migrate", "old").json()["value"] is None
    assert [c[3] for c in store.puts("git/pat/2")] == [0, 1]
    audit = broker.sql("SELECT operation_id FROM audit WHERE connection='pat/2'", rows=True)
    assert audit and all(a["operation_id"] for a in audit)


def test_deploy_keys_are_scoped_to_the_signed_person(signed):
    # The actor is the person the gateway signed for; a desk key is theirs by subject, a shared
    # workspace's key by a membership signed with them (tests/test_git_identity.py has the rest).
    assert git(signed, "put", "k", name="deploy/user-2.priv").status_code == 200
    assert git(signed, "get", name="deploy/user-3.priv").status_code == 403
    member = gateway_identity("2", workspaces=["team"])
    assert git(signed, "get", name="deploy/ws-team.pub", identity=member).status_code == 200
    assert git(signed, "get", name="deploy/ws-team.pub").status_code == 403


@pytest.mark.parametrize("name", ["pat/../2", "deploy/user-2.key", "other/2", "pat/"])
def test_invalid_names_refused(signed, name):
    assert git(signed, "get", name=name).status_code in (403, 422)


def test_store_failure_is_not_absence(signed, store, capsys):
    store.fail = "transport"
    r = git(signed, "get")
    assert r.status_code == 503 and r.json() == {"detail": "Credential store unavailable", "reason": "store_unavailable"}
    assert '"source":"store","kind":"transport"' in capsys.readouterr().out
