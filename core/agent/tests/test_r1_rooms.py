"""R1 — ROOMS FOR REAL (approved model, 2026-10-16; architecture decisions B1, C1, J1, K).

* (a) a room's settings are the ROOM's: one level, one write target (its workspace, fixed) — never a
  member's own chat record;
* (b) a room's turns run as its WORKSPACE's agent `ws:<id>`: one worker per room, the workspace
  read-write, its connected OPEN workspaces read-only, nothing of any person's (no desk, no
  `_system`, no person-wide reads: `shared`), paid by the workspace owner, the speaker recorded as
  `on_behalf_of`; a personal chat stays the person's own agent;
* (c) the room APIs: leave, members, a workspace's open state and its connected open workspaces;
* (d) the folders migration: folders that hold work map to their workspace, the rest go to Hidden,
  a dry-run report first, nothing deleted;
* (e) leaks: a room turn never reads outside its grant; a room with an outsider is `cross_org`, so
  crm-api answers it public fields only (`core/crm/tests/test_cross_org_audience.py`).
"""
from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import chat_folders, folder_migration, room_principal as rp, workspace_membership as m
from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.session_store import SessionStore
from control_plane.workspace_reader import WorkspaceReader
from shared import units
from shared.config import load_settings

from tests._session_fakes import MemBlobs
from tests.test_session_routes import SUBJECTS, _FakeIdentity, _FakeReader, _FakeRuntime

SECRET = "s" * 32
DLG = "d" * 32


def _h(who):
    return {"X-User-Id": who}


