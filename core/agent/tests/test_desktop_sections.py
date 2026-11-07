"""`POST /api/desktop/sections` — the desktop's agent-api half (desktop D1, Vexa-ai/vexa#1783).

The terminal's `/api/desktop` composes the desktop per request: crm-api says WHAT is placed and how
it is arranged, and this route says what each thing IS right now — the person's workspaces (name,
kind, access, members, last activity, last and live call, presence) and the placed agent and file
refs, resolved for this viewer. Nothing here is stored and no model is called.

L2: the real app over real git workspaces, a sqlite session store, the in-memory chat index and a
fake flows client and LiveKit room reader. No docker, no network.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient

from control_plane import home, routine_flows, system_mounts
from control_plane import workspace_attach as attach
from control_plane import workspace_membership as m
from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.identity_token import DELEGATION_HEADERS
from control_plane.livekit_rooms import LiveKit
from control_plane.session_store import SessionStore
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from tests._session_fakes import MemBlobs

SCHEMA = Path(__file__).resolve().parents[1] / "contracts" / "desktop.v1" / "desktop.schema.json"
URL = "/api/desktop/sections"


class _Runtime:
    spawned: list = []

    def spawn(self, workload_id, profile, env):
        _Runtime.spawned.append(workload_id)
        raise AssertionError("the desktop must not start a worker")

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


class _Rooms(LiveKit):
    rooms: dict = {}
    deleted: list = []

    def room(self, name):
        return self.rooms.get(name)

    def delete_room(self, name):
        self.deleted.append(name)
        self.rooms.pop(name, None)
        return True


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "u1", "GIT_AUTHOR_EMAIL": "u1@vexa.local",
                        "GIT_COMMITTER_NAME": "u1", "GIT_COMMITTER_EMAIL": "u1@vexa.local",
                        "HOME": str(cwd), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"})


def _seed(root: Path, subject: str) -> None:
    from shared.seeding import seed_workspace
    ws = root / subject
    ws.mkdir(parents=True)
    (ws / "CLAUDE.md").write_text("SEED")
    seed_workspace(ws, None)


class _Meetings:
    """meeting-api's `GET /meetings` as each viewer sees it — the `schedule_source` seam: their own
    meetings plus the ones shared with them. A row not in a viewer's list is one they cannot read."""
    by_subject: dict = {}

    def __call__(self, subject):
        return list(self.by_subject.get(subject, []))


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _meeting(mid, *, platform="google_meet", status="completed", start=None, end=None, title="", **data):
    now = time.time()
    start = now - 7200 if start is None else start
    end = (start + 1800 if status == "completed" else None) if end is None else end
    return {"id": mid, "platform": platform, "native_meeting_id": f"abc-defg-{mid}", "status": status,
            "start_time": _iso(start), "end_time": _iso(end) if end else None,
            "data": {**({"title": title} if title else {}), **data}}


def _note(ws_dir: Path, name: str, meeting_id) -> None:
    d = ws_dir / "kg" / "entities" / "meeting"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(f"---\ntype: meeting\nmeeting: {meeting_id}\ntitle: Notes\n---\n\nbody\n")


class _Flows:
    """The person's flows as flows-api lists them (`FlowsClient.routines` filters by scope)."""
    by_subject: dict = {}

    def routines(self, subject):
        return list(self.by_subject.get(subject, []))


