"""The bot-account route (role `bots`): each action, owner-filtered listing, owner/platform pinning,
destruction of every stored version, refusal of every other role and of an unconfigured key, and
store faults surfaced as 503."""
import base64
import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from credential_broker import assertion
from credential_broker.app import create_app
from credential_broker.broker import Broker

PATH = "/api/internal/bot-account"
A = "a" * 32
B = "b" * 32
FILES = [{"path": "Default/Cookies", "data": base64.b64encode(b"fixture-profile").decode()}]


def call(signed, body, *, role="bots", actor="meeting-api"):
    return signed(role, "POST", PATH, body, actor=actor)


def put(signed, aid=A, *, owner="user:42", platform="google", files=FILES, name="Notetaker"):
    return call(signed, {"action": "put", "id": aid, "files": files,
                         "account": {"owner": owner, "platform": platform, "display_name": name,
                                     "created_by": "user:42"}})


def test_put_get_state_list_delete(signed, store):
    r = put(signed)
    assert r.status_code == 200, r.text
    (account,) = r.json()["accounts"]
    assert r.json()["files"] is None
    assert account["state"] == "ok" and account["verified_at"] and account["owner"] == "user:42"
    assert store.rows["bot/" + A][-1]["owner"] == "user:42"           # sealed with the account's owner

    got = call(signed, {"action": "get", "id": A}).json()
    assert got["files"] == FILES and got["accounts"][0]["id"] == A

    out = call(signed, {"action": "state", "id": A, "state": "signed_out"}).json()["accounts"][0]
    assert out["state"] == "signed_out" and out["verified_at"] == account["verified_at"]
    assert call(signed, {"action": "state", "id": A, "state": "ok"}).json()["accounts"][0]["state"] == "ok"

    listed = call(signed, {"action": "list", "owners": ["user:42"]}).json()
    assert [a["id"] for a in listed["accounts"]] == [A] and listed["files"] is None

    gone = call(signed, {"action": "delete", "id": A}).json()
    assert [a["id"] for a in gone["accounts"]] == [A]
    assert call(signed, {"action": "get", "id": A}).status_code == 404
    assert call(signed, {"action": "delete", "id": A}).json() == {"accounts": [], "files": None}


def test_list_returns_only_the_named_owners(signed):
    put(signed, A, owner="user:42")
    put(signed, B, owner="user:7", platform="zoom")
    put(signed, "c" * 32, owner="company", platform="microsoft")
    ids = lambda owners: {a["id"] for a in call(signed, {"action": "list", "owners": owners}).json()["accounts"]}
    assert ids(["user:42"]) == {A}
    assert ids(["user:42", "company"]) == {A, "c" * 32}
    assert ids([]) == set()


def test_put_on_an_existing_id_keeps_owner_and_platform(signed):
    first = put(signed).json()["accounts"][0]
    assert put(signed, owner="user:7").status_code == 409
    assert put(signed, owner="company").status_code == 409
    assert put(signed, platform="zoom").status_code == 409
    again = put(signed, files=[{"path": "Local State", "data": "e30="}], name="Renamed")
    assert again.status_code == 200
    account = again.json()["accounts"][0]
    assert account["created_at"] == first["created_at"] and account["display_name"] == "Renamed"
    assert call(signed, {"action": "get", "id": A}).json()["files"] == [{"path": "Local State", "data": "e30="}]


def test_delete_destroys_every_stored_version(signed, store):
    put(signed)
    put(signed, files=[{"path": "Local State", "data": "e30="}])
    assert len(store.rows["bot/" + A]) == 2
    call(signed, {"action": "delete", "id": A})
    assert "bot/" + A not in store.rows
    assert A not in store.rows["bot-index"][-1]["accounts"]


def test_missing_account_is_404(signed):
    assert call(signed, {"action": "get", "id": B}).status_code == 404
    assert call(signed, {"action": "state", "id": B, "state": "ok"}).status_code == 404


@pytest.mark.parametrize("body", [
    {"action": "list"},
    {"action": "get"},
    {"action": "put", "id": A, "files": FILES},
    {"action": "state", "id": A},
    {"action": "get", "id": "NOT-HEX"},
    {"action": "list", "owners": ["user:abc"]},
    {"action": "put", "id": A, "files": [{"path": "../escape", "data": "e30="}],
     "account": {"owner": "user:1", "platform": "google", "display_name": "x", "created_by": "user:1"}},
    {"action": "put", "id": A, "files": [{"path": "ok", "data": "not base64!"}],
     "account": {"owner": "user:1", "platform": "google", "display_name": "x", "created_by": "user:1"}},
    {"action": "put", "id": A, "files": FILES * 129,
     "account": {"owner": "user:1", "platform": "google", "display_name": "x", "created_by": "user:1"}},
])
def test_invalid_bodies_are_422(signed, body):
    assert call(signed, body).status_code == 422


@pytest.mark.parametrize("role", ["agent", "human", "git"])
def test_other_roles_are_refused(signed, role):
    r = call(signed, {"action": "list", "owners": ["user:42"]}, role=role, actor="42")
    assert r.status_code == 403


def test_bots_role_reaches_no_other_route(signed):
    assert signed("bots", "GET", "/api/connections", actor="meeting-api").status_code == 403
    r = signed("bots", "POST", "/api/internal/git-secret", {"name": "pat/2", "action": "get"}, actor="2")
    assert r.status_code == 403


def test_unconfigured_bots_key_refuses_everyone(settings, store, keys):
    bare = replace(settings, key_files={r: keys[r] for r in ("agent", "human", "git")})
    client = TestClient(create_app(Broker(bare, store)))
    raw = json.dumps({"action": "list", "owners": ["company"]}).encode()
    for role, expected in (("bots", 401), ("human", 403)):
        key = Path(keys[role]).read_bytes().strip()
        h = {assertion.HEADER: assertion.sign(key, role=role, actor="7", session="s", method="POST", path=PATH, body=raw),
             "Content-Type": "application/json"}
        assert client.post(PATH, content=raw, headers=h).status_code == expected


def test_store_failure_is_503_not_absence(signed, store, capsys):
    put(signed)
    store.fail = "transport"
    for body in ({"action": "list", "owners": ["user:42"]}, {"action": "get", "id": A}, {"action": "delete", "id": A}):
        r = call(signed, body)
        assert r.status_code == 503 and r.json() == {"detail": "Credential store unavailable", "reason": "store_unavailable"}
    assert '"source":"store","kind":"transport"' in capsys.readouterr().out


def test_audit_and_logs_carry_no_profile_bytes(signed, broker, capsys):
    put(signed, files=[{"path": "Default/Cookies", "data": base64.b64encode(b"PROFILE-CANARY").decode()}])
    rows = broker.sql("SELECT action, outcome, actor FROM audit WHERE connection=?", (A,), rows=True)
    assert {(r["action"], r["outcome"]) for r in rows} == {("bot.account.put", "requested"), ("bot.account.put", "success")}
    assert all(r["actor"] == "meeting-api" for r in rows)
    out = capsys.readouterr().out
    assert "PROFILE-CANARY" not in out and base64.b64encode(b"PROFILE-CANARY").decode() not in out
