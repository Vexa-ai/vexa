"""The model's tools run as a user of their own, so they cannot read the worker through /proc.

Where the worker runs as root and the image has the tools user, every harness CLI starts as that user
(no other groups, group-writable files), and the worker hands it — by group, owners untouched — only
the workspaces a turn may write and the harness's own state. A Codex credential the tools user may
not be able to read is copied into a home of its own. A path that cannot be handed over refuses the
turn with a typed fault; the worker never stops switching, so the model's tools never run as root. A
worker that is not root never tries, and makes itself non-dumpable.
The live proof (a root worker, the real image, the tools user's Bash refused the worker's /proc
environment) runs in the worker image on a host with Docker.
"""
from __future__ import annotations

import os
import stat
import subprocess
from collections import namedtuple

import pytest

from llm import claude_code, codex, ports

PwEntry = namedtuple("PwEntry", "pw_uid pw_gid")


@pytest.fixture
def as_root(monkeypatch):
    """This process looks like a root worker whose image has the tools user — mapped onto the
    test's own uid/gid, so the ownership calls really run."""
    monkeypatch.setattr(ports.os, "geteuid", lambda: 0)
    monkeypatch.setattr(ports.pwd, "getpwnam", lambda name: PwEntry(os.getuid(), os.getgid()))
    return os.getuid(), os.getgid()


def test_a_worker_that_is_not_root_does_not_switch(monkeypatch):
    monkeypatch.setattr(ports.os, "geteuid", lambda: 1000)
    assert ports.tools_identity() is None and ports.harness_identity_kwargs() == {}


def test_an_image_without_the_tools_user_does_not_switch(monkeypatch):
    monkeypatch.setattr(ports.os, "geteuid", lambda: 0)

    def missing(name):
        raise KeyError(name)

    monkeypatch.setattr(ports.pwd, "getpwnam", missing)
    assert ports.harness_identity_kwargs() == {}


def test_a_root_worker_starts_harnesses_as_the_tools_user(as_root):
    uid, gid = as_root
    assert ports.harness_identity_kwargs() == {"user": uid, "group": gid, "extra_groups": [], "umask": 0o002}


@pytest.mark.parametrize("runner", ["_exec_subprocess", "_exec_subprocess_stdin"])
def test_the_claude_cli_starts_as_the_tools_user(as_root, monkeypatch, runner):
    seen = {}

    class Proc:
        def __init__(self, argv, **kw):
            seen.update(kw)
            self.stdout = iter(['{"type":"result"}\n'])
            self.stdin = self

        def write(self, _):
            pass

        def flush(self):
            pass

        def close(self):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(claude_code.subprocess, "Popen", Proc)
    args = (["claude"], "/tmp") + (("hi",) if runner.endswith("stdin") else ())
    list(getattr(claude_code, runner)(*args))
    assert seen["user"] == as_root[0] and seen["group"] == as_root[1] and seen["extra_groups"] == []


def test_codex_starts_as_the_tools_user_with_a_credential_it_can_read(as_root, monkeypatch, tmp_path):
    home = tmp_path / ".codex"
    (home / "sessions-target").mkdir(parents=True)
    (home / "auth.json").write_text('{"token": "x"}')
    (home / "sessions").symlink_to(home / "sessions-target", target_is_directory=True)
    monkeypatch.setenv("CODEX_HOME", str(home))
    seen = {}
    harness = codex.CodexHarness(process_factory=lambda argv, **kw: seen.update(kw) or object())
    harness._spawn(tmp_path)
    own = tmp_path / ".codex-tools"
    assert seen["user"] == as_root[0] and seen["env"]["CODEX_HOME"] == str(own)
    assert (own / "auth.json").read_text() == '{"token": "x"}'
    assert stat.S_IMODE((own / "auth.json").stat().st_mode) == 0o600
    assert os.readlink(own / "sessions") == str(home / "sessions-target")


def test_the_grant_gives_the_group_what_a_turn_writes(as_root, tmp_path):
    ws = tmp_path / "ws"
    (ws / "notes").mkdir(parents=True)
    (ws / "notes" / "a.md").write_text("x")
    os.chmod(ws / "notes" / "a.md", 0o644)
    os.chmod(ws / "notes", 0o755)
    (ws / "link").symlink_to("/etc/hostname")
    assert ports.grant_tools_access([ws]) is True
    assert stat.S_IMODE((ws / "notes" / "a.md").stat().st_mode) == 0o664
    d = (ws / "notes").stat().st_mode
    assert d & stat.S_ISGID and d & stat.S_IWGRP and d & stat.S_IXGRP
    assert os.lstat(ws / "link").st_mode & stat.S_IFLNK          # a link is left alone


