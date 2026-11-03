"""Deny tests: a closed room's membership never outlives its workspace's (N4, review of the int-next
port, 2026-10-22; the closed-room branch of ``room_membership.resolve_role``).

A person removed from a workspace, or one who leaves it, is out of every room in it, the closed ones
included: no reading, no posting, no room turn run as the workspace's agent, no place on the members
panel or the ring list. A demotion to reader keeps them in the room (a reader takes part, as in an
open room) but their turns run on the room's read-only sibling.

The world is ``tests/test_s4_rooms.py``'s ``w``: workspace ``team``, alice owner, bob and dan
contributors, vic a reader.
"""
from __future__ import annotations

from control_plane import join_links, room_membership as rm, room_principal as rp
from control_plane import workspace_membership as m

from tests.test_s4_rooms import H, _room, w  # noqa: F401  (fixture)


def _closed(w):
    return _room(w, members=("bob", "dan"), visibility="closed", name="deal")


def _reads(w, sid, who):
    return w.c.get(f"/api/sessions/{sid}/messages", headers=H(who)).status_code


def _posts(w, sid, who, text="hello"):
    return w.c.post(f"/api/sessions/{sid}/messages", json={"text": text}, headers=H(who)).status_code


def _turn(w, sid, who):
    """A room turn: the status, and whether anything was dispatched for it."""
    before = len(w.turns)
    r = w.c.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa what changed?"}, headers=H(who))
    return r.status_code, len(w.turns) > before


def _remove(w, who):
    r = w.c.delete(f"/api/workspace/members/{who}", params={"workspace_id": "team"}, headers=H("alice"))
    assert r.status_code == 200, r.text
    assert m.is_member(w.root, "team", who) is None


def _panel(w, sid, who="alice"):
    r = w.c.get(f"/api/rooms/{sid}/members", headers=H(who))
    assert r.status_code == 200, r.text
    return {x["principal"]: x["role"] for x in r.json()["members"]}


def _rows(w, sid):
    return {str(r["subject"]): r["role"] for r in m.room_store_get(w.root, sid) or []}


# ── removed from the workspace: out of its closed rooms ─────────────────────────────────────────

def test_removed_member_can_no_longer_read_post_or_run_a_room_turn(w):
    sid = _closed(w)
    assert _reads(w, sid, "bob") == 200 and _posts(w, sid, "bob") < 300
    _remove(w, "bob")
    status, dispatched = _turn(w, sid, "bob")
    seen = {"read": _reads(w, sid, "bob"), "post": _posts(w, sid, "bob"), "turn": status,
            "dispatched": dispatched, "role": w.store.role_of(sid, "bob")}
    assert seen["read"] >= 400 and seen["post"] >= 400 and seen["turn"] >= 400, seen
    assert not seen["dispatched"] and seen["role"] is None, seen


def test_a_member_who_leaves_the_workspace_leaves_its_closed_rooms(w):
    sid = _closed(w)
    r = w.c.post("/api/workspace/team/leave", headers=H("dan"))
    assert r.status_code == 200, r.text
    assert w.store.role_of(sid, "dan") is None and _reads(w, sid, "dan") >= 400


def test_the_rows_go_and_the_panel_and_ring_list_drop_them(w):
    sid = _closed(w)
    _remove(w, "bob")
    assert "bob" not in _rows(w, sid)
    assert "bob" not in _panel(w, sid)
    assert "bob" not in {p["principal"] for p in w.store.participants(sid)}
    assert "bob" not in rm.ringable(w.store, w.root, sid, exclude="alice")


def test_the_authority_holds_before_any_tidy_up(w):
    """With the roster listener off (a failed or not-yet-run tidy-up), the registry row is still there
    and the role check alone refuses: ``resolve_role`` is the authority, the row drop is hygiene."""
    sid = _closed(w)
    m.set_roster_listener(None)
    _remove(w, "bob")
    assert "bob" in _rows(w, sid)
    assert w.store.role_of(sid, "bob") is None
    assert _reads(w, sid, "bob") >= 400 and _posts(w, sid, "bob") >= 400
    status, dispatched = _turn(w, sid, "bob")
    assert status >= 400 and not dispatched
    assert "bob" not in _panel(w, sid)
    assert "bob" not in rm.ringable(w.store, w.root, sid, exclude="alice")