def H(who: str) -> dict:
    return {"x-user-id": who, "x-user-email": f"{who}@example.test"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_FEATURES", "all")
    root = tmp_path / "ws"
    for s in ("u1", "u2", "u3"):
        _seed(root, s)
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    settings = load_settings(workspaces_dir=str(root), redis_url="",
                             global_system_workspace_path=str(root / "_global"))
    idx = m.InMemoryMembershipIndex()
    sess = _Sessions()
    lk = _Rooms(url="wss://calls.example.test", api_key="APIkey1", api_secret="s" * 32)
    _Rooms.rooms = {}
    _Flows.by_subject = {}
    _Meetings.by_subject = {}
    _Runtime.spawned = []
    monkeypatch.setattr(routine_flows, "configured", lambda: True)
    monkeypatch.setattr(routine_flows, "FlowsClient", _Flows)
    app = create_app(Dispatcher(settings, _Runtime(), _Identity()), reader=WorkspaceReader(str(root)),
                     membership_index=idx, sessions=sess, session_store=store, livekit=lk,
                     schedule_source=_Meetings())
    c = TestClient(app)
    yield {"c": c, "root": root, "idx": idx, "sess": sess, "store": store, "settings": settings}
    store.close()


def _shared(env, name, owner, **members):
    """Made the way the app makes one (so its name is registered), then the members granted."""
    root, idx = env["root"], env["idx"]
    r = env["c"].post("/api/workspace/shared/new", headers=H(owner), json={"name": name})
    assert r.status_code == 201, r.text
    wid = r.json()["workspace_id"]
    for who, role in members.items():
        m.grant_membership(root, wid, who, role, added_by=owner, index=idx, commit_fn=m.policy_commit)
    return wid


def _policy(env, line: str) -> None:
    g = system_mounts.global_root(env["settings"], str(env["root"]))
    g.mkdir(parents=True, exist_ok=True)
    (g / "POLICIES.md").write_text(f"---\nkind: policies\n{line}\n---\n\n# Policies\n")


def _post(env, who="u1", refs=(), headers=None):
    r = env["c"].post(URL, headers={**H(who), **(headers or {})}, json={"refs": list(refs)})
    return r


def _ok(env, who="u1", refs=()):
    r = _post(env, who, refs)
    assert r.status_code == 200, r.text
    body = r.json()
    schema = json.loads(SCHEMA.read_text())
    jsonschema.Draft202012Validator({"$ref": "#/$defs/DesktopSections", "$defs": schema["$defs"]}).validate(body)
    return body


def _ws(body, slug):
    return next((w for w in body["workspaces"] if w["slug"] == slug), None)


def _active(env, who, chat, target, age):
    env["sess"].upsert(who, chat)
    env["sess"].set_target(who, chat, target)
    env["sess"]._mem[who][chat]["last_active"] = time.time() - age


# ── the switch and who may ask ─────────────────────────────────────────────────────────────────

def test_switched_off_the_route_refuses_typed(env, monkeypatch):
    monkeypatch.delenv("VEXA_FEATURES")
    r = _post(env)
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "feature_disabled" and r.json()["detail"]["feature"] == "desktop"


def test_a_delegated_worker_is_refused(env):
    r = _post(env, headers={DELEGATION_HEADERS["workspaces"]: "*", DELEGATION_HEADERS["regime"]: "human"})
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "person_only"


def test_more_than_a_hundred_refs_is_refused(env):
    assert _post(env, refs=[f"agent:r{i}" for i in range(101)]).status_code == 422
    assert _post(env, refs=[f"agent:r{i}" for i in range(100)]).status_code == 200


# ── workspaces ─────────────────────────────────────────────────────────────────────────────────

def test_the_private_desk_lists_only_the_person(env):
    body = _ok(env)
    desk = _ws(body, "personal")
    assert desk["kind"] == "private" and desk["access"] == "owner" and desk["write"] is True
    assert desk["name"] == "Personal" and desk["id"] == "u1"
    assert [p["subject"] for p in desk["members"]] == ["u1"]
    assert desk["live_call"] is None


def test_a_shared_workspace_names_its_members_and_the_access(env):
    wid = _shared(env, "Client X", "u2", u1="contributor", u3="viewer")
    body = _ok(env, "u1")
    w = _ws(body, wid)
    assert w["name"] == "Client X" and w["kind"] == "shared"
    assert w["access"] == "contributor" and w["write"] is True
    roles = {p["subject"]: p["role"] for p in w["members"]}
    assert roles == {"u2": "owner", "u1": "contributor", "u3": "reader"}
    assert all(p["initials"] for p in w["members"])
    # a reader sees the workspace, read-only
    r3 = _ws(_ok(env, "u3"), wid)
    assert r3["access"] == "reader" and r3["write"] is False
    assert _ws(_ok(env, "u2"), wid)["access"] == "owner"


def test_a_workspace_nobody_shared_with_you_is_not_listed(env):
    wid = _shared(env, "Theirs", "u2")
    assert _ws(_ok(env, "u1"), wid) is None


def test_hidden_workspaces_are_counted_not_listed(env):
    a = attach.create_workspace(env["root"], "u1", name="Old one").slug
    b = attach.create_workspace(env["root"], "u1", name="Kept").slug
    attach.set_workspace_state(env["root"], "u1", a, "hidden")
    body = _ok(env)
    assert _ws(body, a) is None and _ws(body, b) is not None
    assert body["hidden_workspaces"] == 1


def test_the_company_layer_is_listed_read_only_when_present(env):
    _policy(env, "presence_visibility: workspace_members")
    w = _ws(_ok(env), "_global")
    assert w["kind"] == "company" and w["name"] == "Company"
    assert w["access"] == "reader" and w["write"] is False


# ── presence ───────────────────────────────────────────────────────────────────────────────────

def _presence(body, wid, who):
    return next(p for p in _ws(body, wid)["members"] if p["subject"] == who)["presence"]


def test_presence_by_default_is_activity_in_that_workspace(env):
    wid = _shared(env, "Client X", "u1", u2="contributor", u3="contributor")
    _active(env, "u2", "pchat-a", wid, age=60)          # now
    _active(env, "u3", "pchat-b", wid, age=3 * 3600)    # today
    _active(env, "u3", "pchat-c", "", age=10)           # elsewhere: never counts here
    body = _ok(env)
    assert body["presence_visibility"] == "workspace_members"
    assert _presence(body, wid, "u2") == "now"
    assert _presence(body, wid, "u3") == "today"
    assert _presence(body, wid, "u1") is None            # never yourself


def test_activity_older_than_a_day_is_no_presence(env):
    wid = _shared(env, "Client X", "u1", u2="contributor")
    _active(env, "u2", "pchat-a", wid, age=2 * 86400)
    assert _presence(_ok(env), wid, "u2") is None


def test_presence_off_shows_nobody(env):
    wid = _shared(env, "Client X", "u1", u2="contributor")
    _active(env, "u2", "pchat-a", wid, age=60)
    _policy(env, "presence_visibility: off")
    body = _ok(env)
    assert body["presence_visibility"] == "off"
    assert _presence(body, wid, "u2") is None


def test_presence_shared_chats_is_only_chats_you_share(env):
    wid = _shared(env, "Client X", "u1", u2="contributor", u3="contributor")
    _active(env, "u2", "pchat-a", wid, age=60)           # u2 works there, shares nothing with u1
    sid = env["store"].create("u3", name="pchat-together")["id"]
    env["store"].add_participant(sid, "u3", "u1")         # u3 shares a chat with u1, active now
    _policy(env, "presence_visibility: shared_chats")
    body = _ok(env)
    assert body["presence_visibility"] == "shared_chats"
    assert _presence(body, wid, "u2") is None
    assert _presence(body, wid, "u3") == "now"


def test_a_person_who_switched_presence_off_never_shows(env):
    wid = _shared(env, "Client X", "u1", u2="contributor")
    _active(env, "u2", "pchat-a", wid, age=60)
    home.HomeStore(env["store"]).record("u2", card="presence:off", event="evidence")
    assert _presence(_ok(env), wid, "u2") is None


def test_a_value_nobody_can_read_shows_no_presence(env):
    wid = _shared(env, "Client X", "u1", u2="contributor")
    _active(env, "u2", "pchat-a", wid, age=60)
    _policy(env, "presence_visibility: everyone")
    body = _ok(env)
    assert body["presence_visibility"] == "off" and _presence(body, wid, "u2") is None


# ── calls ──────────────────────────────────────────────────────────────────────────────────────

def test_a_call_in_a_chat_working_in_the_workspace_is_live_then_last(env):
    wid = _shared(env, "Client X", "u1", u2="contributor")
    store = env["store"]
    row = store.create("u1", name="pchat-call", title="Weekly sync")
    env["sess"].set_target("u1", "pchat-call", wid)
    store.bind_call(row["id"], room="call-abcd1", meeting_id=41, owner="u1", share_token="41.x")
    _Rooms.rooms["call-abcd1"] = {"name": "call-abcd1", "num_participants": 2, "metadata": {}}
    w = _ws(_ok(env), wid)
    assert w["live_call"]["meeting"] == "41" and w["live_call"]["people"] == 2
    assert w["live_call"]["session"] == "pchat-call"
    store.end_call(row["id"], "call-abcd1")
    w = _ws(_ok(env), wid)
    assert w["live_call"] is None
    assert w["last_call"]["meeting"] == "41" and w["last_call"]["line"].startswith("Weekly sync")


def test_a_room_that_closed_is_not_live(env):
    wid = _shared(env, "Client X", "u1")
    row = env["store"].create("u1", name="pchat-call")
    env["sess"].set_target("u1", "pchat-call", wid)
    env["store"].bind_call(row["id"], room="call-gone1", meeting_id=7, owner="u1", share_token="7.x")
    assert _ws(_ok(env), wid)["live_call"] is None


# ── every meeting, whatever the kind (bot meetings and Vexa calls) ─────────────────────────────

def test_a_bot_meeting_whose_note_is_in_the_workspace_is_its_last_call(env):
    _note(env["root"] / "u1", "2026-10-14-1000-weekly.md", 501)
    _Meetings.by_subject = {"u1": [_meeting(501, platform="zoom", title="Pricing review")]}
    w = _ws(_ok(env), "personal")
    assert w["last_call"]["meeting"] == "501" and w["last_call"]["platform"] == "zoom"
    assert w["last_call"]["line"].startswith("Pricing review · ")
    assert w["live_call"] is None


def test_the_row_recording_its_note_path_places_it_where_that_file_is(env):
    wid = _shared(env, "Client X", "u1", u2="contributor")
    path = "kg/entities/meeting/2026-10-14-0900-client-x.md"
    (env["root"] / wid / "kg" / "entities" / "meeting").mkdir(parents=True, exist_ok=True)
    (env["root"] / wid / path).write_text("---\ntype: meeting\n---\n")
    _Meetings.by_subject = {"u1": [_meeting(502, platform="teams", title="Client X sync",
                                            metadata={"note_path": path})]}
    body = _ok(env)
    assert _ws(body, wid)["last_call"]["meeting"] == "502"
    assert _ws(body, wid)["last_call"]["platform"] == "teams"
    assert _ws(body, "personal")["last_call"] is None


def test_a_live_bot_meeting_from_a_chat_working_in_the_workspace_is_its_live_call(env):
    wid = _shared(env, "Client X", "u1", u2="contributor")
    env["sess"].upsert("u1", "pchat-sent", meeting="503")
    env["sess"].set_target("u1", "pchat-sent", wid)
    started = time.time() - 600
    _Meetings.by_subject = {"u1": [_meeting(503, status="active", start=started, title="Standup",
                                            participants=["Ana", "Ben"])]}
    w = _ws(_ok(env), wid)
    live = w["live_call"]
    assert live["meeting"] == "503" and live["platform"] == "google_meet"
    assert live["since"] == int(started) and live["line"] == "Standup" and live["people"] == 2
    assert "session" not in live                          # Join opens the meeting, not a chat
    assert w["last_call"] is None


def test_a_meeting_bound_to_a_shared_workspace_counts_there(env):
    wid = _shared(env, "Client X", "u2", u1="contributor")
    _Meetings.by_subject = {"u1": [_meeting(504, title="Their review", workspace_id=wid)]}
    assert _ws(_ok(env), wid)["last_call"]["meeting"] == "504"


def test_a_meeting_the_viewer_cannot_read_does_not_count(env):
    wid = _shared(env, "Client X", "u2", u1="contributor")
    _note(env["root"] / wid, "2026-10-14-1100-private.md", 505)      # u2's note, in the shared tree
    env["sess"].upsert("u2", "pchat-u2", meeting="505")
    env["sess"].set_target("u2", "pchat-u2", wid)
    _Meetings.by_subject = {"u2": [_meeting(505, title="Board prep")]}   # u2's meeting only
    w = _ws(_ok(env, "u1"), wid)
    assert w["last_call"] is None and w["live_call"] is None
    assert "Board prep" not in json.dumps(_ok(env, "u1"))
    assert _ws(_ok(env, "u2"), wid)["last_call"]["meeting"] == "505"


def test_a_meeting_that_never_started_or_is_a_voice_note_is_not_a_call(env):
    _note(env["root"] / "u1", "2026-10-14-1200-planned.md", 506)
    _note(env["root"] / "u1", "2026-10-14-1300-memo.md", 507)
    _Meetings.by_subject = {"u1": [{**_meeting(506, status="scheduled"), "start_time": None, "end_time": None},
                                   _meeting(507, platform="voice_note")]}
    assert _ws(_ok(env), "personal")["last_call"] is None


def test_a_vexa_call_still_counts_and_says_its_platform(env):
    wid = _shared(env, "Client X", "u1", u2="contributor")
    row = env["store"].create("u1", name="pchat-call", title="Weekly sync")
    env["sess"].set_target("u1", "pchat-call", wid)
    env["store"].bind_call(row["id"], room="call-v1", meeting_id=41, owner="u1", share_token="41.x")
    _Rooms.rooms["call-v1"] = {"name": "call-v1", "num_participants": 3, "metadata": {}}
    live = _ws(_ok(env), wid)["live_call"]
    assert live["platform"] == "vexa_call" and live["session"] == "pchat-call" and live["people"] == 3
    env["store"].end_call(row["id"], "call-v1")
    last = _ws(_ok(env), wid)["last_call"]
    assert last["platform"] == "vexa_call" and last["line"].startswith("Weekly sync · ")


def test_the_newer_of_a_vexa_call_and_a_bot_meeting_wins(env):
    wid = _shared(env, "Client X", "u1", u2="contributor")
    row = env["store"].create("u1", name="pchat-call", title="Weekly sync")
    env["sess"].set_target("u1", "pchat-call", wid)
    env["store"].bind_call(row["id"], room="call-v2", meeting_id=41, owner="u1", share_token="41.x")
    env["store"].end_call(row["id"], "call-v2")                          # ended just now
    _Meetings.by_subject = {"u1": [_meeting(508, title="Older review", workspace_id=wid,
                                            start=time.time() - 86400 * 2)]}
    assert _ws(_ok(env), wid)["last_call"]["meeting"] == "41"
    _Meetings.by_subject = {"u1": [_meeting(509, title="Newer review", workspace_id=wid,
                                            start=time.time() + 60, end=time.time() + 120)]}
    last = _ws(_ok(env), wid)["last_call"]
    assert last["meeting"] == "509" and last["platform"] == "google_meet"


def test_a_name_that_is_an_id_is_never_the_line(env):
    _note(env["root"] / "u1", "2026-10-14-1400-x.md", 510)
    _Meetings.by_subject = {"u1": [_meeting(510, title="abc-defg-hij", participants=["Valeriia"])]}
    line = _ws(_ok(env), "personal")["last_call"]["line"]
    assert line.startswith("Call with Valeriia · ") and "abc-defg" not in line


# ── agents ─────────────────────────────────────────────────────────────────────────────────────

def _flow(name, *, status="active", last=None, instruction="Brief me every morning", thread=None):
    f = {"name": name, "version": 1, "scope": "user:u1", "trigger": {"kind": "schedule", "cron": "0 7 * * *"},
         "steps": ["agent_run"], "status": status, "params": {"instruction": instruction},
         "schedule": "daily at 07:00 UTC", "next_run_at": time.time() + 3600}
    if thread:
        f["params"]["thread"] = thread
    if last:
        f["last_run"] = last
    return f


def test_an_agent_ref_resolves_against_the_persons_own_routines(env):
    wid = _shared(env, "Client X", "u1")
    _Flows.by_subject = {"u1": [_flow("morning-brief", thread="pchat-mb",
                                      last={"status": "done", "ended_at": time.time() - 60}),
                                _flow("weekly", status="paused"),
                                _flow("broken", last={"status": "failed", "reason": "no access",
                                                      "ended_at": time.time() - 60})],
                         "u2": [{**_flow("theirs"), "scope": "user:u2"}]}
    env["sess"].set_target("u1", "pchat-mb", wid)
    body = _ok(env, refs=["agent:flow:morning-brief", "agent:weekly", "agent:broken", "agent:theirs",
                          "agent:gone"])
    a = {x["ref"]: x for x in body["agents"]}
    mb = a["agent:flow:morning-brief"]
    assert not mb["missing"] and mb["name"] == "Morning brief" and mb["status"] == "idle"
    assert mb["what"] == "Brief me every morning" and mb["where"] == "Client X" and mb["thread"] == "pchat-mb"
    assert mb["next"] == "daily at 07:00 UTC" and mb["last"]
    assert a["agent:weekly"]["status"] == "paused"
    assert a["agent:broken"]["status"] == "failed"
    assert a["agent:theirs"] == {"ref": "agent:theirs", "missing": True}
    assert a["agent:gone"] == {"ref": "agent:gone", "missing": True}


def test_no_agent_refs_asks_flows_nothing(env, monkeypatch):
    class _Boom:
        def routines(self, subject):
            raise AssertionError("flows asked for nothing")
    monkeypatch.setattr(routine_flows, "FlowsClient", _Boom)
    assert _ok(env)["agents"] == []


# ── files ──────────────────────────────────────────────────────────────────────────────────────

def test_a_file_ref_resolves_under_the_viewers_access(env):
    desk = env["root"] / "u1"
    (desk / "notes").mkdir()
    (desk / "notes" / "x.md").write_text("---\ntitle: Pricing notes\n---\n\nbody\n")
    _git(desk, "add", "notes/x.md")
    _git(desk, "commit", "-m", "notes")
    theirs = _shared(env, "Theirs", "u2")
    body = _ok(env, refs=["file:u1/notes/x.md", "file:personal/notes/x.md", "file:u1/notes/gone.md",
                          f"file:{theirs}/README.md", "file:_system/settings.md", "file:nope"])
    f = {x["ref"]: x for x in body["files"]}
    x = f["file:u1/notes/x.md"]
    assert not x["missing"] and x["title"] == "Pricing notes" and x["where"] == "Personal"
    assert x["slug"] == "u1" and x["path"] == "notes/x.md" and x["changed"]
    assert f["file:personal/notes/x.md"]["title"] == "Pricing notes"
    for gone in ("file:u1/notes/gone.md", f"file:{theirs}/README.md", "file:_system/settings.md", "file:nope"):
        assert f[gone] == {"ref": gone, "missing": True}, gone


# ── the composition is plain code ──────────────────────────────────────────────────────────────

def test_no_model_is_called_and_nothing_is_stored(env, monkeypatch):
    from control_plane import home_model
    for name in dir(home_model):
        if not name.startswith("_") and callable(getattr(home_model, name)):
            monkeypatch.setattr(home_model, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError("model")))
    wid = _shared(env, "Client X", "u1", u2="contributor")
    before = sorted(p.name for p in (env["root"] / "u1").rglob("*") if ".git" not in p.parts)
    a, b = _ok(env), _ok(env)
    assert a == b                                           # deterministic
    assert _Runtime.spawned == []
    after = sorted(p.name for p in (env["root"] / "u1").rglob("*") if ".git" not in p.parts)
    assert before == after
    assert _ws(a, wid) is not None


