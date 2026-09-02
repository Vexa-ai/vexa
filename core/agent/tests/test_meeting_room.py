"""The post-meeting MEETING ROOM — the read-only mounts of a meeting's other attendees.

The room widens what ONE agent turn may read, so every test here is about the boundary rather than
the feature: who may open a room, whose workspaces land in it, that they land READ-ONLY, that nobody
else's _system is ever reachable, that a caller cannot name its way into somebody's desk, and
that a dispatch which names no meeting is byte-identical to before.

Backend-free (fakes / tmp dirs, no docker/kubectl/meeting-api) — the mount plumbing is proven offline.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import meeting_room
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher, build_mount_set
from control_plane.meeting_room import (MAX_ROOM_READ, RoomRefused, room_mounts, select_room,
                                        verified_subjects)
from shared.config import load_settings

INTERNAL_SECRET = "s3cr3t-internal"


# ── fixtures: a store with real workspace dirs, and a fake meeting-api ────────────────────────────

def _git_repo(d: Path, marker: str = "X") -> Path:
    d.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(["git", *a], cwd=d, check=True, capture_output=True)
    run("init", "-q", "-b", "main"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
    (d / "CLAUDE.md").write_text(marker); run("add", "-A"); run("commit", "-q", "-m", "seed")
    return d


def _seed(root: Path, subject: str) -> Path:
    """A seeded private baseline at <root>/<subject> (what active_workspaces resolves)."""
    from shared.seeding import seed_workspace
    ws = root / subject
    ws.mkdir(parents=True)
    (ws / "CLAUDE.md").write_text(f"SEED {subject}")
    seed_workspace(ws, None)
    return ws


def _settings(tmp_path, root: Path):
    return load_settings(workspaces_dir=str(root),
                         global_system_workspace_path=str(_git_repo(tmp_path / "global", "GLOBAL")),
                         internal_api_secret=INTERNAL_SECRET)


def _row(owner: str, meeting_id="42", viewers=None, shared=False, data_extra=None) -> dict:
    """A meeting row shaped like meeting-api's GET /meetings/{id} answer to its OWNER."""
    data = {"transcript_viewers": list(viewers or [])}
    data.update(data_extra or {})
    return {"id": meeting_id, "user_id": owner, "native_meeting_id": "abc-def",
            "shared": shared, "data": data}


# ── gate 3+4: ownership, and the roster as the room's ceiling ─────────────────────────────────────

def test_verified_subjects_is_the_meetings_own_reader_roster():
    subs = verified_subjects(_row("u_owner", viewers=["u_bob", "u_carol"]), requester="u_owner")
    assert subs == ["u_bob", "u_carol"]


def test_the_requester_is_never_in_their_own_room():
    """Their workspaces are already in the stack READ-WRITE; a room copy would shadow them ro."""
    subs = verified_subjects(_row("u_owner", viewers=["u_owner", "u_bob"]), requester="u_owner")
    assert subs == ["u_bob"]


def test_an_unentitled_caller_is_refused():
    """None from the entitlement lookup (absent row / another tenant / meeting-api down)."""
    with pytest.raises(RoomRefused, match="not authorized"):
        verified_subjects(None, requester="u_mallory")


def test_a_share_recipient_may_not_open_a_room():
    """They pass the meeting ACCESS check (they can read the transcript) but the roster is the
    owner's; a recipient opening a room would turn one share into a read of every attendee's desk."""
    row = _row("u_owner", viewers=["u_bob"], shared=True)
    with pytest.raises(RoomRefused, match="owner"):
        verified_subjects(row, requester="u_bob")


def test_ownership_needs_positive_evidence():
    """An absent/blank user_id refuses rather than defaulting to 'probably theirs'."""
    row = _row("u_owner", viewers=["u_bob"]); row["user_id"] = ""
    with pytest.raises(RoomRefused, match="owner"):
        verified_subjects(row, requester="u_owner")


def test_a_meeting_with_no_other_readers_is_an_empty_room_not_an_error():
    assert verified_subjects(_row("u_owner", viewers=[]), requester="u_owner") == []
    row = _row("u_owner"); row["data"] = {}
    assert verified_subjects(row, requester="u_owner") == []


