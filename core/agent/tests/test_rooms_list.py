"""`GET /api/rooms/list` (tool `rooms_list`) — a workspace's rooms, described for its reader.

The agent wrote *"I couldn't list rooms directly"* while putting a workspace's rooms on its page
(app.dev 2026-10-18). These pin what it may now read and, mostly, what it may not:

* a person sees the workspace's open rooms (as a member) and the closed ones they are in, with the
  people by name, whether a call is live, and the recurring meetings bound to them;
* a closed room they are not in, and every room of a workspace they are not a member of, is absent;
* a turn other people read (a room's workspace principal, a shared chat) sees only the OPEN rooms of
  the workspace it was dispatched into, and no meetings; a workspace principal names no other
  workspace; a delegated worker is held to its ceiling.

The world is `tests/test_r1_rooms.py`'s: workspace `team` owned by `alice`, `bob` a contributor;
`handbook` and `secret` are carol's.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from control_plane import rooms_overview
from control_plane import workspace_membership as m
from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.session_store import SessionStore
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

from tests._session_fakes import MemBlobs
from tests.test_r1_rooms import DLG, SECRET, _git, _h
from tests.test_session_routes import SUBJECTS, _FakeIdentity, _FakeReader, _FakeRuntime

SERIES_EVENT = {"series_master": {"name": "VEVENT", "properties": {"RRULE": [{"value": "FREQ=WEEKLY"}]}}}


def _meeting(mid, *, workspace="", room="", url="", recurring=True, when="2099-01-05T09:00:00+00:00",
             uid="", title="Weekly sync"):
    data = {"title": title, "scheduled_at": when, "meeting_url": url, "calendar_uid": uid or f"uid-{mid}"}
    if workspace:
        data["workspace_id"] = workspace
    if room:
        data["room"] = room
    if recurring:
        data["calendar_sources"] = [{"id": "cal", "uid": data["calendar_uid"], "event": SERIES_EVENT}]
    return {"id": mid, "platform": "google_meet", "status": "scheduled", "data": data}


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    monkeypatch.setenv("VEXA_FEATURES", "all,rooms,transcript_signals")
    root = tmp_path / "ws"
    (root / "_global").mkdir(parents=True)
    idx = m.InMemoryMembershipIndex()
    for ws, owner in (("team", "alice"), ("handbook", "carol"), ("secret", "carol")):
        (root / ws).mkdir()
        _git(root / ws)
        m.ensure_owner(root, ws, owner, index=idx, email=f"{owner}@acme.test")
    m.grant_membership(root, "team", "bob", "contributor", added_by="alice", index=idx,
                       email="bob.builder@acme.test")
    m.grant_membership(root, "team", "dan", "contributor", added_by="alice", index=idx,
                       email="dan@acme.test")
    for desk in ("alice", "bob", "dan"):
        (root / desk).mkdir()
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    settings = load_settings(workspaces_dir=str(root), redis_url="", internal_api_secret=SECRET,
                             global_system_workspace_path=str(root / "_global"),
                             mcp_url="http://mcp.test", mcp_delegation_secret=DLG)
    rt = _FakeRuntime()
    rt.spawn = lambda uid, profile, env: uid
    meetings: dict = {}
    asked: list = []

    def schedule_source(subject):
        asked.append(subject)
        return list(meetings.get(subject, []))

    app = create_app(Dispatcher(settings, rt, _FakeIdentity()), stream_reader=_FakeReader(),
                     reader=WorkspaceReader(str(root)), sessions=_Sessions(), session_store=store,
                     membership_index=idx, email_subject_lookup=lambda a: SUBJECTS.get(str(a).lower()),
                     schedule_source=schedule_source)

    class W:
        pass
    x = W()
    x.c, x.store, x.root, x.meetings, x.asked = TestClient(app, raise_server_exceptions=False), store, root, meetings, asked
    yield x
    store.close()


def _make(w, name, *, owner="alice", members=(), visibility="open"):
    r = w.c.post("/api/rooms", json={"workspace": "team", "name": name, "visibility": visibility,
                                     "members": list(members)}, headers=_h(owner))
    assert r.status_code == 201, r.text
    return r.json()["session_id"]


def _list(w, headers, workspace="team"):
    return w.c.get("/api/rooms/list", params={"workspace": workspace}, headers=headers)


def _principal(ws="team", ceiling="team"):
    """What a room turn running as workspace `ws`'s own principal presents (D2)."""
    return {"X-User-Id": "1042", "x-user-regime": "human", "x-user-delegation-shared": "1",
            "x-user-delegation-workspaces": ceiling, "x-user-principal-kind": "workspace",
            "x-user-principal-workspace": ws, "x-user-delegation-unit": "agent-ws-team-room-x"}