def test_unknown_and_card_refs_are_not_resolved_here(env):
    body = _ok(env, refs=["card:abc", "workspace:personal", "weird"])
    assert body["agents"] == [] and body["files"] == []


def test_every_golden_conforms_to_the_schema():
    schema = json.loads(SCHEMA.read_text())
    goldens = sorted((SCHEMA.parent / "golden").glob("*.json"))
    assert goldens
    for g in goldens:
        shape = g.name.split(".")[0]
        jsonschema.Draft202012Validator({"$ref": f"#/$defs/{shape}", "$defs": schema["$defs"]}).validate(
            json.loads(g.read_text()))


# ── chats (founder 2026-10-15: the "selected agents" are chats as well as routines) ────────────

def test_a_placed_chat_is_its_title_its_last_activity_and_where_it_works(env):
    wid = _shared(env, "Pricing", "u1")
    env["sess"].upsert("u1", "pchat-a1", title="Pricing prep")
    env["sess"].set_target("u1", "pchat-a1", wid)
    env["sess"]._mem["u1"]["pchat-a1"]["last_active"] = time.mktime((2026, 10, 14, 12, 0, 0, 0, 0, -1))
    env["sess"].upsert("u2", "pchat-u2", title="Not yours")
    env["sess"].upsert("u1", "pchat-untitled")
    body = _ok(env, refs=["chat:pchat-a1", "chat:pchat-u2", "chat:pchat-none", "chat:pchat-untitled"])
    c = {x["ref"]: x for x in body["chats"]}
    a = c["chat:pchat-a1"]
    assert a["id"] == "pchat-a1" and a["title"] == "Pricing prep" and a["missing"] is False
    assert a["last"] == "Active Oct 14" and a["where"] == "Pricing" and isinstance(a["last_at"], int)
    # another person's chat, and one that does not exist, say nothing
    assert c["chat:pchat-u2"] == {"ref": "chat:pchat-u2", "missing": True}
    assert c["chat:pchat-none"] == {"ref": "chat:pchat-none", "missing": True}
    # a chat nobody named is never shown by its id
    assert c["chat:pchat-untitled"]["title"] == "Untitled chat"
    assert c["chat:pchat-untitled"]["where"] == "Personal"