def _refuse_chmod(monkeypatch):
    def refuse(*a):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(ports.os, "chmod", refuse)
    monkeypatch.setattr(ports.os, "fchmod", refuse)


def test_a_path_that_cannot_be_handed_over_is_named_and_never_stops_the_switch(as_root, monkeypatch,
                                                                               tmp_path):
    """R6-1: a grant that failed used to switch the worker off for the rest of its life — every later
    harness ran the model's tools as root. Now the grant raises, naming the path and the cause, and
    the worker still starts the next harness as the tools user."""
    uid, gid = as_root
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "f").write_text("x")
    os.chmod(ws / "f", 0o600)
    _refuse_chmod(monkeypatch)
    with pytest.raises(ports.ToolsAccessRefused) as caught:
        ports.grant_tools_access([ws])
    assert caught.value.root == str(ws) and caught.value.cause == "Operation not permitted"
    assert ports.tools_identity() == (uid, gid)
    assert ports.harness_identity_kwargs() == {"user": uid, "group": gid, "extra_groups": [], "umask": 0o002}


def test_an_entry_swapped_for_a_link_after_the_walk_is_never_changed_through_it(as_root, monkeypatch,
                                                                                tmp_path):
    """The grant runs as root while a process the tools user left behind may still be running. An
    entry looked at, then swapped for a link before its mode is changed, must not have the change
    made to whatever the link points at (``chmod`` by name follows the link)."""
    victim = tmp_path / "victim"
    victim.write_text("theirs")
    os.chmod(victim, 0o600)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "f").write_text("x")
    os.chmod(ws / "f", 0o600)
    swapped = []

    def swap_after(real):
        def look(path, *a, **kw):
            out = real(path, *a, **kw)
            if not swapped and str(path).endswith("f") and not str(path).endswith("victim"):
                swapped.append(path)
                os.rename(ws / "f", ws / "f-real")
                os.symlink(victim, ws / "f")
            return out
        return look

    monkeypatch.setattr(ports.os, "stat", swap_after(os.stat))
    monkeypatch.setattr(ports.os, "lstat", swap_after(os.lstat))
    ports.grant_tools_access([ws])
    monkeypatch.undo()
    assert swapped
    assert stat.S_IMODE(victim.stat().st_mode) == 0o600          # never changed through the link


def test_a_turn_whose_grant_fails_is_refused_with_a_typed_fault_not_run_as_root(as_root, monkeypatch,
                                                                                tmp_path):
    import json

    from worker import engine
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("VEXA_MOUNTS", json.dumps([]))
    monkeypatch.setattr(engine, "_ensure_repo", lambda w: None)
    monkeypatch.setattr(engine, "report_friction", lambda rec, **kw: None)
    ran = []

    def run(*a, **kw):
        ran.append(ports.harness_identity_kwargs())
        return iter([{"type": "done", "ok": True, "reply": "ran"}])

    monkeypatch.setattr(engine, "run_harness_turn", run)

    class Harness:
        name = "fake"

        def prepare(self, work, chat_root=None):
            pass

        def transcript_bytes(self, work, sid):
            return 0

    work = tmp_path / "desk"
    work.mkdir()
    (work / "notes.md").write_text("x")
    os.chmod(work / "notes.md", 0o600)
    _refuse_chmod(monkeypatch)
    evs = list(engine.run_turn_over_workspace(work, "hi", harness=Harness(), session_continuity=False))
    assert ran == []                                         # the harness never started
    assert len(evs) == 1 and evs[0]["type"] == "done" and evs[0]["ok"] is False
    fault = evs[0]["fault"]
    assert fault["source"] == "agent-worker" and fault["kind"] == "tools_unconfined"
    assert "Operation not permitted" in fault["detail"] and fault["remedy"]
    monkeypatch.undo()                                       # the cause is fixed: the next turn runs,
    monkeypatch.setattr(ports.os, "geteuid", lambda: 0)      # still as the tools user
    monkeypatch.setattr(ports.pwd, "getpwnam", lambda name: PwEntry(os.getuid(), os.getgid()))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("VEXA_MOUNTS", json.dumps([]))
    monkeypatch.setattr(engine, "_ensure_repo", lambda w: None)
    monkeypatch.setattr(engine, "report_friction", lambda rec, **kw: None)
    monkeypatch.setattr(engine, "run_harness_turn", run)
    evs = list(engine.run_turn_over_workspace(work, "hi", harness=Harness(), session_continuity=False))
    assert ran and ran[0].get("user") == os.getuid() and evs[-1]["reply"] == "ran"