# ── what a person sees ─────────────────────────────────────────────────────────────────────────

def test_a_member_lists_open_rooms_and_their_own_closed_ones_with_people_by_name(w):
    open_sid = _make(w, "Exchange integration")
    closed_sid = _make(w, "Board prep", members=("bob",), visibility="closed")
    r = _list(w, _h("bob"))
    assert r.status_code == 200, r.text
    rooms = {x["name"]: x for x in r.json()["rooms"]}
    assert set(rooms) == {"Exchange integration", "Board prep"}
    ex = rooms["Exchange integration"]
    assert ex["id"] == open_sid and ex["ref"] == f"[[room:{open_sid}]]" and ex["visibility"] == "open"
    # an open room's people are the workspace's members, named from its roster — never an address
    assert sorted(ex["people"]) == ["alice", "bob.builder", "dan"]
    assert not any("@" in p for p in ex["people"])
    assert ex["call_live"] is False
    assert sorted(rooms["Board prep"]["people"]) == ["alice", "bob.builder"]
    assert rooms["Board prep"]["id"] == closed_sid


def test_a_live_call_is_reported(w):
    sid = _make(w, "Exchange integration")
    w.store.bind_call(sid, room="lk-1", meeting_id=77, owner="alice", share_token="tok-secret")
    got = _list(w, _h("dan")).json()
    row, = got["rooms"]
    assert row["call_live"] is True and row["call_started"]
    assert "tok-secret" not in str(got) and "lk-1" not in str(got)


def test_recurring_meetings_are_attached_to_their_room_and_to_the_workspace(w):
    sid = _make(w, "Exchange integration")
    w.meetings["bob"] = [
        _meeting(1, room=sid, url="https://meet.google.com/abc-defg-hij", uid="weekly"),
        _meeting(2, room=sid, url="https://meet.google.com/abc-defg-hij", uid="weekly",
                 when="2099-01-12T09:00:00+00:00"),
        _meeting(3, workspace="team", url="https://meet.google.com/xyz-xyzx-xyz", title="Pilot steering"),
        _meeting(4, workspace="team", url="https://meet.google.com/one-offx-one", recurring=False),
        _meeting(5, workspace="secret", url="https://meet.google.com/not-mine-xyz"),
    ]
    got = _list(w, _h("bob")).json()
    row, = got["rooms"]
    assert row["recurring_meetings"] == [{"title": "Weekly sync", "link": "https://meet.google.com/abc-defg-hij",
                                          "platform": "google_meet", "next": "2099-01-05T09:00:00+00:00"}]
    assert [x["link"] for x in got["recurring_meetings"]] == ["https://meet.google.com/xyz-xyzx-xyz"]
    assert w.asked == ["bob"]          # the reader's OWN meetings, nobody else's


# ── what a person does not see (deny) ──────────────────────────────────────────────────────────

def test_deny_a_closed_room_is_absent_for_a_workspace_member_not_in_it(w):
    _make(w, "Exchange integration")
    _make(w, "Board prep", members=("bob",), visibility="closed")
    names = [x["name"] for x in _list(w, _h("dan")).json()["rooms"]]
    assert names == ["Exchange integration"]


def test_deny_a_non_member_sees_no_room_of_the_workspace(w):
    _make(w, "Exchange integration")
    _make(w, "Board prep", members=("bob",), visibility="closed")
    r = _list(w, _h("carol"))
    assert r.status_code == 200 and r.json()["rooms"] == []


def test_deny_a_delegated_worker_cannot_list_a_workspace_outside_its_ceiling(w):
    _make(w, "Exchange integration")
    hdr = {**_h("alice"), "x-user-regime": "human", "x-user-delegation-workspaces": "handbook"}
    r = _list(w, hdr)
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "out_of_scope"


