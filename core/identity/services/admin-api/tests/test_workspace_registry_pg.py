"""The workspace registry (MIGRATION-0009, PRD 0.13.4 D5) on a real Postgres (docker).

Membership, visibility, connections and invites are rows in identity's database; admin-api is their
one writer and agent-api reaches them through `/internal/registry/*` with the internal secret."""
import pytest

from conftest import requires_docker
from test_stack_admin_api import INTERNAL_SECRET, client  # noqa: F401  (fixture)

pytestmark = requires_docker
H = {"X-Internal-Secret": INTERNAL_SECRET}
ROSTER = [{"subject": "alice", "role": "owner", "added_by": "alice", "added_at": "t0", "email": "a@x.test"},
          {"subject": "bob", "role": "contributor", "added_by": "alice", "added_at": "t1"}]


def test_every_registry_door_needs_the_internal_secret(client):  # noqa: F811
    for method, path in (("GET", "/internal/registry/workspaces/team"),
                         ("PUT", "/internal/registry/workspaces/team"),
                         ("DELETE", "/internal/registry/workspaces/team"),
                         ("GET", "/internal/registry/principals/alice/memberships"),
                         ("GET", "/internal/registry/workspaces/team/invites"),
                         ("PUT", "/internal/registry/workspaces/team/invites"),
                         ("GET", "/internal/registry/invites/abc")):
        r = client.request(method, path, json={} if method == "PUT" else None,
                           headers={"X-Internal-Secret": "wrong"})
        assert r.status_code == 403, (method, path, r.status_code)


def test_a_roster_round_trips_with_its_extras_and_order(client):  # noqa: F811
    assert client.get("/internal/registry/workspaces/team", headers=H).status_code == 404
    r = client.put("/internal/registry/workspaces/team", headers=H,
                   json={"members": ROSTER, "visibility": "open", "connections": [{"workspace": "hb"}]})
    assert r.status_code == 200, r.text
    got = client.get("/internal/registry/workspaces/team", headers=H).json()
    assert got["members"] == ROSTER
    assert got["workspace"]["owner"] == "alice" and got["workspace"]["visibility"] == "open"
    assert got["workspace"]["connections"] == [{"workspace": "hb"}]
    assert client.get("/internal/registry/principals/bob/memberships", headers=H).json() == {
        "memberships": [{"workspace_id": "team", "role": "contributor", "added_at": "t1"}]}


def test_a_roster_is_replaced_whole(client):  # noqa: F811
    client.put("/internal/registry/workspaces/team", headers=H, json={"members": ROSTER})
    client.put("/internal/registry/workspaces/team", headers=H, json={"members": ROSTER[:1]})
    assert [m["subject"] for m in client.get("/internal/registry/workspaces/team", headers=H).json()["members"]] == ["alice"]
    assert client.get("/internal/registry/principals/bob/memberships", headers=H).json() == {"memberships": []}


def test_bad_rows_are_refused(client):  # noqa: F811
    for body in ({"members": [{"subject": "x", "role": "god"}]}, {"visibility": "public"},
                 {"kind": "weird"}, {"members": "nope"}):
        assert client.put("/internal/registry/workspaces/team", headers=H, json=body).status_code == 400, body


def test_invites_by_hash_and_drop(client):  # noqa: F811
    client.put("/internal/registry/workspaces/team", headers=H, json={"members": ROSTER})
    inv = {"id": "i1", "hash": "h" * 64, "role": "viewer", "mode": "restricted",
           "allowed_emails": ["c@x.test"], "expires_at": 4102444800, "max_uses": 1, "uses": 0,
           "created_by": "alice", "created_at": "t", "revoked": False, "owner_act": True}
    assert client.put("/internal/registry/workspaces/team/invites", headers=H,
                      json={"invites": [inv]}).status_code == 200
    assert client.get("/internal/registry/workspaces/team/invites", headers=H).json()["invites"] == [inv]
    assert client.get(f"/internal/registry/invites/{'h' * 64}", headers=H).json() == {
        "workspace_id": "team", "invite": inv}
    assert client.delete("/internal/registry/workspaces/team", headers=H).json() == {"dropped": True}
    assert client.get(f"/internal/registry/invites/{'h' * 64}", headers=H).status_code == 404
    assert client.get("/internal/registry/workspaces/team", headers=H).status_code == 404
    assert client.get("/internal/registry/principals/alice/memberships", headers=H).json() == {"memberships": []}


def test_closed_room_rows_round_trip_and_never_mix_with_workspaces(client):  # noqa: F811
    """0.13.4 S4 (D36): a closed room's members are `room:<id>` rows; an open room has none here."""
    rid = "s-" + "a" * 32
    assert client.get(f"/internal/registry/rooms/{rid}/members", headers=H).status_code == 404
    rows = [{"subject": "alice", "role": "owner", "added_by": "alice", "added_at": "t0"},
            {"subject": "bob", "role": "member", "added_by": "alice", "added_at": "t1"}]
    assert client.put(f"/internal/registry/rooms/{rid}/members", headers=H, json={"members": rows}).status_code == 200
    assert client.get(f"/internal/registry/rooms/{rid}/members", headers=H).json()["members"] == rows
    assert client.get("/internal/registry/principals/bob/rooms", headers=H).json() == {
        "rooms": [{"room_id": rid, "role": "member", "added_at": "t1"}]}
    # a room row is never a workspace membership
    assert client.get("/internal/registry/principals/bob/memberships", headers=H).json() == {"memberships": []}
    assert client.put(f"/internal/registry/rooms/{rid}/members", headers=H,
                      json={"members": [{"subject": "x", "role": "contributor"}]}).status_code == 400
    assert client.delete(f"/internal/registry/rooms/{rid}/members", headers=H).json() == {"dropped": True}
    assert client.get(f"/internal/registry/rooms/{rid}/members", headers=H).status_code == 404
    for method in ("GET", "PUT", "DELETE"):
        r = client.request(method, f"/internal/registry/rooms/{rid}/members", json={} if method == "PUT" else None,
                           headers={"X-Internal-Secret": "wrong"})
        assert r.status_code == 403
