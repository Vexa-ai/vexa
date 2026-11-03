"""The post-meeting MEETING ROOM — and, since founder decision 3 (2026-10-17), what it no longer does.

DESKS ARE PERSONAL. The write-up reads the transcript, the room's workspace (the meeting's group
desk) and explicit shares; it never mounts another attendee's desk, whatever the invite or the
transcript names. Every test here is about the boundary: who may open a room, that no attendee's
desk lands in it, that the subject's own desk is read-only on a room run, that the group desk is
the one writable workspace, and that a dispatch which names no meeting is byte-identical to before.

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
from control_plane.meeting_room import RoomRefused, assert_owner
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


def _room(meeting_id="42", desks=(), *, group="", cap=None) -> dict:
    """The room shape ``api._resolve_room`` hands the dispatcher: ordered ADDRESSES + the resolver.
    ``desks`` = [(address, subject)] — the directory this room's lookup answers from."""
    book = dict(desks)
    return {"meeting_id": meeting_id, "source": "invite:participants→admin-api",
            "ordered": [(a, "unmatched-invite-order") for a, _ in desks],
            "lookup": book.get, "read_max": cap, "group_workspace_id": group}


def _row(owner: str, meeting_id="42", viewers=None, shared=False, data_extra=None) -> dict:
    """A meeting row shaped like meeting-api's GET /meetings/{id} answer to its OWNER."""
    data = {"transcript_viewers": list(viewers or [])}
    data.update(data_extra or {})
    return {"id": meeting_id, "user_id": owner, "native_meeting_id": "abc-def",
            "shared": shared, "data": data}


# ── gate 3: ownership ────────────────────────────────────────────────────────────────────────────

def test_an_unentitled_caller_is_refused():
    """``None`` from the entitlement lookup (absent row / another tenant / meeting-api down)."""
    with pytest.raises(RoomRefused, match="not authorized"):
        assert_owner(None, requester="u_mallory")


def test_a_share_recipient_may_not_open_a_room():
    """They pass the meeting ACCESS check (they can read the transcript) but a recipient opening a
    room would turn one share into a read of every attendee's desk."""
    with pytest.raises(RoomRefused, match="owner"):
        assert_owner(_row("u_owner", shared=True), requester="u_bob")


def test_ownership_needs_positive_evidence():
    """An absent/blank user_id refuses rather than defaulting to 'probably theirs'."""
    row = _row("u_owner"); row["user_id"] = ""
    with pytest.raises(RoomRefused, match="owner"):
        assert_owner(row, requester="u_owner")


def test_the_owner_passes():
    assert assert_owner(_row("u_owner"), requester="u_owner") is None


# ── decision 3: the room module has no desk-mounting door left ───────────────────────────────────

def test_the_room_module_has_no_way_to_mount_an_attendees_desk():
    """`room_mounts`, `resolve_desks` and `order_participants` are gone: nothing in the room turns
    an address, a name or a subject into another person's desk."""
    for gone in ("room_mounts", "resolve_desks", "order_participants", "ROOM_ROLE"):
        assert not hasattr(meeting_room, gone), gone


# ── composition into the mount STACK ─────────────────────────────────────────────────────────────

def test_the_stack_is_unchanged_when_no_room_is_named(tmp_path):
    """REGRESSION: every dispatch that names no meeting must be byte-identical to before."""
    root = tmp_path / "ws"; _seed(root, "u_owner")
    settings = _settings(tmp_path, root)
    assert build_mount_set(settings, "u_owner") == build_mount_set(settings, "u_owner", room=None)
    assert [m["role"] for m in build_mount_set(settings, "u_owner")] == ["global", "private", "system"]