def test_an_unreadable_roster_answers_no_membership(w, monkeypatch):
    sid = _closed(w)

    def _boom(*_a, **_k):
        raise m.MembershipError("policy store down", status=503)

    monkeypatch.setattr(m, "is_member", _boom)
    assert w.store.role_of(sid, "bob") is None and w.store.role_of(sid, "alice") is None


def test_the_owner_removed_hands_the_room_on(w):
    sid = _room(w, owner="bob", members=("dan", "alice"), visibility="closed", name="bobs")
    _remove(w, "bob")
    assert w.store.role_of(sid, "bob") is None
    assert w.store.role_of(sid, "dan") == "owner" and _rows(w, sid)["dan"] == "owner"
    assert w.store.get(sid)["owner"] == "dan"
    assert _reads(w, sid, "dan") == 200


def test_no_room_link_door_on_this_line(w):
    """The finding's link half (mint 201, old link admits 200) needs a registered chat target; this
    line registers none, so a room link is never minted. If one is wired, it must ask ``role_of``."""
    sid = _closed(w)
    assert join_links.chat_target() is None
    _remove(w, "bob")
    r = w.c.post("/api/join-links", json={"target_kind": "chat", "target_id": sid}, headers=H("bob"))
    assert r.status_code >= 400


# ── positive controls ───────────────────────────────────────────────────────────────────────────

def test_current_members_are_unaffected(w):
    sid = _closed(w)
    _remove(w, "bob")
    for who in ("alice", "dan"):
        assert _reads(w, sid, who) == 200 and _posts(w, sid, who) < 300
    status, dispatched = _turn(w, sid, "dan")
    assert status < 300 and dispatched
    assert {"alice", "dan"} <= set(_panel(w, sid))


def test_vic_never_in_the_room_stays_out(w):
    sid = _closed(w)
    assert w.store.role_of(sid, "vic") is None and _reads(w, sid, "vic") >= 400


# ── coming back ─────────────────────────────────────────────────────────────────────────────────

def test_readded_to_the_workspace_gets_open_rooms_back_and_a_closed_room_by_being_added(w):
    closed = _closed(w)
    opened = _room(w, visibility="open", name="lobby")
    _remove(w, "bob")
    assert w.store.role_of(opened, "bob") is None
    m.grant_membership(w.root, "team", "bob", "contributor", added_by="alice", index=w.idx,
                       email="bob@acme.test")
    assert w.store.role_of(opened, "bob") == "member"           # open: derived from the workspace
    assert w.store.role_of(closed, "bob") is None               # closed: its own rows decide
    r = w.c.post(f"/api/rooms/{closed}/members", json={"subject": "bob"}, headers=H("alice"))
    assert r.status_code < 300, r.text
    assert w.store.role_of(closed, "bob") == "member" and _reads(w, closed, "bob") == 200


# ── demotion: a reader takes part, but never runs the workspace's agent as a writer ──────────────

def test_demoted_to_reader_turns_run_read_only(w):
    sid = _closed(w)
    r = w.c.post("/api/workspace/members/bob/role", params={"workspace_id": "team"},
                 json={"role": "viewer"}, headers=H("alice"))
    assert r.status_code == 200, r.text
    assert w.store.role_of(sid, "bob") == "member"              # as in an open room: a reader takes part
    p = rp.resolve(w.store, w.root, sid, speaker="bob")
    assert p is not None and p.writable is False
    assert p.unit.endswith(rp.READ_UNIT_SUFFIX)
    assert "ro:team" in p.ceiling() and "team" not in p.ceiling()
    w_ = rp.resolve(w.store, w.root, sid, speaker="dan")
    assert w_.writable is True and not w_.unit.endswith(rp.READ_UNIT_SUFFIX)
