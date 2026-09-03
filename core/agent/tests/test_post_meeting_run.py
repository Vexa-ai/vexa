"""THE POST-MEETING RUN WRITES ONE ARTEFACT AND NOTHING ELSE (decision 22).

Two findings of 2026-09-03's rehearsal run, both of them the same shape — a general rule that is
right everywhere else meeting a run whose contract is narrower, with nothing in between:

  F103  the entity WRITE-BACK PHASE (decision 24: "the agent writes entities as a phase of every
        turn") authored pages into the organiser's own desk after the post-meeting turn. HEAD
        moved, `process_meeting`'s decision-22 detector failed the meeting — loudly and correctly
        — and the minutes mail went nowhere. BOTH rehearsal states that reach a completed meeting
        (`group-member`, `reply-pending`) died there, which is what made it look like two bugs.
        Two things let it happen and both are closed here: on a `#group:` meeting the organiser's
        desk was mounted writable beside the group's, and on every room run the phase ran at all.

  F104  the same turn called `bot_stop` FOUR TIMES against a meeting that was over. The verbs were
        in its toolbelt, and the answer they got (`{"stopped": false, "status": 404}`) reads as a
        transient failure. Both halves are fixed, independently: the belt no longer carries the
        bot verbs on a room run, and `bot_stop` answers the state for every other caller.

No network, no docker: the mount stack and the worker env are pure functions of the dispatch.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from control_plane.dispatch import build_mount_set, build_unit_env
from shared.config import load_settings

SECRET = "test-delegation-secret"

INV = {
    "identity": {"subject": "58", "launcher": "user:58"},
    "runner": "claude-code",
    "workspaces": [{"id": "58", "mode": "rw"}],
    "trigger": "message",
    "context": {"kind": "none"},
    "start": {"entrypoint": {"inline": "hi"}},
}


def _git_repo(d: Path, marker: str = "X") -> Path:
    d.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(["git", *a], cwd=d, check=True, capture_output=True)
    run("init", "-q", "-b", "main"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
    (d / "CLAUDE.md").write_text(marker); run("add", "-A"); run("commit", "-q", "-m", "seed")
    return d


def _seed(root: Path, subject: str) -> Path:
    from shared.seeding import seed_workspace
    ws = root / subject
    ws.mkdir(parents=True)
    (ws / "CLAUDE.md").write_text(f"SEED {subject}")
    seed_workspace(ws, None)
    return ws


def _shared_ws(root: Path, ws_id: str, members: list[tuple[str, str]]) -> Path:
    ws = root / ws_id
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "README.md").write_text(f"group {ws_id}")
    pol = ws / "policy"
    pol.mkdir(parents=True, exist_ok=True)
    (pol / "members.json").write_text(json.dumps(
        [{"subject": s, "role": r} for s, r in members]))
    return ws


def _settings(tmp_path, root: Path, **over):
    return load_settings(**{"workspaces_dir": str(root),
                            "global_system_workspace_path": str(_git_repo(tmp_path / "global")),
                            "internal_api_secret": "s3cr3t-internal",
                            "mcp_url": "https://rig.example/mcp",
                            "mcp_delegation_secret": SECRET, **over})


def _room(meeting_id="42", desks=(), *, group=""):
    book = dict(desks)
    return {"meeting_id": meeting_id, "source": "invite:participants→admin-api",
            "ordered": [(a, "unmatched-invite-order") for a, _ in desks],
            "lookup": book.get, "read_max": None, "group_workspace_id": group}


# ══ F103 · the mount set — one writable desk on a group meeting ══════════════════════════════
def test_a_group_meetings_run_has_exactly_one_writable_desk_and_it_is_the_groups(tmp_path):
    """The organiser's own desk is one of the room's desks, and the room is read-only. It kept a
    write bit because F59 needed a writable CWD — but on a group meeting the CWD is the GROUP's
    desk, so the grant bought nothing and cost the detector."""
    root = tmp_path / "ws"
    for s in ("58", "u_bob"):
        _seed(root, s)
    _shared_ws(root, "g_acme", [("58", "contributor")])
    stack = build_mount_set(_settings(tmp_path, root), "58",
                            room=_room(desks=[("b@x.test", "u_bob")], group="g_acme"))

    desks = [m for m in stack if m["role"] not in ("global", "system")]
    assert [m["slug"] for m in desks if m["write"]] == ["g_acme"]
    own = next(m for m in stack if m["role"] == "private")
    assert own["write"] is False and own["primary"] is False


def test_without_a_group_the_organisers_desk_still_keeps_the_write_bit_it_needs(tmp_path):
    """The other half of F59, and the reason the narrowing above is conditional: with no group
    desk the subject's own desk IS the cwd, and a read-only cwd killed the worker before the model
    ran (`OSError: [Errno 30] Read-only file system: .../.claude`)."""
    root = tmp_path / "ws"
    for s in ("58", "u_bob"):
        _seed(root, s)
    stack = build_mount_set(_settings(tmp_path, root), "58",
                            room=_room(desks=[("b@x.test", "u_bob")]))

    own = next(m for m in stack if m["role"] == "private")
    assert own["write"] is True and own["primary"] is True


def test_a_group_the_subject_may_only_read_never_costs_them_their_cwd(tmp_path):
    """The narrowing turns on a WRITABLE group desk, not on the meeting merely naming one. A
    viewer's group desk is read-only, so demoting the organiser's own desk beside it would leave
    the run with no writable mount at all — F59 through a quieter door."""
    root = tmp_path / "ws"
    _seed(root, "58")
    _shared_ws(root, "g_acme", [("u_somebody_else", "owner"), ("58", "viewer")])
    stack = build_mount_set(_settings(tmp_path, root), "58", room=_room(group="g_acme"))

    own = next(m for m in stack if m["role"] == "private")
    assert own["write"] is True and own["primary"] is True
    group = next((m for m in stack if m["slug"] == "g_acme"), None)
    assert group is None or group["write"] is False


# ══ F103/F104 · the worker is TOLD, positively, that this is a room run ══════════════════════
def test_the_dispatch_stamps_the_room_meeting_and_only_on_a_room_run(tmp_path):
    """A POSITIVE signal, because the mount shape cannot answer this question: a room whose other
    attendees have no desks yet resolves to zero `role: "room"` mounts — the small-team case — and
    "are there room mounts?" would then answer `no` on a run that is one."""
    root = tmp_path / "ws"
    _seed(root, "58")
    settings = _settings(tmp_path, root)

    plain = build_unit_env(settings, dict(INV), unit_id="u1", token="t")
    assert "VEXA_ROOM_MEETING" not in plain

    roomed = build_unit_env(settings, dict(INV), unit_id="u1", token="t",
                            room=_room(meeting_id="97"))
    assert roomed["VEXA_ROOM_MEETING"] == "97"


def test_the_worker_reads_that_stamp_as_the_room_predicate(monkeypatch):
    from worker.engine import room_run

    monkeypatch.delenv("VEXA_ROOM_MEETING", raising=False)
    assert room_run() == ""
    monkeypatch.setenv("VEXA_ROOM_MEETING", "97")
    assert room_run() == "97"


# ══ F104 · the bot verbs leave the toolbelt when the meeting is over ═════════════════════════
def test_a_post_meeting_turn_is_offered_no_bot_verbs():
    """The meeting is OVER. `bot_stop` cannot help, and a tool that cannot help is a tool that can
    be looped on — it was called four times in one turn."""
    from worker.engine import VEXA_MCP_SERVER, VEXA_MCP_TOOLS, room_toolbelt

    full = [f"mcp__{VEXA_MCP_SERVER}",
            *(f"mcp__{VEXA_MCP_SERVER}__{t}" for t in VEXA_MCP_TOOLS)]
    narrowed = room_toolbelt(full)

    assert f"mcp__{VEXA_MCP_SERVER}__bot_stop" in full        # the belt this narrows
    assert not [t for t in narrowed if t.startswith(f"mcp__{VEXA_MCP_SERVER}__bot")]
    # ...and nothing else moved: the transcript is the whole point of the turn
    assert f"mcp__{VEXA_MCP_SERVER}__meeting_transcript" in narrowed
    assert f"mcp__{VEXA_MCP_SERVER}" in narrowed              # the server id itself stays
    assert len(narrowed) == len(full) - len(
        [t for t in VEXA_MCP_TOOLS if t.startswith("bot")])


# ══ F103 · the two writers on the organiser's desk, both silenced while a room is open ═══════
# `process_meeting`'s detector compares the organiser's desk HEAD before and after the turn and
# fails the meeting when it moved. Two things moved it, and they fail differently — the write-back
# phase only on a turn that learned a new name, the README refresh on any turn that changed a
# section — which is why one looked intermittent and the other looked like a different bug.

DESK = {"slug": "58", "path": "/w/58", "role": "private", "write": True, "primary": True}
GROUP = {"slug": "g_acme", "path": "/w/g_acme", "role": "shared", "write": True, "primary": True}
ROOM = {"slug": "room:u_bob", "path": "/w/u_bob", "role": "room", "write": False}
SYSTEM = {"slug": "_system", "path": "/w/58/_system", "role": "system", "write": True}


def test_the_write_back_phase_has_no_target_on_a_group_less_room_run(monkeypatch, tmp_path):
    """The subject's desk is writable on this run ON PURPOSE — the runtime needs a writable cwd to
    create `<cwd>/.claude` at all (F59) — so the write bit means "the process can start" here, not
    "the turn may author here". The phase read it as the latter."""
    from worker import engine

    monkeypatch.setenv("VEXA_ROOM_MEETING", "97")
    assert engine.writeback_candidates(["Priya Raman joined from Acme"],
                                       [DESK, ROOM, SYSTEM]) == []


def test_the_group_desk_is_still_a_write_back_target(monkeypatch):
    """Narrowed, not disabled: decision 22's group half says the run maintains the group's desk,
    so the phase keeps working there. (`missing_names` reads the workspace; an empty temp root
    simply means every name is missing, which is what makes this assert non-trivially non-empty.)"""
    from worker import engine

    monkeypatch.setenv("VEXA_ROOM_MEETING", "97")
    roots_seen = []
    monkeypatch.setattr("shared.entities.missing_names",
                        lambda roots, texts: roots_seen.extend(str(r) for r in roots) or ["Priya"])
    out = engine.writeback_candidates(["Priya Raman joined"], [DESK, GROUP, ROOM, SYSTEM])

    assert out == ["Priya"]
    assert roots_seen == ["/w/g_acme"]          # the group's, and NOT the organiser's own


def test_off_a_room_run_the_phase_targets_the_desk_exactly_as_before(monkeypatch):
    """The scoping is to room mode and nothing else — a person's own chat still writes their desk."""
    from worker import engine

    monkeypatch.delenv("VEXA_ROOM_MEETING", raising=False)
    roots_seen = []
    monkeypatch.setattr("shared.entities.missing_names",
                        lambda roots, texts: roots_seen.extend(str(r) for r in roots) or [])
    engine.writeback_candidates(["Priya Raman joined"], [DESK, SYSTEM])

    assert roots_seen == ["/w/58"]