# ── gate 5: propose-and-verify — a caller may narrow, never widen ─────────────────────────────────

def test_a_proposal_can_only_narrow_the_verified_room():
    selected, rejected = select_room(["u_bob", "u_carol"], proposed=["u_carol", "u_mallory"])
    assert selected == ["u_carol"]          # u_mallory was never in the meeting
    assert rejected == ["u_mallory"]        # …and the rejection is reported, never silent


def test_a_proposal_supplies_ORDER_speaking_time_first():
    selected, _ = select_room(["u_bob", "u_carol"], proposed=["u_carol", "u_bob"])
    assert selected == ["u_carol", "u_bob"]


def test_no_proposal_means_the_whole_verified_roster():
    assert select_room(["u_bob", "u_carol"], proposed=None) == (["u_bob", "u_carol"], [])


def test_the_cap_bounds_a_runaway_roster():
    many = [f"u_{i}" for i in range(200)]
    selected, _ = select_room(many, proposed=None)
    assert len(selected) == meeting_room.DEFAULT_ROOM_READ_MAX == 12
    # a caller may LOWER the cap …
    assert len(select_room(many, cap=3)[0]) == 3
    # … but never raise it past the server's own ceiling
    assert len(select_room(many, cap=10_000)[0]) == MAX_ROOM_READ


def test_a_proposal_of_junk_shapes_authorises_nothing():
    selected, _ = select_room(["u_bob"], proposed=[None, True, {"id": "u_bob"}, "", "u_bob", "u_bob"])
    assert selected == ["u_bob"]


# ── the mounts themselves ────────────────────────────────────────────────────────────────────────

def test_room_mounts_are_read_only_and_never_primary(tmp_path):
    root = tmp_path / "ws"; _seed(root, "u_bob")
    mounts = room_mounts(str(root), ["u_bob"], meeting_id="42")
    assert len(mounts) == 1
    m = mounts[0]
    assert m["write"] is False and m["primary"] is False and m["role"] == "room"
    assert m["slug"] == "room:u_bob" and m["path"] == str(root / "u_bob")
    assert m["room"] == {"meeting_id": "42", "subject": "u_bob"}


def test_a_room_mount_never_creates_the_attendees_workspace(tmp_path):
    """An attendee who has never onboarded is simply absent from the room — a post-meeting read
    must not mint somebody a workspace as a side effect."""
    root = tmp_path / "ws"; root.mkdir()
    assert room_mounts(str(root), ["u_never_seen"], meeting_id="42") == []
    assert not (root / "u_never_seen").exists()


def test_a_room_mount_never_shadows_the_subjects_own_stack(tmp_path):
    root = tmp_path / "ws"; _seed(root, "u_bob")
    taken = {str(root / "u_bob")}
    assert room_mounts(str(root), ["u_bob"], meeting_id="42", taken_paths=taken) == []


def test_one_attendee_with_a_broken_store_does_not_lose_the_others(tmp_path, monkeypatch):
    root = tmp_path / "ws"; _seed(root, "u_bob"); _seed(root, "u_carol")
    real = meeting_room.active_workspaces

    def flaky(r, subject):
        if subject == "u_bob":
            raise OSError("store hiccup")
        return real(r, subject)

    monkeypatch.setattr(meeting_room, "active_workspaces", flaky)
    slugs = [m["slug"] for m in room_mounts(str(root), ["u_bob", "u_carol"], meeting_id="42")]
    assert slugs == ["room:u_carol"]


def test_no_other_subjects_system_workspace_is_ever_mounted(tmp_path):
    """_system is the ONE tier that stays private. It lives at <root>/.system/<subject> and
    active_workspaces refuses a dot-name, so the room cannot reach it — asserted, not assumed."""
    from control_plane.system_mounts import ensure_system_workspace
    root = tmp_path / "ws"; _seed(root, "u_bob")
    bob_system = ensure_system_workspace(str(root), "u_bob")
    mounts = room_mounts(str(root), ["u_bob", ".system", "../.."], meeting_id="42")
    paths = {m["path"] for m in mounts}
    assert str(bob_system) not in paths
    assert all("/.system/" not in p for p in paths)
    assert paths == {str(root / "u_bob")}