# ── a turn other people read ───────────────────────────────────────────────────────────────────

def test_the_workspace_principal_sees_its_open_rooms_only_and_no_meetings(w):
    _make(w, "Exchange integration")
    _make(w, "Board prep", owner="alice", members=("bob",), visibility="closed")
    w.meetings["1042"] = [_meeting(1, workspace="team", url="https://meet.google.com/abc-defg-hij")]
    r = _list(w, _principal())
    assert r.status_code == 200, r.text
    got = r.json()
    assert [x["name"] for x in got["rooms"]] == ["Exchange integration"]
    assert "recurring_meetings" not in got and all("recurring_meetings" not in x for x in got["rooms"])
    assert w.asked == []               # no person-wide meetings read for a shared turn


def test_deny_the_workspace_principal_names_no_other_workspace(w):
    # even with the other workspace inside its ceiling, a workspace's agent lists only its own
    r = _list(w, _principal(ceiling="team,handbook"), workspace="handbook")
    assert r.status_code == 403
    assert r.json()["detail"]["refused"] == "out_of_scope"


def test_deny_the_workspace_principal_outside_its_ceiling(w):
    r = _list(w, _principal(), workspace="secret")
    assert r.status_code == 403


def test_deny_a_shared_chat_turn_sees_no_closed_room_of_its_owner(w):
    """VEXA_ROOM_WS_PRINCIPAL off: a shared turn's token subject is the owner (alice), who IS in the
    closed room — and the turn still lists only the open ones."""
    _make(w, "Exchange integration")
    _make(w, "Board prep", members=("bob",), visibility="closed")
    hdr = {"X-User-Id": "alice", "x-user-regime": "human", "x-user-delegation-shared": "1",
           "x-user-delegation-workspaces": "team"}
    r = _list(w, hdr)
    assert r.status_code == 200, r.text
    assert [x["name"] for x in r.json()["rooms"]] == ["Exchange integration"]
    r = _list(w, hdr, workspace="handbook")
    assert r.status_code == 403


def test_the_route_is_the_one_shared_tokens_may_reach_and_the_app_list_is_unchanged(w):
    _make(w, "Exchange integration")
    # the app's own list keeps its shape
    got = w.c.get("/api/rooms", params={"workspace": "team"}, headers=_h("bob")).json()
    assert set(got["rooms"][0]) >= {"id", "title", "owner", "last_active", "visibility", "member"}
    # and the old list is still refused to a shared token (only the described one is declared)
    r = w.c.get("/api/rooms", params={"workspace": "team"}, headers=_principal())
    assert r.status_code == 403


# ── the pure parts ─────────────────────────────────────────────────────────────────────────────

def test_recurring_is_read_off_the_calendar_series_marks():
    assert rooms_overview.is_recurring(_meeting(1, workspace="team"))
    assert not rooms_overview.is_recurring(_meeting(1, workspace="team", recurring=False))
    inherited = _meeting(1, workspace="team", recurring=False)
    inherited["data"]["workspace_source"] = "series"
    assert rooms_overview.is_recurring(inherited)
    rrule = _meeting(1, workspace="team", recurring=False)
    rrule["data"]["calendar_sources"] = [{"event": {"component": {"properties": {"RRULE": []}}}}]
    assert rooms_overview.is_recurring(rrule)


def test_the_next_occurrence_is_the_soonest_ahead_else_the_latest():
    rows = [_meeting(1, workspace="team", uid="s", when="2000-01-01T09:00:00+00:00"),
            _meeting(2, workspace="team", uid="s", when="2099-02-01T09:00:00+00:00"),
            _meeting(3, workspace="team", uid="s", when="2099-01-01T09:00:00+00:00"),
            _meeting(4, workspace="team", uid="p", when="2000-01-01T09:00:00+00:00"),
            _meeting(5, workspace="team", uid="p", when="2001-01-01T09:00:00+00:00")]
    out = rooms_overview.recurring_meetings(rows, "team", [])[""]
    assert [x["next"] for x in out] == ["2099-01-01T09:00:00+00:00", "2001-01-01T09:00:00+00:00"]