def test_a_room_mounts_no_attendees_desk(tmp_path):
    """Decision 3: naming attendees who all have desks adds none of them to the stack."""
    root = tmp_path / "ws"
    for s in ("u_owner", "u_bob", "u_carol"):
        _seed(root, s)
    settings = _settings(tmp_path, root)
    stack = build_mount_set(settings, "u_owner",
                            room=_room(desks=[("b@x.test", "u_bob"), ("c@x.test", "u_carol")]))
    assert [m["role"] for m in stack] == ["global", "private", "system"]
    assert not any(str(root / "u_bob") == m["path"] or str(root / "u_carol") == m["path"]
                   for m in stack)
    # the subject's own desk is read-only on a room run (Vexa-ai/vexa#1606, decision 22)
    assert stack[1]["role"] == "private" and stack[1]["write"] is False
    assert stack[-1]["write"] is True          # _system is not a desk: chat continuity anchors there


def test_the_runtime_binds_a_room_mount_read_only(tmp_path):
    """The write flag is not decoration: the shared runtime mount plumbing turns it into a :ro bind,
    so read-only is enforced by the container's mount table rather than by asking the model nicely."""
    # IMPORT THE MODULE, NOT THE PACKAGE (F92). `from runtime_kernel.mounts import …` executes
    # `runtime_kernel/__init__.py`, which eagerly imports all three backends — including
    # `docker_backend`, whose `requests_unixsocket` is a RUNTIME-image dependency the agent test env
    # has no reason to hold. That import is why this test has been the suite's one standing red, and
    # the red says nothing about mounts: `mounts.py` itself imports only the stdlib. Load it by path
    # so the assertion runs against the real shared plumbing without dragging the package in.
    import importlib.util
    import sys

    mounts_py = Path(__file__).resolve().parents[2] / "runtime" / "src" / "runtime_kernel" / "mounts.py"
    name = "runtime_kernel_mounts_under_test"
    spec = importlib.util.spec_from_file_location(name, mounts_py)
    assert spec and spec.loader, mounts_py
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module      # @dataclass resolves its own module through sys.modules
    spec.loader.exec_module(module)
    workspace_binds = module.workspace_binds

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
    with_room = token_scope(_room(desks=[("b@x.test", "u_bob")]))
    assert plain == with_room
    assert with_room[1] == {"regime": "human", "workspaces": "*"}   # the subject's OWN account, only
    assert with_room[2] == [{"id": "u_owner", "mode": "rw"}]        # no attendee was granted


def test_a_room_reaches_the_worker_env_with_no_attendees_desk(tmp_path):
    root = tmp_path / "ws"
    for s in ("u_owner", "u_bob"):
        _seed(root, s)
    settings = _settings(tmp_path, root)
    d = Dispatcher(settings, _FakeRuntime(), _FakeIdentity())
    d.dispatch(_inv(), room=_room(desks=[("b@x.test", "u_bob")]))
    mounts = json.loads(d._runtime.spawned[-1][2]["VEXA_MOUNTS"])
    assert not any(m["role"] == "room" for m in mounts)
    assert not any(m["path"] == str(root / "u_bob") for m in mounts)
    # with every desk read-only on a room run the cwd is the `_system` tier, writable by contract
    cwd = d._runtime.spawned[-1][2]["VEXA_WORKSPACE_PATH"]
    at_cwd = next(m for m in mounts if m["path"] == cwd)
    assert at_cwd["role"] == "system" and at_cwd["write"] is True


# ── the HTTP surface: who may ask for a room at all ───────────────────────────────────────────────

class _FakeReader:
    def read(self, unit_id, *, resume=None, fresh=False):
        yield {"type": "message-delta", "text": "ok"}
        yield {"type": "turn-complete"}


# The platform directory the room resolves participant ADDRESSES against. In production this is
# admin-api; here it is a dict, injected through the same seam.
DIRECTORY = {"bob@acme.test": "u_bob", "carol@acme.test": "u_carol",
             "mallory@evil.test": "u_mallory", "owner@acme.test": "u_owner",
             "dave@acme.test": "u_dave_no_desk"}