def test_the_mcp_attachment_is_handed_to_the_tools_user(as_root, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(ports.os, "chown",
                        lambda p, uid, gid, **kw: calls.append((str(p), uid, gid, kw)))
    ports.hand_to_tools(tmp_path / "mcp.json")
    # the credential file is chowned WITHOUT following a link raced in at its name
    assert calls == [(str(tmp_path / "mcp.json"), *as_root, {"follow_symlinks": False})]


def test_the_worker_makes_itself_non_dumpable(monkeypatch):
    calls = []

    class Libc:
        def prctl(self, *args):
            calls.append(args)
            return 0

    monkeypatch.setattr(ports.ctypes, "CDLL", lambda *a, **k: Libc())
    ports.harden_worker_process()
    assert calls == [(4, 0, 0, 0, 0)]            # PR_SET_DUMPABLE, 0


def test_hardening_never_raises_where_there_is_no_prctl(monkeypatch):
    monkeypatch.setattr(ports.ctypes, "CDLL", lambda *a, **k: object())
    ports.harden_worker_process()


def test_what_the_harness_only_reads_is_shown_read_only(as_root, tmp_path):
    """The staged skills and the CLI's user scope: the tools group may read them, never write them
    — written, they would decide what a later turn loads."""
    stage = tmp_path / ".vexa-skills" / "turn-x"
    (stage / "mine").mkdir(parents=True)
    (stage / "mine" / "SKILL.md").write_text("---\nname: mine\n---\n")
    os.chmod(stage, 0o2770)                                     # what an earlier grant left
    os.chmod(stage / "mine" / "SKILL.md", 0o664)
    (stage / "shipped").symlink_to("/etc", target_is_directory=True)
    user_scope = tmp_path / ".claude"
    user_scope.mkdir()
    os.chmod(user_scope, 0o2775)
    (user_scope / "skills").symlink_to(stage, target_is_directory=True)
    assert ports.show_tools([tmp_path / ".vexa-skills", user_scope]) is True
    for d in (stage, stage / "mine", user_scope):
        mode = d.stat().st_mode
        assert mode & stat.S_IRGRP and mode & stat.S_IXGRP
        assert not mode & (stat.S_IWGRP | stat.S_IWOTH | stat.S_ISGID)
    f = (stage / "mine" / "SKILL.md").stat().st_mode
    assert f & stat.S_IRGRP and not f & (stat.S_IWGRP | stat.S_IWOTH)
    assert os.lstat(stage / "shipped").st_mode & stat.S_IFLNK      # a platform skill link is left be


def test_a_turn_grants_its_workspaces_and_only_shows_the_skills(monkeypatch, tmp_path):
    import json

    from worker import engine
    granted, shown = [], []
    monkeypatch.setattr(engine, "grant_tools_access", lambda paths: granted.extend(map(str, paths)) or True)
    monkeypatch.setattr(engine, "show_tools", lambda paths: shown.extend(map(str, paths)) or True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("VEXA_MOUNTS", json.dumps([]))

    class Harness:
        name = "fake"

        def prepare(self, work, chat_root=None):
            pass

        def transcript_bytes(self, work, sid):
            return 0

    monkeypatch.setattr(engine, "_ensure_repo", lambda w: None)
    monkeypatch.setattr(engine, "run_harness_turn", lambda *a, **kw: iter([{"type": "done", "ok": True}]))
    monkeypatch.setattr(engine, "report_friction", lambda rec, **kw: None)
    work = tmp_path / "ws"
    work.mkdir()
    list(engine.run_turn_over_workspace(work, "hi", harness=Harness(), session_continuity=False))
    home = tmp_path / "home"
    assert str(work) in granted and str(home / ".codex") in granted
    assert str(home / ".claude") not in granted and str(home / ".vexa-skills") not in granted
    assert shown == [str(home / ".claude"), str(home / ".vexa-skills")]