# ── composition into the mount STACK ─────────────────────────────────────────────────────────────

def test_the_stack_is_unchanged_when_no_room_is_named(tmp_path):
    """REGRESSION: every dispatch that names no meeting must be byte-identical to before."""
    root = tmp_path / "ws"; _seed(root, "u_owner")
    settings = _settings(tmp_path, root)
    assert build_mount_set(settings, "u_owner") == build_mount_set(settings, "u_owner", room=None)
    assert [m["role"] for m in build_mount_set(settings, "u_owner")] == ["global", "private", "system"]


def test_room_mounts_sit_between_the_active_set_and_system(tmp_path):
    root = tmp_path / "ws"
    for s in ("u_owner", "u_bob", "u_carol"):
        _seed(root, s)
    settings = _settings(tmp_path, root)
    stack = build_mount_set(settings, "u_owner",
                            room={"meeting_id": "42", "subjects": ["u_bob", "u_carol"]})
    assert [m["role"] for m in stack] == ["global", "private", "room", "room", "system"]
    rooms = [m for m in stack if m["role"] == "room"]
    assert [m["slug"] for m in rooms] == ["room:u_bob", "room:u_carol"]
    assert all(m["write"] is False for m in rooms)
    # the subject's OWN tiers keep their access exactly as before
    assert stack[1]["write"] is True and stack[-1]["write"] is True


def test_a_broken_room_never_kills_the_dispatch(tmp_path, monkeypatch):
    root = tmp_path / "ws"; _seed(root, "u_owner")
    settings = _settings(tmp_path, root)
    import control_plane.dispatch as disp

    def boom(*a, **k):
        raise OSError("store gone")

    monkeypatch.setattr(disp, "room_mounts", boom)
    stack = build_mount_set(settings, "u_owner", room={"meeting_id": "42", "subjects": ["u_bob"]})
    assert [m["role"] for m in stack] == ["global", "private", "system"]


def test_the_runtime_binds_a_room_mount_read_only(tmp_path):
    """The write flag is not decoration: the shared runtime mount plumbing turns it into a :ro bind,
    so read-only is enforced by the container's mount table rather than by asking the model nicely."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runtime" / "src"))
    from runtime_kernel.mounts import workspace_binds

    env = {"VEXA_WORKSPACE_MOUNT_SOURCE": "/host/store", "VEXA_WORKSPACE_MOUNT_TARGET": "/workspaces",
           "VEXA_MOUNTS": json.dumps([
               {"slug": "u_owner", "path": "/workspaces/u_owner", "role": "private", "write": True},
               {"slug": "room:u_bob", "path": "/workspaces/u_bob", "role": "room", "write": False},
           ])}
    binds = {b.target: b.read_only for b in workspace_binds(env)}
    assert binds["/workspaces/u_owner"] is False
    assert binds["/workspaces/u_bob"] is True


# ── the delegation token: deliberately UNCHANGED by a room ────────────────────────────────────────

class _FakeRuntime:
    def __init__(self):
        self.spawned = []

    def spawn(self, workload_id, profile, env):
        self.spawned.append((workload_id, profile, env))
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


def _inv(subject="u_owner", trigger="message"):
    return {"identity": {"subject": subject, "launcher": f"user:{subject}"},
            "runner": "claude-code", "workspaces": [{"id": subject, "mode": "rw"}],
            "trigger": trigger, "context": {"kind": "none"},
            "start": {"entrypoint": {"inline": "write it up"}}}


def test_a_room_does_not_widen_the_workers_delegation_token(tmp_path):
    """The delegation scope is a CEILING ON THE ACCOUNT (delegation.scope_allows_workspace), so
    naming another attendee's workspace in it would ask the control MCP to hand THIS uid a workspace
    it does not own — inert at best, a real widening at worst. The room is a MOUNT-level grant the
    container's mount table enforces; the credential must not move. Same bytes, room or no room."""
    from shared import delegation
    root = tmp_path / "ws"
    for s in ("u_owner", "u_bob"):
        _seed(root, s)
    settings = load_settings(workspaces_dir=str(root),
                             global_system_workspace_path=str(_git_repo(tmp_path / "g", "G")),
                             mcp_delegation_secret="delegation-key", mcp_url="http://mcp:9000")

    def token_scope(room):
        d = Dispatcher(settings, _FakeRuntime(), _FakeIdentity())
        d.dispatch(_inv(), room=room)
        env = d._runtime.spawned[-1][2]
        claims = delegation.verify_delegation("delegation-key", env["VEXA_MCP_DELEGATION_TOKEN"])
        return claims["sub"], claims["scope"], json.loads(env["VEXA_WORKSPACES"])

    plain = token_scope(None)
    with_room = token_scope({"meeting_id": "42", "subjects": ["u_bob"]})
    assert plain == with_room
    assert with_room[1] == {"regime": "human", "workspaces": "*"}   # the subject's OWN account, only
    assert with_room[2] == [{"id": "u_owner", "mode": "rw"}]        # no attendee was granted