def _client(tmp_path, rows: dict, *, secret=INTERNAL_SECRET, directory=None):
    """rows = {(user_id, meeting_id): row}. Mirrors _http_meeting_owner_lookup's contract:
    a row the caller may not read comes back as None (meeting-api 404s it)."""
    root = tmp_path / "ws"
    for sub in ("u_owner", "u_bob", "u_carol", "u_mallory"):
        if not (root / sub).exists():
            _seed(root, sub)
    settings = load_settings(workspaces_dir=str(root),
                             global_system_workspace_path=str(_git_repo(tmp_path / "g", "G")),
                             internal_api_secret=secret)
    runtime = _FakeRuntime()
    book = DIRECTORY if directory is None else directory
    app = create_app(
        Dispatcher(settings, runtime, _FakeIdentity()), stream_reader=_FakeReader(),
        meeting_owner_lookup=lambda uid, mid, workspaces=None: rows.get((str(uid), str(mid))),
        email_subject_lookup=lambda address: book.get(address),
    )
    return TestClient(app), runtime


def _mounts_of(runtime) -> list[dict]:
    return json.loads(runtime.spawned[-1][2]["VEXA_MOUNTS"])


@pytest.fixture(autouse=True)
def _creds(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")


def test_an_end_user_cannot_open_a_room_without_the_internal_secret(tmp_path):
    """The room is a flows/operator capability. A signed-in browser client reaches /api/chat through
    the gateway and holds no internal secret, so it cannot open a room AT ALL. Under the participant
    model this gate is ALSO the trust boundary on who is in the room, which makes it load-bearing
    twice over."""
    c, runtime = _client(tmp_path, {("u_owner", "42"): _row("u_owner")})
    r = c.post("/api/chat", json={"prompt": "hi", "session": "m42", "room_meeting_id": "42",
                                  "room_participants": ["bob@acme.test"]},
               headers={"X-User-Id": "u_owner"})
    assert r.status_code == 403 and "internal" in r.json()["detail"]
    assert runtime.spawned == []          # refused BEFORE anything ran


def test_an_unconfigured_internal_secret_means_nobody_gets_a_room(tmp_path):
    c, _ = _client(tmp_path, {("u_owner", "42"): _row("u_owner")}, secret="")
    r = c.post("/api/chat", json={"prompt": "hi", "session": "m42", "room_meeting_id": "42"},
               headers={"X-User-Id": "u_owner", "X-Internal-Secret": ""})
    assert r.status_code == 403


def test_the_invites_participants_never_become_desks(tmp_path):
    """Decision 3: flows still sends the invite's participants, names and speakers; none of them
    mounts anything, and no attendee's workspace is created."""
    c, runtime = _client(tmp_path, {("u_owner", "42"): _row("u_owner")})
    r = c.post("/api/chat", json={
        "prompt": "write it up", "session": "m42", "room_meeting_id": "42",
        "room_participants": ["bob@acme.test", "carol@acme.test", "dave@acme.test"],
        "room_participant_names": {"bob@acme.test": "Bob Stone"},
        "room_speakers": ["Bob Stone"]},
        headers={"X-User-Id": "u_owner", "X-Internal-Secret": INTERNAL_SECRET})
    assert r.status_code == 200
    mounts = _mounts_of(runtime)
    assert [m["role"] for m in mounts] == ["global", "private", "system"]
    assert not any(m["path"].endswith(("/u_bob", "/u_carol")) for m in mounts)
    assert not (tmp_path / "ws" / "u_dave_no_desk").exists()


def test_no_resolver_configured_means_zero_desks_never_a_guess(tmp_path):
    c, runtime = _client(tmp_path, {("u_owner", "42"): _row("u_owner")}, directory={})
    r = c.post("/api/chat", json={
        "prompt": "go", "session": "m42", "room_meeting_id": "42",
        "room_participants": ["bob@acme.test"], "room_speakers": ["Bob Stone"]},
        headers={"X-User-Id": "u_owner", "X-Internal-Secret": INTERNAL_SECRET})
    assert r.status_code == 200          # the turn still happens
    assert [m["role"] for m in _mounts_of(runtime)] == ["global", "private", "system"]


def test_a_meeting_the_caller_does_not_own_is_refused(tmp_path):
    """The entitlement lookup returns None for a row that is not theirs → 403, nothing spawned."""
    c, runtime = _client(tmp_path, {("u_owner", "42"): _row("u_owner")})
    r = c.post("/api/chat", json={"prompt": "go", "session": "m42", "room_meeting_id": "42",
                                  "room_participants": ["bob@acme.test"]},
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
    """``extra=forbid`` holds: there is no back door next to the room fields."""
    c, _ = _client(tmp_path, {})
    r = c.post("/api/chat", json={"prompt": "hi", "mount_workspaces": ["u_bob"]},
               headers={"X-User-Id": "u_owner"})
    assert r.status_code == 422


# ── DECISION 22: a room run writes NO desk — except the meeting's group desk ──────────────────────

def _shared_ws(root: Path, ws_id: str, members: list[tuple[str, str]]) -> Path:
    """A materialized SHARED workspace at <root>/<ws_id> with an authoritative policy/members.json."""
    from control_plane import workspace_membership as membership
    ws = root / ws_id
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "README.md").write_text(f"group {ws_id}")
    pol = ws / membership.POLICY_DIR if hasattr(membership, "POLICY_DIR") else ws / "policy"
    pol.mkdir(parents=True, exist_ok=True)
    (pol / "members.json").write_text(json.dumps(
        [{"subject": s, "role": r} for s, r in members]))
    return ws


def test_the_room_and_the_subjects_own_desk_are_both_read_only(tmp_path):
    """Founder decision 22: the run reads desks and writes ONE shared artefact whose home is the
    meeting row; flows distributes it afterwards.

    ⚠ THIS TEST HAS NOW BEEN WRITTEN BOTH WAYS, AND BOTH TIMES THE ASSERTION WAS THE POLICY. It
    first said the subject\'s own desk is read-only, which broke every non-admin post-meeting spawn
    (`OSError: [Errno 30] Read-only file system` on `<cwd>/.claude`, F59); it was then inverted to
    say the desk is writable, and two end-of-turn writers took that write bit as permission — the
    entity write-back phase on meeting 147, the desk README refresh on meeting 150
    (Vexa-ai/vexa#1606). Both versions were arguing about the WRONG PROPERTY. F59 needs a writable
    CWD, not a writable DESK, and those stopped being the same thing when `_worker_cwd` learned to
    fall back to `_system`. So: every desk read-only, and the cwd asserted writable in its own
    right."""
    root = tmp_path / "ws"
    for s in ("u_owner", "u_bob"):
        _seed(root, s)
    settings = _settings(tmp_path, root)
    stack = build_mount_set(settings, "u_owner", room=_room(desks=[("b@x.test", "u_bob")]))
    assert not any(m["role"] == "room" for m in stack), "decision 3: no attendee's desk"
    own = next(m for m in stack if m["role"] == "private")
    assert own["write"] is False, "decision 22 is a mount mode now, not a check after the fact"
    assert own["primary"] is False
    from control_plane.dispatch import _worker_cwd
    cwd = _worker_cwd(str(root), "u_owner", stack)
    assert next(m for m in stack if m["path"] == cwd)["write"] is True, \
        "the subject cannot start a turn on a read-only cwd (F59)"
    # _system is NOT a desk — chat continuity anchors there, so it stays read-write, and with no
    # writable desk on this run it is also where the turn lives
    assert stack[-1]["role"] == "system" and stack[-1]["write"] is True
    assert cwd == stack[-1]["path"]
    assert stack[0]["role"] == "global" and stack[0]["write"] is False


def test_a_non_admin_subjects_post_meeting_dispatch_can_start(tmp_path):
    """THE REGRESSION, end to end at the mount level (F59, 2026-09-02).

    Every post-meeting turn for every non-admin subject died on spawn, and the instance\'s only
    admin is the founder — so dogfooding could not see it. The three mount modes a room run needs,
    asserted together because it is their COMBINATION that was wrong."""
    root = tmp_path / "ws"
    for s in ("u_org", "u_bob", "u_carol"):
        _seed(root, s)
    _shared_ws(root, "g_acme", [("u_org", "contributor")])
    settings = _settings(tmp_path, root)
    stack = build_mount_set(settings, "u_org", room=_room(
        desks=[("b@x.test", "u_bob"), ("c@x.test", "u_carol")], group="g_acme"))
    by = {m["slug"]: m for m in stack}
    own = next(m for m in stack if m["role"] == "private")
    assert not any(f"room:{s}" in by for s in ("u_bob", "u_carol"))   # decision 3: no attendee desk
    assert by["g_acme"]["write"] is True                       # the group: rw when present
    assert by["g_acme"]["primary"] is True                     # ...and it is the cwd
    assert own["primary"] is False
    # F59'S ACTUAL PROPERTY, asserted as itself: the cwd the runtime is handed is WRITABLE, so the
    # worker can create `<cwd>/.claude` and the turn starts. This line used to read
    # `own["write"] is True` instead, which was a PROXY for it — and the wrong one when the meeting
    # has a group, because then the cwd is the group desk and the subject's own desk needs no write
    # bit at all. That over-grant left a second writable content desk in a run whose contract is
    # that it writes no desk; the entity write-back phase (decision 24) then authored pages into
    # the organiser's desk after the turn, moved HEAD, and tripped `process_meeting`'s own
    # decision-22 detector on every `#group:` meeting (F103). The two group-less cases — no group,
    # and a group this subject may only read — are covered two tests down, and there the own desk
    # keeps both bits.
    from control_plane.dispatch import _worker_cwd
    cwd = _worker_cwd(str(root), "u_org", stack)
    assert cwd == str(root / "g_acme")
    assert next(m for m in stack if m["path"] == cwd)["write"] is True
    assert own["write"] is False                               # it is one of the room's desks now


def test_the_same_subject_keeps_a_writable_desk_when_no_room_is_named(tmp_path):
    """REGRESSION twin of the test above: the demotion is scoped to room mode, nothing else."""
    root = tmp_path / "ws"; _seed(root, "u_owner")
    settings = _settings(tmp_path, root)
    stack = build_mount_set(settings, "u_owner")
    assert [m["write"] for m in stack] == [False, True, True]


def test_the_meetings_group_desk_is_the_one_writable_desk(tmp_path):
    root = tmp_path / "ws"
    for s in ("u_owner", "u_bob"):
        _seed(root, s)
    _shared_ws(root, "g_acme", [("u_owner", "contributor")])
    settings = _settings(tmp_path, root)
    stack = build_mount_set(settings, "u_owner",
                            room=_room(desks=[("b@x.test", "u_bob")], group="g_acme"))
    writable = [m["slug"] for m in stack
                if m["write"] and m["role"] not in ("global", "system")]
    # THE ONE WRITABLE DESK, which is what this test has always been called. It used to assert
    # `[own_slug, "g_acme"]` — two of them — under a comment excusing the first as F59's doing;
    # F59 needs a writable CWD, and here the cwd is the group desk. See the fuller note in
    # `test_a_non_admin_subjects_post_meeting_dispatch_can_start` above (F103).
    assert writable == ["g_acme"]
    group = next(m for m in stack if m["slug"] == "g_acme")
    # it becomes the turn's cwd, so the run maintains the group's memory rather than a ro desk
    assert group["primary"] is True
    from control_plane.dispatch import _worker_cwd
    assert _worker_cwd(str(root), "u_owner", stack) == str(root / "g_acme")


def test_a_non_member_gets_no_group_desk(tmp_path):
    """The bound id is not a grant: the write bit is re-read from the workspace's own member list."""
    root = tmp_path / "ws"; _seed(root, "u_owner")
    _shared_ws(root, "g_acme", [("u_somebody_else", "owner")])
    settings = _settings(tmp_path, root)
    stack = build_mount_set(settings, "u_owner", room=_room(group="g_acme"))
    assert not any(m["slug"] == "g_acme" for m in stack)
    # no group desk to work in — and the subject's own desk is read-only on a room run all the same
    # (Vexa-ai/vexa#1606), so the cwd comes from `_system` and F59 is bought there
    own = next(m for m in stack if m["role"] == "private")
    assert own["write"] is False and own["primary"] is False
    from control_plane.dispatch import _worker_cwd
    assert next(m for m in stack
                if m["path"] == _worker_cwd(str(root), "u_owner", stack))["write"] is True


def test_a_viewer_of_the_group_desk_reads_it_but_cannot_write_it(tmp_path):
    root = tmp_path / "ws"; _seed(root, "u_owner")
    _shared_ws(root, "g_acme", [("u_owner", "viewer")])
    settings = _settings(tmp_path, root)
    stack = build_mount_set(settings, "u_owner", room=_room(group="g_acme"))
    group = next((m for m in stack if m["slug"] == "g_acme"), None)
    assert group is not None and group["write"] is False
    # AND IT DOES NOT TAKE THE CWD. A read-only group desk as cwd is F59 through a quieter door:
    # the turn would again start on a mount it cannot write. Neither does the subject's own desk,
    # which is read-only on a room run too (Vexa-ai/vexa#1606) — the cwd is `_system`.
    assert group["primary"] is False
    own = next(m for m in stack if m["role"] == "private")
    assert own["primary"] is False and own["write"] is False
    from control_plane.dispatch import _worker_cwd
    cwd = _worker_cwd(str(root), "u_owner", stack)
    assert next(m for m in stack if m["path"] == cwd)["write"] is True
    assert next(m for m in stack if m["path"] == cwd)["role"] == "system"


def test_the_group_is_read_off_the_meeting_row_never_the_caller(tmp_path):
    from control_plane.meeting_room import group_workspace_id
    assert group_workspace_id(_row("u_owner", data_extra={"workspace_id": "g_acme"})) == "g_acme"
    assert group_workspace_id(_row("u_owner")) == ""
    assert group_workspace_id(_row("u_owner", data_extra={"workspace_id": True})) == ""
    assert group_workspace_id(None) == ""


def test_the_resolver_carries_the_group_from_meeting_api(tmp_path):
    c, runtime = _client(tmp_path, {("u_owner", "42"): _row("u_owner", viewers=["u_bob"],
                                                             data_extra={"workspace_id": "g_acme"})})
    r = c.post("/api/chat", json={"prompt": "go", "session": "m42", "room_meeting_id": "42"},
               headers={"X-User-Id": "u_owner", "X-Internal-Secret": INTERNAL_SECRET})
    assert r.status_code == 200
    mounts = _mounts_of(runtime)
    # no group workspace was materialized in this fixture → NO desk is writable on this run
    # (Vexa-ai/vexa#1606); the turn lives in `_system`, which is not a desk
    writable = [m["role"] for m in mounts if m["write"] and m["role"] not in ("global", "system")]
    assert writable == []
    assert any(m["role"] == "system" and m["write"] for m in mounts)


def test_prepare_survives_a_read_only_cwd(tmp_path, monkeypatch):
    """A room run's cwd is ro by construction, so PREPARE must write nothing into it: chats go to the
    private continuity root and skills to the per-subject HOME."""
    import os
    import stat
    from llm.claude_code import ClaudeCodeHarness
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    work = tmp_path / "ro-desk"
    work.mkdir()
    (work / "README.md").write_text("x")
    os.chmod(work, stat.S_IRUSR | stat.S_IXUSR)
    try:
        ClaudeCodeHarness().prepare(work, chat_root=tmp_path / "system")   # must not raise
    finally:
        os.chmod(work, stat.S_IRWXU)