def test_the_desk_readme_refresh_does_not_touch_the_organisers_desk_on_a_room_run(monkeypatch,
                                                                                   tmp_path):
    """The refresh runs on EVERY turn and COMMITS `README.md` when a section changed, so it moves
    HEAD without the write-back phase doing anything at all — the half that made this look
    intermittent.

    The desk is a REAL directory here on purpose: `refresh_desk_readme` returns None for a path
    that is not one, so a fake path would make this test pass against the unfixed code for a
    reason that has nothing to do with the room."""
    from worker import engine

    desk = {**DESK, "path": str(tmp_path)}
    monkeypatch.setenv("VEXA_ROOM_MEETING", "97")
    monkeypatch.setattr(engine, "desk_mounts", lambda mounts=None: (desk, []))
    looked_at = []
    monkeypatch.setattr("shared.desk_readme.update_readme",
                        lambda root, **kw: looked_at.append(str(root)) or {"changed": False})

    assert engine.refresh_desk_readme([desk, ROOM]) is None
    assert looked_at == []          # it did not even read the organiser's desk


def test_the_groups_readme_is_still_refreshed_on_a_group_meeting(monkeypatch, tmp_path):
    """Decision 22's group half names the README explicitly — "the dashboard a member reads first
    — make it true as of today". The narrowing must not take that with it."""
    from worker import engine

    monkeypatch.setenv("VEXA_ROOM_MEETING", "97")
    group = {**GROUP, "path": str(tmp_path)}
    monkeypatch.setattr(engine, "desk_mounts", lambda mounts=None: (group, []))
    seen = {}
    monkeypatch.setattr("shared.desk_readme.update_readme",
                        lambda root, **kw: seen.update(root=str(root)) or {"changed": False})

    assert engine.refresh_desk_readme([group]) == {"changed": False}
    assert seen["root"] == str(tmp_path)