def _git(ws: Path):
    subprocess.run(["git", "-C", str(ws), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.name", "t"], check=True)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    monkeypatch.setenv("VEXA_FEATURES", "all,rooms,transcript_signals")
    root = tmp_path / "ws"
    (root / "_global").mkdir(parents=True)
    idx = m.InMemoryMembershipIndex()
    for ws, owner in (("team", "alice"), ("handbook", "carol"), ("secret", "carol")):
        (root / ws).mkdir()
        _git(root / ws)
        m.ensure_owner(root, ws, owner, index=idx)
    m.grant_membership(root, "team", "bob", "contributor", added_by="alice", index=idx)
    for desk in ("alice", "bob"):
        (root / desk).mkdir()
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    settings = load_settings(workspaces_dir=str(root), redis_url="", internal_api_secret=SECRET,
                             global_system_workspace_path=str(root / "_global"),
                             mcp_url="http://mcp.test", mcp_delegation_secret=DLG)
    rt = _FakeRuntime()
    spawned = []
    rt.spawn = lambda uid, profile, env: spawned.append((uid, env)) or uid
    dispatcher = Dispatcher(settings, rt, _FakeIdentity())
    calls = []
    real = dispatcher.dispatch

    def _dispatch(inv, **kw):
        calls.append((inv, kw))
        return real(inv, **kw)

    dispatcher.dispatch = _dispatch
    app = create_app(dispatcher, stream_reader=_FakeReader(), reader=WorkspaceReader(str(root)),
                     sessions=_Sessions(), session_store=store, membership_index=idx,
                     email_subject_lookup=lambda a: SUBJECTS.get(str(a).lower()))
    yield TestClient(app, raise_server_exceptions=False), store, calls, spawned, root, dispatcher
    store.close()


def _room(client, owner="alice", members=("bob",), visibility="open"):
    r = client.post("/api/rooms", json={"workspace": "team", "name": "Exchange integration",
                                        "visibility": visibility, "members": list(members)}, headers=_h(owner))
    assert r.status_code == 201, r.text
    return r.json()["session_id"]


def _claims(env: dict) -> dict:
    tok = env["VEXA_MCP_DELEGATION_TOKEN"][len("vxd_"):]
    return json.loads(base64.urlsafe_b64decode(tok.split(".")[1] + "=="))


# ── (a) room-level settings ──────────────────────────────────────────────────────────────────────

def test_a_rooms_level_and_write_target_are_the_rooms_not_the_speakers(world):
    client, store, calls, spawned, root, _d = world
    sid = _room(client)
    r = client.patch(f"/api/sessions/{sid}/room", json={"level": "opus-4"}, headers=_h("bob"))
    assert r.status_code == 200 and r.json()["level"] == "opus-4" and r.json()["write_target"] == "team"
    assert store.room(sid)["created_by"] == "alice"
    assert client.patch(f"/api/sessions/{sid}/room", json={"level": "Not An Id"},
                        headers=_h("bob")).status_code == 422
    # bob's own chat preferences point elsewhere; the room does not care
    client.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa summarise"}, headers=_h("bob"))
    (_inv, kw), = calls
    assert kw["target"] == "team" and kw["model"] == "opus-4" and kw["room_principal"].workspace == "team"


# ── (b) the workspace principal ──────────────────────────────────────────────────────────────────

def test_a_room_turn_runs_as_the_workspace_agent_on_one_worker(world):
    client, store, calls, spawned, root, _d = world
    sid = _room(client)
    client.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa what changed?"}, headers=_h("bob"))
    client.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa and since then?"}, headers=_h("alice"))
    units_ = {uid for uid, _env in spawned}
    assert units_ == {rp.room_unit_id("team", sid)}                 # one worker, whoever speaks
    uid, env = spawned[0]
    assert env["VEXA_PRINCIPAL"] == "ws:team" and env["VEXA_ON_BEHALF_OF"] == "bob"
    assert env["VEXA_OWNER"] == "alice"                               # the workspace owner's pool
    claims = _claims(env)
    assert claims["sub"] == "alice" and claims["unit"] == uid
    assert claims["scope"]["shared"] is True and claims["scope"]["workspaces"] == ["team"]
    assert "audience" not in claims["scope"]


def _reader(root, who="vic"):
    m.grant_membership(root, "team", who, "viewer", added_by="alice", index=m.InMemoryMembershipIndex())
    return who


def test_a_readers_turn_in_a_room_runs_read_only_on_its_own_worker(world):
    """A reader of the workspace speaks in its room: the workspace's agent answers, but with W
    mounted read-only and a read-only ceiling, on a sibling worker of its own - never the warm
    worker a writer's turn spawned with W writable."""
    client, store, calls, spawned, root, _d = world
    vic = _reader(root)
    sid = _room(client, members=("bob", vic))
    client.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa what changed?"}, headers=_h("bob"))
    writer_uid, writer_env = spawned[-1]
    assert {x["slug"]: x["write"] for x in json.loads(writer_env["VEXA_MOUNTS"])}["team"] is True
    store_lease = f"session:{sid}:lease"  # noqa: F841 - the fake runtime holds no lease
    client.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa rewrite the plan"}, headers=_h(vic))
    uid, env = spawned[-1]
    assert uid != writer_uid and uid == rp.room_unit_id("team", sid) + rp.READ_UNIT_SUFFIX
    assert {x["slug"]: x["write"] for x in json.loads(env["VEXA_MOUNTS"])}["team"] is False
    assert _claims(env)["scope"]["workspaces"] == ["ro:team"]
    p = rp.resolve(store, root, sid, speaker=vic)
    assert p.writable is False and p.ceiling() == ["ro:team"]
    assert rp.resolve(store, root, sid, speaker="bob").writable is True


def test_a_reader_cannot_make_move_or_price_a_room(world):
    client, store, calls, spawned, root, _d = world
    vic = _reader(root)
    r = client.post("/api/rooms", json={"workspace": "team", "name": "Mine"}, headers=_h(vic))
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "needs_a_writer"
    sid = _room(client, members=("bob", vic))
    r = client.patch(f"/api/sessions/{sid}/room", json={"level": "opus-4"}, headers=_h(vic))
    assert r.status_code == 403
    assert store.room(sid)["level"] in (None, "")
    # moving a room into a workspace needs a writer there too
    m.grant_membership(root, "handbook", "bob", "viewer", added_by="carol", index=m.InMemoryMembershipIndex())
    client.patch(f"/api/sessions/{sid}/room", json={"visibility": "closed"}, headers=_h("alice"))
    r = client.patch(f"/api/sessions/{sid}/room", headers=_h("bob"),
                     json={"visibility": "open", "workspace": "handbook", "confirm_history": True})
    assert r.status_code == 403 and store.room(sid)["workspace"] == "team"


def test_a_room_runs_as_its_owner_only_when_rooms_is_on_for_the_owner(world, monkeypatch):
    """The per-account switch: a speaker with `rooms` on cannot run turns on the authority and the
    pool of a workspace owner whose account does not have it."""
    client, store, calls, spawned, root, _d = world
    sid = _room(client)
    monkeypatch.setenv("VEXA_FEATURES", "all")
    (root / "_global" / "POLICIES.md").write_text(
        "---\nkind: policies\nfeatures_accounts: {rooms: bob}\n---\n\n# Policies\n")
    client.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa summarise"}, headers=_h("bob"))
    assert calls and calls[-1][1].get("room_principal") is None


def test_a_personal_chat_stays_the_persons_own_agent(world):
    client, store, calls, spawned, root, _d = world
    client.post("/api/chat/submit", json={"session": "pchat-mine", "prompt": "hello"}, headers=_h("bob"))
    (uid, env), = spawned
    assert uid == units.chat_unit_id("bob", "pchat-mine") and "VEXA_PRINCIPAL" not in env
    assert _claims(env)["sub"] == "bob"


# ── (e) leaks ────────────────────────────────────────────────────────────────────────────────────

def test_a_room_turn_mounts_only_its_grant(world):
    client, store, calls, spawned, root, _d = world
    sid = _room(client)
    rp.set_visibility(root, "handbook", "open", commit_fn=m.policy_commit)
    rp.connect(root, "team", "handbook", actor="alice", commit_fn=m.policy_commit)
    client.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa check the handbook"}, headers=_h("bob"))
    _uid, env = spawned[-1]
    mounts = {x["slug"]: x["write"] for x in json.loads(env["VEXA_MOUNTS"])}
    assert mounts == {"_global": False, "team": True, "handbook": False}
    assert not {"alice", "bob", "_system", "secret"} & set(mounts)
    assert _claims(env)["scope"]["workspaces"] == ["ro:handbook", "team"]
    # a connected workspace that closes is no longer read — decided at every dispatch
    rp.set_visibility(root, "handbook", "closed", commit_fn=m.policy_commit)
    client.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa again"}, headers=_h("bob"))
    assert "handbook" not in {x["slug"] for x in json.loads(spawned[-1][1]["VEXA_MOUNTS"])}


def test_only_an_open_workspace_of_the_same_organisation_is_connected(world):
    client, store, calls, spawned, root, _d = world
    rp.set_visibility(root, "handbook", "open", commit_fn=m.policy_commit)
    vexa_dir = root / "handbook" / ".vexa"
    vexa_dir.mkdir(exist_ok=True)
    from workspaces.shared.workspace_id import mint_id
    wid = mint_id()
    (vexa_dir / "workspace.json").write_text(json.dumps({"id": wid, "org": "o-other"}))
    with pytest.raises(PermissionError):
        rp.connect(root, "team", "handbook", actor="alice", commit_fn=m.policy_commit)
    r = client.post("/api/workspace-links/team", json={"workspace": "handbook"}, headers=_h("alice"))
    assert r.status_code == 409
    # a connection made before the workspace moved org is no longer read
    (vexa_dir / "workspace.json").write_text(json.dumps({"id": wid}))
    rp.connect(root, "team", "handbook", actor="alice", commit_fn=m.policy_commit)
    assert rp.connected_open(root, "team") == ["handbook"]
    (vexa_dir / "workspace.json").write_text(json.dumps({"id": wid, "org": "o-other"}))
    assert rp.connected_open(root, "team") == []


def test_a_room_with_an_outsider_is_cross_org(world):
    client, store, calls, spawned, root, _d = world
    sid = _room(client)
    store.join(sid, "dave")                           # in the room, not a member of the workspace
    client.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa who is Globex?"}, headers=_h("bob"))
    assert _claims(spawned[-1][1])["scope"]["audience"] == "cross_org"
    p = rp.resolve(store, root, sid, speaker="bob")
    assert p.audience == "cross_org"


def test_a_room_wake_is_the_workspace_agent_writing_its_own_workspace(world, monkeypatch):
    # FOUNDER RULING 2026-10-16 ("it can't write to its own workspace"): a sensor-woken turn in a room
    # runs with the room agent's scope — W read-write under W's grants — like any room turn.
    client, store, calls, spawned, root, _d = world
    (root / "_global" / "POLICIES.md").write_text("---\ntranscript_signals: on\n---\n")
    sid = _room(client)
    ev = {"name": "room.signal", "subject": "alice", "target": {"chat": sid},
          "plan": {"prompt": "Messages since you last spoke."},
          "data": {"session": sid, "score": 1.0, "reason": "threshold",
                   "buffer": [{"segment_id": f"{sid}:1", "text": "is TLS 1.3 supported?"}]}}
    r = client.post("/events", json=ev, headers={"X-Internal-Secret": SECRET})
    assert r.status_code == 202, r.text
    uid, env = spawned[-1]
    assert uid == rp.room_unit_id("team", sid) + "-signal"
    mounts = json.loads(env["VEXA_MOUNTS"])
    assert [x["slug"] for x in mounts if x["write"]] == ["team"]                # its own workspace only
    assert "secret" not in {x["slug"] for x in mounts}                          # nobody else's
    assert "team" in _claims(env)["scope"]["workspaces"] and env["VEXA_PRINCIPAL"] == "ws:team"
    assert "VEXA_LEAST_PRIVILEGE" not in env


def test_a_room_signal_turn_writes_its_own_workspace_and_is_refused_another(world):
    client, store, calls, spawned, root, _d = world
    sid = _room(client)
    unit = rp.room_unit_id("team", sid) + "-signal"
    hdr = {"X-User-Id": "alice", "x-user-regime": "autonomous", "x-user-delegation-workspaces": "team",
           "x-user-delegation-unit": unit, "x-user-delegation-target": "team"}
    r = client.put("/api/workspace/file", headers=hdr, json={"path": "decisions.md", "content": "- Tuesday\n"})
    assert r.status_code == 200, r.text
    assert (root / "team" / "decisions.md").read_text() == "- Tuesday\n"
    r = client.put("/api/workspace/file", headers=hdr,
                   json={"path": "x.md", "content": "no\n", "slug": "secret"})
    assert r.status_code == 403 and not (root / "secret" / "x.md").exists()


# ── (c) room APIs ────────────────────────────────────────────────────────────────────────────────

def test_members_and_leave(world):
    client, store, calls, spawned, root, _d = world
    sid = _room(client)
    got = client.get(f"/api/rooms/{sid}/members", headers=_h("bob")).json()["members"]
    assert [(x["principal"], x["role"]) for x in got] == [("alice", "owner"), ("bob", "member")]
    assert client.post(f"/api/rooms/{sid}/leave", headers=_h("bob")).json()["left"] is True
    assert client.get(f"/api/rooms/{sid}/members", headers=_h("bob")).status_code == 404


def test_only_an_open_workspace_connects_and_only_its_owner_opens_it(world):
    client, store, calls, spawned, root, _d = world
    r = client.post("/api/workspace-links/team", json={"workspace": "handbook"}, headers=_h("alice"))
    assert r.status_code == 409 and r.json()["detail"]["refused"] == "not_open"
    assert client.put("/api/workspace-links/handbook/visibility", json={"visibility": "open"},
                      headers=_h("alice")).status_code == 403                       # not its owner
    assert client.put("/api/workspace-links/handbook/visibility", json={"visibility": "open"},
                      headers=_h("carol")).json()["visibility"] == "open"
    r = client.post("/api/workspace-links/team", json={"workspace": "handbook"}, headers=_h("bob"))
    assert r.status_code == 200 and [x["workspace"] for x in r.json()["connected"]] == ["handbook"]
    got = client.get("/api/workspace-links/team", headers=_h("bob")).json()
    assert got["connected"][0]["open"] is True
    assert client.get("/api/workspace-links/team", headers=_h("mallory")).status_code == 404
    r = client.post("/api/workspace-links/team/remove", json={"workspace": "handbook"}, headers=_h("bob"))
    assert r.json()["connected"] == []


def test_with_rooms_off_the_r1_routes_are_closed(world, monkeypatch):
    client, store, calls, spawned, root, _d = world
    monkeypatch.setenv("VEXA_FEATURES", "all")
    for method, path in (("POST", "/api/rooms"), ("GET", "/api/workspace-links/team"),
                         ("POST", "/api/rooms/migrate-folders")):
        assert client.request(method, path, headers=_h("alice"), json={}).status_code == 403, path


# ── (d) folders migration ────────────────────────────────────────────────────────────────────────

def test_folders_that_hold_work_map_to_workspaces_and_the_rest_are_hidden(world):
    client, store, calls, spawned, root, _d = world
    work = chat_folders.create(store, "bob", "Exchange")["id"]
    misc = chat_folders.create(store, "bob", "Misc")["id"]
    chat_folders.file_rows(store, "bob", ["c:s-1", "c:s-2"], work)
    chat_folders.file_rows(store, "bob", ["c:s-3", "m:42"], misc)
    chat_folders.file_rows(store, "bob", ["c:s-9"], chat_folders.PINNED)
    targets = {"s-1": "team", "s-2": "team", "s-3": "team"}
    report = folder_migration.plan(store, "bob", target_of=lambda s: targets.get(s, ""))
    by = {f["name"]: f for f in report["folders"]}
    assert by["Exchange"]["action"] == "map" and by["Exchange"]["workspace"] == "team"
    assert by["Misc"]["action"] == "hide" and sorted(report["hidden"]) == ["c:s-3", "m:42"]
    assert chat_folders.listing(store, "bob")["filings"]["c:s-1"] == work      # a dry run moves nothing
    notes = []
    folder_migration.apply(store, "bob", target_of=lambda s: targets.get(s, ""), notify=notes.append)
    filings = chat_folders.listing(store, "bob")["filings"]
    assert filings["c:s-1"] == chat_folders.UNGROUPED and filings["c:s-3"] == chat_folders.HIDDEN
    assert filings["c:s-9"] == chat_folders.PINNED                            # pins stay pinned
    assert {f["name"] for f in chat_folders.listing(store, "bob")["folders"]} >= {"Exchange", "Misc"}
    assert notes and "1 mapped, 1 to Hidden" in notes[0]
    again = folder_migration.apply(store, "bob", target_of=lambda s: targets.get(s, ""))
    assert again["moves"] == {}                                                # idempotent


def test_the_migration_route_reports_by_default_and_applies_on_ask(world):
    client, store, calls, spawned, root, _d = world
    f = chat_folders.create(store, "bob", "Misc")["id"]
    chat_folders.file_rows(store, "bob", ["c:s-5"], f)
    r = client.post("/api/rooms/migrate-folders", headers=_h("bob"))
    assert r.status_code == 200 and r.json()["dry_run"] is True and r.json()["hidden"] == ["c:s-5"]
    assert chat_folders.listing(store, "bob")["filings"]["c:s-5"] == f
    r = client.post("/api/rooms/migrate-folders?dry_run=false", headers=_h("bob"))
    assert r.json()["dry_run"] is False
    assert chat_folders.listing(store, "bob")["filings"]["c:s-5"] == chat_folders.HIDDEN
    rep = client.get("/internal/rooms/folders-report", headers={"X-Internal-Secret": SECRET})
    assert rep.status_code == 200 and any(p["owner"] == "bob" for p in rep.json()["people"])
    assert client.get("/internal/rooms/folders-report", headers=_h("bob")).status_code == 403


# ── 1.2: a call started in a room is the room's ──────────────────────────────────────────────────

class _LiveKit:
    url = "wss://livekit.test"

    def __init__(self, meta):
        self.meta = meta

    def room(self, name):
        return {"name": name, "metadata": self.meta, "num_participants": 1}

    def list_rooms(self, names=None):
        return [self.room(n) for n in (names or [])]


def test_a_call_in_a_room_is_owned_by_it_and_its_recap_lands_in_the_room(world, monkeypatch):
    client, store, calls, spawned, root, dispatcher = world
    sid = _room(client)
    annotated = []
    from control_plane.routers import calls as calls_router  # noqa: F401
    app = client.app
    # the call's room, as meeting-api made it: started by bob, in this session, meeting 77
    from control_plane import api as api_mod  # noqa: F401
    sessions = _Sessions()
    app2 = create_app(dispatcher, stream_reader=_FakeReader(), reader=WorkspaceReader(str(root)),
                      sessions=sessions, session_store=store, membership_index=app.state.__dict__.get("mindex"),
                      livekit=_LiveKit({"owner": "bob", "session_id": sid, "meeting_id": 77}),
                      meeting_annotator=lambda who, mid, meta: annotated.append((who, mid, meta)) or True,
                      meeting_owner_lookup=lambda uid, mid, workspaces=None: {"id": 77, "user_id": uid})
    c2 = TestClient(app2)
    r = c2.post(f"/api/sessions/{sid}/call", json={"room": "call-abcd1234", "meeting_id": 77,
                                                   "share_token": "77.tok-xyz"}, headers=_h("bob"))
    assert r.status_code == 200, r.text
    assert annotated == [("bob", 77, {"room": sid, "workspace": "team"})]
    assert sessions.chat_for("bob", "meet-77") == sid                 # the recap's chat is the room
    # the recap (a flow's scheduled turn on meet-77) runs as the room's agent, on its own sibling
    hdr = {"X-User-Id": "bob", "X-Internal-Secret": SECRET, "x-vexa-flow-regime": "autonomous",
           "x-vexa-flow-name": "post_meeting", "x-vexa-flow-scope": "instance", "x-vexa-flow-step": "agent_run"}
    c2.post("/api/chat/submit", json={"session": "meet-77", "prompt": "Write the meeting's report."},
            headers=hdr)
    uid, env = spawned[-1]
    assert uid == rp.room_unit_id("team", sid) + units.ROUTINE_UNIT_SUFFIX
    assert env["VEXA_PRINCIPAL"] == "ws:team"
    assert {x["slug"] for x in json.loads(env["VEXA_MOUNTS"])} == {"_global", "team"}


def test_a_call_outside_a_room_or_with_rooms_off_is_left_as_it_was(world, monkeypatch):
    client, store, calls, spawned, root, dispatcher = world
    annotated = []
    sessions = _Sessions()
    row = store.create("bob", name="pchat-solo")
    app2 = create_app(dispatcher, stream_reader=_FakeReader(), reader=WorkspaceReader(str(root)),
                      sessions=sessions, session_store=store,
                      livekit=_LiveKit({"owner": "bob", "session_id": row["id"], "meeting_id": 78}),
                      meeting_annotator=lambda who, mid, meta: annotated.append(meta) or True)
    c2 = TestClient(app2, raise_server_exceptions=False)
    r = c2.post(f"/api/sessions/{row['id']}/call", json={"room": "call-efgh5678", "meeting_id": 78,
                                                        "share_token": "78.tok-abcdef"}, headers=_h("bob"))
    assert r.status_code == 200, r.text
    assert annotated == [] and sessions.chat_for("bob", "meet-78") == "meet-78"


# ── 1.3: the room's own words reach its sensor, as context ───────────────────────────────────────

def test_what_the_room_agent_said_reaches_the_room_stream_once(world):
    import fakeredis
    from control_plane import room_echo, rooms
    client, store, calls, spawned, root, _d = world
    sid = _room(client)
    r = fakeredis.FakeRedis(decode_responses=True)
    unit = rp.room_unit_id("team", sid)
    topic = units.output_topic(unit)
    for ev in ({"type": "message-delta", "text": "The proxy "}, {"type": "message-delta", "text": "supports TLS 1.3."},
               {"type": "turn-complete"}, {"type": "message-delta", "text": "half a turn"}):
        r.xadd(topic, {"event": json.dumps(ev)})
    assert room_echo.session_of(unit) == sid and room_echo.session_of(unit + "-signal") == sid
    assert room_echo.room_units(r) == [unit]
    assert room_echo.echo_unit(r, store, unit) == 1
    (entry,) = [json.loads(f["payload"]) for _id, f in r.xrange(rooms.stream_key(sid))]
    assert entry["author"]["kind"] == "agent" and entry["text"] == "The proxy supports TLS 1.3."
    assert entry["subject"] == "alice" and entry["session"] == sid
    assert room_echo.echo_unit(r, store, unit) == 0                      # never twice
    r.xadd(topic, {"event": json.dumps({"type": "turn-complete"})})      # the half turn finishes
    assert room_echo.echo_unit(r, store, unit) == 1
    assert json.loads(r.xrange(rooms.stream_key(sid))[-1][1]["payload"])["text"] == "half a turn"


# ── 1.4: a room plans a call; the creator's calendar sends it ────────────────────────────────────

def test_a_room_plans_a_call_and_nothing_is_sent(world, monkeypatch):
    from control_plane import room_calls
    client, store, calls, spawned, root, _d = world
    sid = _room(client)
    seen = []

    def fake(method, url, headers, body=None, timeout=8.0):
        seen.append((method, url, headers, body))
        if method == "POST":
            return 201, {"id": 501, "status": "scheduled",
                         "data": {"title": body["title"], "scheduled_at": body["scheduled_at"],
                                  "room": body["room"], "attendees": body.get("attendees", []),
                                  "agenda": body.get("agenda"), "auto_join": False}}
        return 200, {"id": 501, "data": {"room": sid, "calendar_event_ref": body["calendar_event_ref"]}}
    monkeypatch.setattr(room_calls, "http", fake)
    monkeypatch.setattr(_d.settings, "meeting_api_url", "http://meeting-api.test")
    r = client.post(f"/api/rooms/{sid}/plan", headers=_h("bob"),
                    json={"title": "Install sync", "start": "2026-10-20T15:00:00+02:00",
                          "invitees": ["ana@example.com"], "agenda": "kg/agenda/install.md"})
    assert r.status_code == 201, r.text
    method, url, headers, body = seen[0]
    assert (method, url) == ("POST", "http://meeting-api.test/meetings")
    assert body["room"] == sid and body["workspace_id"] == "team" and body["auto_join"] is False
    assert headers["X-User-Id"] == "bob" and headers["X-User-Writable-Workspaces"] == "team"
    out = r.json()
    assert out["calendar_draft"]["attendees"] == ["ana@example.com"] and out["ics"] == "/meetings/501/ics"
    assert len(seen) == 1                                                  # no calendar call, no mail
    rec = client.post(f"/api/rooms/{sid}/plan/501/calendar", json={"event_ref": "gcal:e1"}, headers=_h("bob"))
    assert rec.status_code == 200 and seen[-1][3] == {"calendar_event_ref": "gcal:e1"}
    worker = {"X-User-Id": "bob", "x-user-regime": "human", "x-user-delegation-workspaces": "*"}
    assert client.post(f"/api/rooms/{sid}/plan", headers=worker,
                       json={"title": "x", "start": "2026-10-20T15:00:00Z"}).status_code == 403
    assert client.post(f"/api/rooms/{sid}/plan", headers=_h("mallory"),
                       json={"title": "x", "start": "2026-10-20T15:00:00Z"}).status_code == 404


# ── 1.5: Vexa's direct edits during a call: recorded, listed, undoable ──────────────────────────

def _call_in(store, sid, room="call-abcd1234", meeting=77):
    store.bind_call(sid, room=room, meeting_id=meeting, owner="alice", share_token="77.tok-xyz")
    return room


def _agent_write(client, sid, path, content):
    unit = rp.room_unit_id("team", sid)
    hdr = {"X-User-Id": "alice", "x-user-regime": "human", "x-user-delegation-workspaces": "team",
           "x-user-delegation-unit": unit, "x-user-delegation-target": "team"}
    r = client.put("/api/workspace/file", headers=hdr, json={"path": path, "content": content})
    assert r.status_code == 200, r.text


def test_a_room_agents_write_during_a_call_is_recorded_and_undoable(world):
    client, store, calls, spawned, root, _d = world
    sid = _room(client)
    (root / "team" / "plan.md").write_text("# Plan\nv1\n")
    subprocess.run(["git", "-C", str(root / "team"), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root / "team"), "commit", "-qm", "seed"], check=True)
    room = _call_in(store, sid)
    _agent_write(client, sid, "plan.md", "# Plan\nv2 from the call\n")
    got = client.get(f"/api/calls/{room}/changes", headers=_h("bob")).json()
    (ch,) = got["changes"]
    assert ch["path"] == "plan.md" and ch["workspace"] == "team" and ch["undoable"] is True
    assert client.get(f"/api/calls/{room}/changes", headers=_h("mallory")).status_code == 404
    r = client.post(f"/api/calls/{room}/changes/{ch['id']}/undo", headers=_h("bob"))
    assert r.status_code == 200, r.text
    assert (root / "team" / "plan.md").read_text() == "# Plan\nv1\n"
    assert client.post(f"/api/calls/{room}/changes/{ch['id']}/undo", headers=_h("bob")).status_code == 409


def test_each_call_change_names_the_person_the_turn_answered(world, monkeypatch):
    """1.6: `on_behalf_of` on every call-change row is the speaker of the room turn that made the
    write - read server side, never from the worker - and follows the speaker turn by turn on the
    room's one warm worker (whose spawn-time env names only the first speaker)."""
    import fakeredis
    from control_plane import rooms
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(rooms, "redis_for", lambda url: r)
    client, store, calls, spawned, root, _d = world
    sid = _room(client)
    room = _call_in(store, sid)
    client.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa note the date"}, headers=_h("bob"))
    _agent_write(client, sid, "plan.md", "v1 for bob\n")
    r.delete(f"session:{sid}:lease")           # bob's turn completed (the saver releases the head)
    client.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa and the owner"}, headers=_h("alice"))
    _agent_write(client, sid, "plan.md", "v2 for alice\n")
    got = client.get(f"/api/calls/{room}/changes", headers=_h("bob")).json()["changes"]
    assert sorted(c["on_behalf_of"] for c in got) == ["alice", "bob"]
    # a worker cannot name someone else: a forged header loses to the server's record
    unit = rp.room_unit_id("team", sid)
    forged = {"X-User-Id": "alice", "x-user-regime": "human", "x-user-delegation-workspaces": "team",
              "x-user-delegation-unit": unit, "x-user-delegation-target": "team",
              "x-vexa-on-behalf-of": "mallory"}
    assert client.put("/api/workspace/file", headers=forged,
                      json={"path": "plan.md", "content": "v3\n"}).status_code == 200
    got = client.get(f"/api/calls/{room}/changes", headers=_h("bob")).json()["changes"]
    assert "mallory" not in {c["on_behalf_of"] for c in got} and len(got) == 3


def test_an_undo_after_someone_else_changed_the_page_becomes_a_proposal(world):
    client, store, calls, spawned, root, _d = world
    sid = _room(client)
    (root / "team" / "plan.md").write_text("v1\n")
    room = _call_in(store, sid)
    _agent_write(client, sid, "plan.md", "v2\n")
    (root / "team" / "plan.md").write_text("v3 by a person\n")
    (ch,) = client.get(f"/api/calls/{room}/changes", headers=_h("bob")).json()["changes"]
    r = client.post(f"/api/calls/{room}/changes/{ch['id']}/undo", headers=_h("bob"))
    assert r.status_code == 409 and r.json()["detail"]["refused"] == "changed_since"
    assert (root / "team" / "plan.md").read_text() == "v3 by a person\n"
    p = store.proposal(r.json()["detail"]["proposal"])
    assert p["body"] == "v1\n" and p["path"] == "plan.md" and p["owner"] == "bob"


def test_a_write_outside_a_call_or_by_a_person_is_not_a_call_change(world):
    client, store, calls, spawned, root, _d = world
    sid = _room(client)
    _agent_write(client, sid, "notes.md", "before any call\n")              # no call yet
    room = _call_in(store, sid)
    client.put("/api/workspace/file", headers=_h("bob"), json={"path": "notes.md", "content": "bob\n", "slug": "team"})
    assert client.get(f"/api/calls/{room}/changes", headers=_h("bob")).json()["changes"] == []


def test_the_recap_lists_the_calls_changes(world):
    client, store, calls, spawned, root, _d = world
    sid = _room(client)
    room = _call_in(store, sid)
    _agent_write(client, sid, "plan.md", "v2\n")
    from control_plane.api import _Sessions  # noqa: F401
    hdr = {"X-User-Id": "alice", "X-Internal-Secret": SECRET, "x-vexa-flow-regime": "autonomous",
           "x-vexa-flow-name": "post_meeting", "x-vexa-flow-scope": "instance", "x-vexa-flow-step": "agent_run"}
    app2 = create_app(_d, stream_reader=_FakeReader(), reader=WorkspaceReader(str(root)),
                      sessions=_Sessions(), session_store=store,
                      meeting_owner_lookup=lambda uid, mid, workspaces=None: {"id": 77, "user_id": uid, "data": {}})
    r = TestClient(app2).post("/api/chat/submit", json={"session": sid, "prompt": "Write the meeting's report.",
                                                        "room_meeting_id": "77"}, headers=hdr)
    assert calls, r.text
    inv, _kw = calls[-1]
    text = inv["start"]["entrypoint"]["inline"]
    assert "Changes you made in the workspace during the call" in text and "team/plan.md" in text


# ── docs review fixes (2026-10-16): #6 #7 #8, and the claims the docs could not verify ───────────

def test_proactivity_changes_what_the_sensor_runs():
    from control_plane import meeting_listen, rooms
    base = meeting_listen.normalize(meeting_listen.DEFAULT_BRIEF)
    quiet, active, off = (rooms.effective_brief(base, p) for p in ("quiet", "active", "off"))
    assert quiet == base
    assert active["wake_at"] == 0.5 and active["max_wait_s"] == 120 and quiet["max_wait_s"] == 300
    assert [lab["name"] for lab in off["labels"] if lab["wake"]] == ["addressed_to_vexa"]


def test_the_internal_brief_carries_the_rooms_proactivity(world):
    client, store, calls, spawned, root, _d = world
    sid = _room(client)
    client.patch(f"/api/sessions/{sid}/room", json={"proactivity": "active"}, headers=_h("bob"))
    got = client.get(f"/internal/chats/{sid}/listen?subject=alice", headers={"X-Internal-Secret": SECRET}).json()
    assert got["proactivity"] == "active" and got["brief"]["wake_at"] == 0.5


def test_off_still_wakes_when_addressed(world):
    client, store, calls, spawned, root, _d = world
    (root / "_global" / "POLICIES.md").write_text("---\ntranscript_signals: on\n---\n")
    sid = _room(client)
    store.put_room(sid, proactivity="off")
    ev = lambda reason: {"name": "room.signal", "subject": "alice", "target": {"chat": sid},  # noqa: E731
                         "plan": {"prompt": "x"}, "data": {"session": sid, "score": 1.0, "reason": reason,
                                                           "buffer": [{"segment_id": f"{sid}:1", "text": "@vexa?"}]}}
    assert client.post("/events", json=ev("threshold"), headers={"X-Internal-Secret": SECRET}).status_code == 409
    assert client.post("/events", json=ev("addressed"), headers={"X-Internal-Secret": SECRET}).status_code == 202


def test_the_room_says_when_its_agent_is_acting(world, monkeypatch):
    import fakeredis
    from control_plane import rooms, session_sync
    client, store, calls, spawned, root, _d = world
    sid = _room(client)
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(rooms, "redis_for", lambda url: r)
    assert client.get(f"/api/sessions/{sid}/room", headers=_h("bob")).json()["agent_busy"] is False
    session_sync.take_lease(r, sid, rp.room_unit_id("team", sid), kind="room")
    assert client.get(f"/api/sessions/{sid}/room", headers=_h("bob")).json()["agent_busy"] is True
    session_sync.release_lease(r, sid, rp.room_unit_id("team", sid))
    session_sync.take_lease(r, sid, units.chat_unit_id("bob", sid), kind="reply")   # a person's own turn
    assert client.get(f"/api/sessions/{sid}/room", headers=_h("bob")).json()["agent_busy"] is False


def test_the_owner_leaving_hands_the_room_on_and_the_last_one_cannot_leave(world):
    client, store, calls, spawned, root, _d = world
    sid = _room(client)
    r = client.post(f"/api/rooms/{sid}/leave", headers=_h("alice"))
    assert r.status_code == 200 and r.json()["handed_to"] == "bob"
    assert store.role_of(sid, "bob") == "owner" and store.role_of(sid, "alice") is None
    r = client.post(f"/api/rooms/{sid}/leave", headers=_h("bob"))
    assert r.status_code == 409 and r.json()["detail"]["refused"] == "hand_the_room_on_first"
    assert "hand it on" in r.json()["detail"]["said"]


def test_hand_on_is_the_owners_and_only_to_a_member(world):
    client, store, calls, spawned, root, _d = world
    sid = _room(client)
    assert client.post(f"/api/rooms/{sid}/hand-on", json={"to": "alice"}, headers=_h("bob")).status_code == 403
    r = client.post(f"/api/rooms/{sid}/hand-on", json={"to": "mallory"}, headers=_h("alice"))
    assert r.status_code == 403 and "members" in r.json()["detail"]["said"]
    r = client.post(f"/api/rooms/{sid}/hand-on", json={"to": "bob"}, headers=_h("alice"))
    assert r.status_code == 200 and r.json()["owner"] == "bob" and store.role_of(sid, "alice") == "member"


def test_a_room_turn_charges_the_workspace_owners_pool(world):
    client, store, calls, spawned, root, dispatcher = world
    charged = []

    class _Budget:
        def admit(self, subject):
            charged.append(subject)
    dispatcher._llm_budget = _Budget()
    sid = _room(client)
    client.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa go"}, headers=_h("bob"))
    client.post("/api/chat/submit", json={"session": "pchat-own", "prompt": "hi"}, headers=_h("bob"))
    assert charged == ["alice", "bob"]          # the room's turn: the owner's pool; bob's own chat: his


def test_a_room_token_is_refused_person_wide_reads(world):
    client, store, calls, spawned, root, _d = world
    sid = _room(client)
    client.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa read my mail"}, headers=_h("bob"))
    claims = _claims(spawned[-1][1])
    assert claims["scope"]["shared"] is True
    # what every service does with such a token, here agent-api's own person-wide reads
    hdr = {"X-User-Id": claims["sub"], "x-user-regime": "human", "x-user-delegation-shared": "1",
           "x-user-delegation-workspaces": ",".join(claims["scope"]["workspaces"])}
    for path in ("/api/connections", "/api/meeting/note?meeting=77"):
        r = client.get(path, headers=hdr)
        assert r.status_code == 403 and r.json()["detail"]["reason"] == "shared_conversation", path


def test_a_room_brief_version_goes_up_by_one_each_time(world):
    client, store, calls, spawned, root, _d = world
    sid = _room(client)
    v = [client.patch(f"/api/sessions/{sid}/room", json={"listen": {"focus": f"f{i}"}},
                      headers=_h("bob")).json()["listen_version"] for i in range(3)]
    assert v == [1, 2, 3]


# ── the test-account switch: `rooms` on for named accounts only ──────────────────────────────────

def test_rooms_can_be_switched_on_for_named_accounts_only(world, monkeypatch):
    from control_plane import features as F
    client, store, calls, spawned, root, _d = world
    monkeypatch.setenv("VEXA_FEATURES", "all")                     # off for the company
    (root / "_global" / "POLICIES.md").write_text("---\nfeatures_accounts: {rooms: bob|carol}\n---\n")
    assert F.effective(root / "_global")["rooms"] is False
    assert F.effective(root / "_global", subject="bob")["rooms"] is True
    assert F.on_for_anyone(root / "_global", "rooms") is True
    assert client.post("/api/rooms", json={"workspace": "team", "name": "x"}, headers=_h("alice")).status_code == 403
    r = client.post("/api/rooms", json={"workspace": "team", "name": "Test room"}, headers=_h("bob"))
    assert r.status_code == 201, r.text
    # alice's own chat is untouched: she can still add people to it (rooms are off for her)
    r = client.post("/api/sessions/pchat-a/participants", json={"email": "bob@acme.test", "history": "all"},
                    headers=_h("alice"))
    assert r.status_code == 200, r.text


def test_a_calendar_change_reaches_the_owners_room_calls_through_the_internal_door(world, monkeypatch):
    """PLAN-8H 8.4: the flows calendar sensor hands a moved or cancelled event on as its owner; this
    door forwards it to meeting-api as that owner. Internal tier only; nothing else is called."""
    from control_plane import room_calls
    client, *_rest, _d = world
    seen = []
    monkeypatch.setattr(room_calls, "http", lambda m, u, h, b=None, timeout=8.0: seen.append((m, u, h, b)) or (200, {"synced": [501]}))
    monkeypatch.setattr(_d.settings, "meeting_api_url", "http://meeting-api.test")
    body = {"event_id": "evt1", "status": "updated", "start": "2026-10-20T09:00:00Z", "end": "2026-10-20T09:30:00Z"}
    assert client.post("/api/rooms/calendar-sync", headers=_h("bob"), json=body).status_code == 403   # a person: no
    ok = client.post("/api/rooms/calendar-sync", headers={**_h("bob"), "X-Internal-Secret": SECRET}, json=body)
    assert ok.status_code == 200 and ok.json() == {"synced": [501]}
    (m, url, headers, sent), = seen
    assert (m, url) == ("POST", "http://meeting-api.test/meetings/calendar-sync")
    assert headers["X-User-Id"] == "bob" and sent == body
    bad = client.post("/api/rooms/calendar-sync", headers={**_h("bob"), "X-Internal-Secret": SECRET},
                      json={"event_id": "evt1", "status": "deleted"})
    assert bad.status_code == 422



def test_a_call_cancelled_or_moved_in_the_calendar_is_said_in_its_room(world, monkeypatch):
    """PLAN-8H 8.4: the room shows the calendar's change — a line from Vexa in the room the call
    belongs to, and only in a room the event's owner may write."""
    from control_plane import room_calls
    client, store, *_rest, _d = world
    sid = _room(client)
    monkeypatch.setattr(room_calls, "http", lambda m, u, h, b=None, timeout=8.0: (200, {"synced": [501, 502], "changes": [
        {"meeting_id": 501, "room": sid, "title": "Install sync", "status": "cancelled"},
        {"meeting_id": 502, "room": "s-not-theirs", "title": "Secret", "status": "cancelled"}]}))
    monkeypatch.setattr(_d.settings, "meeting_api_url", "http://meeting-api.test")
    ok = client.post("/api/rooms/calendar-sync", headers={**_h("bob"), "X-Internal-Secret": SECRET},
                     json={"event_id": "evt1", "status": "cancelled"})
    assert ok.status_code == 200
    texts = [n["text"] for n in store.notes(sid)]
    assert any("cancelled" in t and "Install sync" in t for t in texts)
    assert not any("Secret" in t for t in texts)



# -- "Vexa heard you · thinking…" from the moment the wake is accepted (founder, 2026-10-16) ------

def test_an_accepted_room_wake_shows_thinking_at_once(world, monkeypatch):
    import fakeredis
    from control_plane import rooms
    client, store, calls, spawned, root, _d = world
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(rooms, "redis_for", lambda _url: r)
    (root / "_global" / "POLICIES.md").write_text("---\ntranscript_signals: on\n---\n")
    sid = _room(client)
    assert client.get(f"/api/sessions/{sid}/room", headers=_h("bob")).json().get("agent_turn") is None
    ev = {"name": "room.signal", "subject": "alice", "target": {"chat": sid},
          "plan": {"prompt": "Messages since you last spoke."},
          "data": {"session": sid, "score": 1.0, "reason": "addressed",
                   "buffer": [{"segment_id": f"{sid}:1", "text": "vexa, what did we decide?"}]}}
    assert client.post("/events", json=ev, headers={"X-Internal-Secret": SECRET}).status_code == 202
    assert client.get(f"/api/sessions/{sid}/room", headers=_h("bob")).json()["agent_turn"] == "thinking"



def test_a_calls_wake_into_a_room_runs_as_the_room_agent(world, tmp_path):
    # The founder's call in a room woke `meeting.signal` with the room as its chat; the turn ran as the
    # person's least-privilege signal unit and "couldn't save to the workspace". A call's wake into a
    # room runs as the room's agent, with the room's scope.
    client, store, calls, spawned, root, dispatcher = world
    (root / "_global" / "POLICIES.md").write_text("---\ntranscript_signals: on\n---\n")
    sid = _room(client)
    rows = {"77": {"id": 77, "native_meeting_id": "call-x", "platform": "vexa_call", "data": {"title": "Call"}}}
    app = create_app(dispatcher, stream_reader=_FakeReader(), reader=WorkspaceReader(str(root)),
                     sessions=_Sessions(), session_store=store,
                     meeting_owner_lookup=lambda uid, mid, workspaces=None: rows.get(str(mid)) if uid == "alice" else None)
    c2 = TestClient(app, raise_server_exceptions=False)
    ev = {"name": "meeting.signal", "subject": "alice", "target": {"chat": sid},
          "plan": {"prompt": "Signals since your last look."},
          "data": {"meeting_row": "77", "score": 1.0, "reason": "addressed",
                   "buffer": [{"segment_id": "77:1", "speaker": "A", "text": "Vexa, note that", "start": 1.0}]}}
    r = c2.post("/events", json=ev, headers={"X-Internal-Secret": SECRET})
    assert r.status_code == 202, r.text
    uid, env = spawned[-1]
    assert uid == rp.room_unit_id("team", sid) + "-signal"
    assert env["VEXA_PRINCIPAL"] == "ws:team" and "VEXA_LEAST_PRIVILEGE" not in env
    # …and the call's note is made in THE ROOM'S workspace, where this turn can write it
    notes = list((root / "team" / "kg" / "entities" / "meeting").glob("*.md"))
    assert len(notes) == 1 and "meeting: 77" in notes[0].read_text()
    assert not (root / "alice" / "kg" / "entities" / "meeting").exists() or \
        not list((root / "alice" / "kg" / "entities" / "meeting").glob("*.md"))



# -- A ROOM IS JUST A CHAT WITH MORE THAN ONE PERSON (founder, 2026-10-16) --------------------------
# "create a room with eugenia" in a private chat writing to Family Health: the agent showed a card,
# then refused three times ("a room has to be created inside a workspace"). Adding a person to a
# private chat that targets a workspace turns it into a room of that workspace.

def _private_to(client, name, workspace):
    r = client.post("/api/chat/target", headers=_h("alice"), json={"session": name, "workspace": workspace})
    assert r.status_code == 200, r.text


def test_adding_a_member_to_a_private_chat_makes_it_a_room_of_its_workspace(world):
    client, store, calls, spawned, root, _d = world
    _private_to(client, "c1", "team")
    r = client.post("/api/sessions/c1/participants", headers=_h("alice"), json={"subject": "bob"})
    assert r.status_code == 200, r.text
    row = store.by_name("alice", "c1")
    room = store.room(row["id"])
    assert room is not None and room["workspace"] == "team"
    assert store.role_of(row["id"], "bob") is not None                    # history from the start is theirs


def test_start_fresh_makes_a_new_room_and_leaves_the_chat_private(world):
    client, store, calls, spawned, root, _d = world
    _private_to(client, "c2", "team")
    r = client.post("/api/sessions/c2/participants", headers=_h("alice"), json={"subject": "bob", "history": "none"})
    assert r.status_code == 200, r.text
    new = r.json()["session_id"]
    old = store.by_name("alice", "c2")
    assert new != old["id"] and store.room(old["id"]) is None and store.role_of(old["id"], "bob") is None
    assert store.room(new)["workspace"] == "team" and store.role_of(new, "bob") is not None


def test_a_person_outside_the_workspace_is_refused_with_its_title_and_the_way_in(world):
    client, store, calls, spawned, root, _d = world
    _private_to(client, "c3", "team")
    r = client.post("/api/sessions/c3/participants", headers=_h("alice"), json={"subject": "carol"})
    assert r.status_code == 403
    d = r.json()["detail"]
    assert d["refused"] == "not_a_workspace_member" and "workspace_invite" in d["said"]
    assert store.room(store.by_name("alice", "c3")["id"]) is None          # nothing changed


def test_the_tool_note_offers_the_card_and_names_the_history():
    tools = json.loads((Path(__file__).resolve().parents[1] / "mcp.tools.v1.json").read_text())
    note = next(t for t in tools["tools"] if t["name"] == "chat_participant_add")["note"]
    assert "room" in note and "```ask" in note and "history" in note and "Start a fresh room" in note
