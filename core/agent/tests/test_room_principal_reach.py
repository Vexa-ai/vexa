"""A ROOM'S TURN RUNS AS ITS WORKSPACE'S OWN PRINCIPAL (D2, C1) — what that principal can and cannot do.

The token's subject is the workspace's numeric principal (identity's ``principals``; the conftest's
fake identity answers it here), an account in no session, on no desk, with no company book. Every
route a room's agent is meant to use must answer that account for its own room and workspace, and
nothing a person owns may be written under its id.

The room fixture is `tests/test_r1_rooms.py`'s `world`: workspace `team` owned by `alice`, `bob` a
contributor, a room made in `team`.
"""
from __future__ import annotations

from control_plane import workspace_store_service as ws_store

from tests.test_r1_rooms import _h, _room, world  # noqa: F401  (fixture)
from tests.test_sec_deny_rooms import _room_token_headers


def test_a_room_turn_shows_a_page_on_its_own_room(world):
    client, store, calls, spawned, root, _d = world
    (root / "team" / "plan.md").write_text("# Integration plan\n\nText.\n")
    sid, _env, claims, hdr = _room_token_headers(client, root, spawned)
    r = client.post(f"/api/sessions/{sid}/call/show", headers=hdr,
                    json={"workspace": "team", "path": "plan.md"})
    assert r.status_code == 200, r.text
    assert r.json()["shown"]["by"] == claims["sub"]
    showing = client.get(f"/api/sessions/{sid}/messages", headers=_h("bob")).json()["showing"]
    assert showing["path"] == "plan.md" and showing["title"] == "Integration plan"
    lines = [m["text"] for m in client.get(f"/api/sessions/{sid}/messages", headers=_h("alice")).json()["messages"]]
    assert any(t.startswith("Vexa is showing “Integration plan”") for t in lines), lines


def test_deny_a_room_turn_with_no_named_ceiling_shows_nothing(world):
    """A workspace principal is in no roster: its reach is the workspace its dispatch names, so a
    token with no ceiling, or with `*`, shows nothing."""
    client, store, calls, spawned, root, _d = world
    (root / "team" / "plan.md").write_text("# Plan\n")
    sid, _env, _claims, hdr = _room_token_headers(client, root, spawned)
    for ceiling in (None, "*"):
        h = {k: v for k, v in hdr.items() if k != "x-user-delegation-workspaces"}
        if ceiling is not None:
            h["x-user-delegation-workspaces"] = ceiling
        r = client.post(f"/api/sessions/{sid}/call/show", headers=h,
                        json={"workspace": "team", "path": "plan.md"})
        assert r.status_code == 403, (ceiling, r.status_code, r.text)
    assert client.get(f"/api/sessions/{sid}/messages", headers=_h("bob")).json()["showing"] is None


def test_deny_a_room_turn_shows_nothing_in_another_room(world):
    client, store, calls, spawned, root, _d = world
    (root / "team" / "plan.md").write_text("# Plan\n")
    other = _room(client)
    _sid, _env, _claims, hdr = _room_token_headers(client, root, spawned)
    for verb, body in (("show", {"workspace": "team", "path": "plan.md"}), ("highlight", {"anchor": "Plan"})):
        r = client.post(f"/api/sessions/{other}/call/{verb}", headers=hdr, json=body)
        assert r.status_code == 404, (verb, r.status_code, r.text)
    assert client.get(f"/api/sessions/{other}/messages", headers=_h("bob")).json()["showing"] is None


def test_a_room_turn_proposes_to_its_payer_and_makes_no_desk(world):
    """D27: a room's turn may propose; the proposal waits in the book of whoever answers for the room
    (its payer, signed by identity as `x-user-bills-to`) — never on a desk named after the principal."""
    client, store, calls, spawned, root, _d = world
    _sid, _env, claims, hdr = _room_token_headers(client, root, spawned)
    r = client.post("/api/claims", headers={**hdr, "x-user-bills-to": "alice"},
                    json={"claims": ["They run Exchange on-prem"]})
    assert r.status_code == 200, r.text
    assert not (root / claims["sub"]).exists(), "a desk was made for the workspace principal"
    assert client.get("/api/claims", headers=_h("alice")).json()["still_proposed"] == 1
    assert client.get("/api/claims", headers=_h("bob")).json()["still_proposed"] == 0


def test_deny_a_room_turn_with_no_payer_proposes_nothing(world):
    client, store, calls, spawned, root, _d = world
    _sid, _env, claims, hdr = _room_token_headers(client, root, spawned)
    r = client.post("/api/claims", headers=hdr, json={"claims": ["They run Exchange on-prem"]})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "shared_conversation"
    assert not (root / claims["sub"]).exists()


def test_the_s3_store_grant_names_the_principal_the_worker_presents(world, monkeypatch):
    """On the s3 backend the store routes compare a unit's recorded subject with ``x-user-id`` —
    the token's ``sub``, which for a room's turn is the workspace's principal, not the owner."""
    client, store, calls, spawned, root, _d = world
    monkeypatch.setattr(ws_store, "_running", object())
    monkeypatch.setattr(ws_store, "_local_mounts", {})
    _sid, _env, claims, _hdr = _room_token_headers(client, root, spawned)
    uid = spawned[-1][0]
    record = ws_store.unit_record(None, uid)
    assert record is not None and record["subject"] == claims["sub"] != "alice"
    assert any(m["path"].endswith("/team") and m["write"] for m in record["mounts"]), record


def test_a_persons_turn_keeps_its_own_subject_on_the_store_grant(world, monkeypatch):
    client, store, calls, spawned, root, _d = world
    monkeypatch.setattr(ws_store, "_running", object())
    monkeypatch.setattr(ws_store, "_local_mounts", {})
    client.post("/api/chat/submit", json={"prompt": "hello"}, headers=_h("bob"))
    uid = spawned[-1][0]
    assert ws_store.unit_record(None, uid)["subject"] == "bob"