def test_without_chat_refs_the_answer_has_no_chats(env):
    body = _ok(env, refs=["agent:x"])
    assert body.get("chats", []) == []



def test_a_call_with_nobody_in_it_is_not_live_and_an_old_one_is_ended(env):
    """Founder 2026-10-16: "Call now · room — Citizenship applications · 0 people · Join". A call is
    live on the home only while a person is in it (the hidden listener is nobody); one nobody has been
    in for its grace is ended by its room's own path."""
    import time as _t
    wid = _shared(env, "Citizenship applications", "u1", u2="contributor")
    store = env["store"]
    row = store.create("u1", name="pchat-orphan", title="Citizenship applications")
    env["sess"].set_target("u1", "pchat-orphan", wid)
    store.bind_call(row["id"], room="call-orph01", meeting_id=146, owner="u1", share_token="146.x")
    _Rooms.deleted = []
    _Rooms.rooms["call-orph01"] = {"name": "call-orph01", "num_participants": 1, "humans": 0, "metadata": {}}
    w = _ws(_ok(env), wid)
    assert w["live_call"] is None                    # nobody in it: never "0 people · Join"
    assert _Rooms.deleted == []                      # young: the room's own timeouts still apply
    with store._tx():
        store._x("UPDATE agent_session_call SET started = ? WHERE room = ?", (_t.time() - 600, "call-orph01"))
    w = _ws(_ok(env), wid)
    assert w["live_call"] is None
    assert _Rooms.deleted == ["call-orph01"]          # empty past its grace: ended
