"""SHOW A DOCUMENT TO THE ROOM — no in-app call needed (JTBD-1, a room whose call is on Meet).

A member shows a page; every member's panel opens it (the room's `messages` read carries what is
shown); the presenter keeps control (only they replace or stop it); others close their own panel.
Only a page every member can already read is shown."""
from __future__ import annotations

from tests.test_session_calls import H, _members, env  # noqa: F401 — the shared fixture


def test_a_member_shows_a_page_and_every_member_reads_what_is_shown(env):  # noqa: F811
    client, _store, sid, _r, root = env
    _members(root, "ait-shared", "alice", "bob", "vic")
    (root / "ait-shared" / "plan.md").write_text("# Sponsor plan\n\nText.\n")
    resp = client.post(f"/api/sessions/{sid}/call/show", headers=H("bob"),
                       json={"workspace": "ait-shared", "path": "plan.md"})
    assert resp.status_code == 200, resp.text
    for who in ("alice", "vic", "bob"):
        got = client.get(f"/api/sessions/{sid}/messages", headers=H(who)).json()
        s = got["showing"]
        assert s["by"] == "bob" and s["workspace"] == "ait-shared" and s["path"] == "plan.md"
        assert s["title"] == "Sponsor plan" and isinstance(s["seq"], int)
    msgs = client.get(f"/api/sessions/{sid}/messages", headers=H("alice")).json()["messages"]
    assert any("is showing “Sponsor plan”" in m["text"] for m in msgs)


def test_the_presenter_keeps_control(env):  # noqa: F811
    client, _store, sid, _r, root = env
    _members(root, "ait-shared", "alice", "bob", "vic")
    (root / "ait-shared" / "plan.md").write_text("# Plan\n")
    (root / "ait-shared" / "notes.md").write_text("# Notes\n")
    assert client.post(f"/api/sessions/{sid}/call/show", headers=H("bob"),
                       json={"workspace": "ait-shared", "path": "plan.md"}).status_code == 200
    took = client.post(f"/api/sessions/{sid}/call/show", headers=H("alice"),
                       json={"workspace": "ait-shared", "path": "notes.md"})
    assert took.status_code == 409                                  # bob is presenting
    assert client.post(f"/api/sessions/{sid}/call/show", headers=H("alice"), json={"workspace": "ait-shared", "path": "notes.md", "stop": True}).status_code == 403
    assert client.post(f"/api/sessions/{sid}/call/show", headers=H("bob"),
                       json={"workspace": "ait-shared", "path": "notes.md"}).status_code == 200
    assert client.post(f"/api/sessions/{sid}/call/show", headers=H("bob"), json={"workspace": "ait-shared", "path": "notes.md", "stop": True}).status_code == 200
    assert client.get(f"/api/sessions/{sid}/messages", headers=H("alice")).json()["showing"] is None
    assert client.post(f"/api/sessions/{sid}/call/show", headers=H("alice"),
                       json={"workspace": "ait-shared", "path": "notes.md"}).status_code == 200


def test_only_a_page_every_member_can_read_and_never_by_a_viewer(env):  # noqa: F811
    client, _store, sid, _r, root = env
    _members(root, "bobs-own", "bob", "alice")                      # vic is not in it
    (root / "bobs-own" / "plan.md").write_text("# Plan\n")
    refused = client.post(f"/api/sessions/{sid}/call/show", headers=H("bob"), json={"workspace": "bobs-own", "path": "plan.md"})
    assert refused.status_code == 409 and refused.json()["detail"]["error"] == "not_shared"
    assert client.post(f"/api/sessions/{sid}/call/show", headers=H("bob"), json={"workspace": "personal", "path": "x.md"}).status_code == 409
    _members(root, "all-in", "bob", "alice", "vic")
    (root / "all-in" / "p.md").write_text("# P\n")
    assert client.post(f"/api/sessions/{sid}/call/show", headers=H("vic"), json={"workspace": "all-in", "path": "p.md"}).status_code == 403
    assert client.post(f"/api/sessions/{sid}/call/show", headers=H("eve"), json={"workspace": "all-in", "path": "p.md"}).status_code == 404
    assert client.post(f"/api/sessions/{sid}/call/show", headers=H("bob"), json={"workspace": "all-in", "path": "../x.md"}).status_code in (400, 404, 422)
    assert client.get(f"/api/sessions/{sid}/messages", headers=H("alice")).json()["showing"] is None