def test_a_room_reaches_the_worker_env_as_read_only_mounts(tmp_path):
    root = tmp_path / "ws"
    for s in ("u_owner", "u_bob"):
        _seed(root, s)
    settings = _settings(tmp_path, root)
    d = Dispatcher(settings, _FakeRuntime(), _FakeIdentity())
    d.dispatch(_inv(), room={"meeting_id": "42", "subjects": ["u_bob"]})
    mounts = json.loads(d._runtime.spawned[-1][2]["VEXA_MOUNTS"])
    room = [m for m in mounts if m["role"] == "room"]
    assert [m["slug"] for m in room] == ["room:u_bob"]
    assert room[0]["write"] is False
    # the worker's cwd still follows the SUBJECT's own primary — a room never becomes the home
    assert d._runtime.spawned[-1][2]["VEXA_WORKSPACE_PATH"] == str(root / "u_owner")


# ── the HTTP surface: who may ask for a room at all ───────────────────────────────────────────────

class _FakeReader:
    def read(self, unit_id, *, resume=None):
        yield {"type": "message-delta", "text": "ok"}
        yield {"type": "turn-complete"}


def _client(tmp_path, rows: dict, *, secret=INTERNAL_SECRET):
    """rows = {(user_id, meeting_id): row}. Mirrors _http_meeting_owner_lookup's contract:
    a row the caller may not read comes back as None (meeting-api 404s it)."""
    root = tmp_path / "ws"
    for s in ("u_owner", "u_bob", "u_carol", "u_mallory"):
        if not (root / s).exists():
            _seed(root, s)
    settings = load_settings(workspaces_dir=str(root),
                             global_system_workspace_path=str(_git_repo(tmp_path / "g", "G")),
                             internal_api_secret=secret)
    runtime = _FakeRuntime()
    app = create_app(
        Dispatcher(settings, runtime, _FakeIdentity()), stream_reader=_FakeReader(),
        meeting_owner_lookup=lambda uid, mid: rows.get((str(uid), str(mid))),
    )
    return TestClient(app), runtime


def _mounts_of(runtime) -> list[dict]:
    return json.loads(runtime.spawned[-1][2]["VEXA_MOUNTS"])


@pytest.fixture(autouse=True)
def _creds(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")


def test_an_end_user_cannot_open_a_room_without_the_internal_secret(tmp_path):
    """The room is a flows/operator capability. A signed-in browser client reaches /api/chat through
    the gateway and holds no internal secret, so it cannot open a room AT ALL — the smallest blast
    radius available without editing core/flows."""
    c, runtime = _client(tmp_path, {("u_owner", "42"): _row("u_owner", viewers=["u_bob"])})
    r = c.post("/api/chat", json={"prompt": "hi", "session": "m42", "room_meeting_id": "42"},
               headers={"X-User-Id": "u_owner"})
    assert r.status_code == 403 and "internal" in r.json()["detail"]
    assert runtime.spawned == []          # refused BEFORE anything ran


def test_an_unconfigured_internal_secret_means_nobody_gets_a_room(tmp_path):
    c, _ = _client(tmp_path, {("u_owner", "42"): _row("u_owner", viewers=["u_bob"])}, secret="")
    r = c.post("/api/chat", json={"prompt": "hi", "session": "m42", "room_meeting_id": "42"},
               headers={"X-User-Id": "u_owner", "X-Internal-Secret": ""})
    assert r.status_code == 403


def test_the_internal_caller_gets_the_meetings_room(tmp_path):
    c, runtime = _client(tmp_path, {("u_owner", "42"): _row("u_owner", viewers=["u_bob", "u_carol"])})
    r = c.post("/api/chat", json={"prompt": "write it up", "session": "m42", "room_meeting_id": "42"},
               headers={"X-User-Id": "u_owner", "X-Internal-Secret": INTERNAL_SECRET})
    assert r.status_code == 200
    rooms = [m for m in _mounts_of(runtime) if m["role"] == "room"]
    assert [m["slug"] for m in rooms] == ["room:u_bob", "room:u_carol"]
    assert all(m["write"] is False for m in rooms)


def test_a_caller_cannot_mount_a_workspace_by_asserting_it(tmp_path):
    """THE constraint. u_mallory was never in meeting 42; naming them in the proposal mounts nothing,
    even from an internal caller who legitimately owns the meeting."""
    c, runtime = _client(tmp_path, {("u_owner", "42"): _row("u_owner", viewers=["u_bob"])})
    r = c.post("/api/chat", json={"prompt": "go", "session": "m42", "room_meeting_id": "42",
                                 "room_subjects": ["u_mallory", "u_bob"]},
               headers={"X-User-Id": "u_owner", "X-Internal-Secret": INTERNAL_SECRET})
    assert r.status_code == 200
    rooms = [m for m in _mounts_of(runtime) if m["role"] == "room"]
    assert [m["slug"] for m in rooms] == ["room:u_bob"]


def test_a_subject_not_in_the_meeting_is_not_mounted_even_with_no_proposal(tmp_path):
    c, runtime = _client(tmp_path, {("u_owner", "42"): _row("u_owner", viewers=["u_bob"])})
    c.post("/api/chat", json={"prompt": "go", "session": "m42", "room_meeting_id": "42"},
           headers={"X-User-Id": "u_owner", "X-Internal-Secret": INTERNAL_SECRET})
    paths = {m["path"] for m in _mounts_of(runtime)}
    assert not any(p.endswith("/u_carol") or p.endswith("/u_mallory") for p in paths)


def test_a_meeting_the_caller_does_not_own_is_refused(tmp_path):
    """The entitlement lookup returns None for a row that is not theirs → 403, nothing spawned."""
    c, runtime = _client(tmp_path, {("u_owner", "42"): _row("u_owner", viewers=["u_bob"])})
    r = c.post("/api/chat", json={"prompt": "go", "session": "m42", "room_meeting_id": "42"},
               headers={"X-User-Id": "u_mallory", "X-Internal-Secret": INTERNAL_SECRET})
    assert r.status_code == 403 and runtime.spawned == []


def test_a_chat_that_names_no_meeting_mounts_no_room(tmp_path):
    """REGRESSION: the ordinary chat stack is untouched."""
    c, runtime = _client(tmp_path, {})
    r = c.post("/api/chat", json={"prompt": "hello", "session": "main"},
               headers={"X-User-Id": "u_owner"})
    assert r.status_code == 200
    assert [m["role"] for m in _mounts_of(runtime)] == ["global", "private", "system"]


def test_the_body_still_refuses_an_unknown_field(tmp_path):
    """extra=forbid holds: there is no back door next to the room field."""
    c, _ = _client(tmp_path, {})
    r = c.post("/api/chat", json={"prompt": "hi", "mount_workspaces": ["u_bob"]},
               headers={"X-User-Id": "u_owner"})
    assert r.status_code == 422
