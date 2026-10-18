"""STOP ENDS THE TURN, A SHELL COMMAND HAS A LIMIT, AND THE AGENT DOES NOT READ ITS TRANSCRIPTS.

Live app.dev, 2026-10-15 (user 2, chat pchat-mv9n3xc1, turn t0 at 15:47 UTC): the model ran
`grep -o -i -h '[^"]\\{0,300\\}(pin\\b|six-digit|6-digit)[^"]\\{0,300\\}' .claude/projects/-workspaces-2/*.jsonl`
— a catastrophic pattern over megabytes of its own CLI transcripts. The CLI's 120 s Bash timeout
moved it to the background instead of killing it; the person pressed Stop, which only closed the
browser's request; the grep ran 14+ minutes, the turn never completed, its session lease was never
released and every next message was refused (409 "session conflict") until an operator killed the
pid by hand.

Each test below names the piece of that chain it closes.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from llm import claude_code, proctree
from worker import engine
from shared import units

GUARD = Path(claude_code.__file__).with_name("shell_guard.sh")


# ── helpers: processes found by a marker in their command line, and always cleaned up ──────────

def _pids_with(marker: str) -> set[int]:
    out = set()
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/cmdline", "rb") as fh:
                cmd = fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
            with open(f"/proc/{name}/stat", "rb") as fh:
                state = fh.read().decode("ascii", "replace").rsplit(")", 1)[1].split()[0]
        except OSError:
            continue
        if marker in cmd and state != "Z" and int(name) != os.getpid():
            out.add(int(name))
    return out


def _wait_for(pred, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


@pytest.fixture
def marker():
    m = f"{3000 + (os.getpid() % 900)}.{time.time_ns() % 1_000_000}"     # a unique `sleep` length
    yield m
    for p in _pids_with(f"sleep {m}"):           # never leave a stray process behind on the host
        try:
            os.kill(p, 9)
        except OSError:
            pass


def _fake_cli(tmp_path: Path, marker: str) -> list[str]:
    """A stand-in for the claude CLI whose tool is a silent long-running shell: it reads its prompt,
    prints one event, then starts the two shapes of tool process the real CLI leaves — a shell in a
    session of its own (`detached`, the CLI's Bash tool) running a pipeline, and a plain child —
    and prints nothing more, exactly like a 14-minute grep."""
    script = tmp_path / "fake-cli.sh"
    script.write_text(
        "#!/bin/bash\n"
        "read -r line\n"
        "echo '{\"type\":\"system\",\"subtype\":\"init\",\"session_id\":\"s1\"}'\n"
        f"setsid bash -lc 'sleep {marker} | cat' </dev/null >/dev/null 2>&1 &\n"
        f"bash -c 'sleep {marker}' </dev/null >/dev/null 2>&1 &\n"
        "wait\n")
    script.chmod(0o755)
    return [str(script)]


# ── (1) Stop ends the turn: the CLI and every tool process of it are killed ─────────────────────

def test_stop_kills_a_turn_whose_tool_is_a_long_silent_sleep(tmp_path, marker):
    gen = claude_code._exec_subprocess_stdin(_fake_cli(tmp_path, marker), str(tmp_path), "hi",
                                             injectable=False)
    lines: list[str] = []
    reader = threading.Thread(target=lambda: lines.extend(gen), daemon=True)
    reader.start()
    assert _wait_for(lambda: len(_pids_with(f"sleep {marker}")) >= 2), "the fake tool never started"

    t0 = time.monotonic()
    assert claude_code.stop_running_turn() is True
    reader.join(timeout=10)
    assert not reader.is_alive(), "the turn did not end after Stop"
    assert time.monotonic() - t0 < 10
    assert _wait_for(lambda: not _pids_with(f"sleep {marker}"), 5), \
        f"tool processes outlived Stop: {_pids_with(f'sleep {marker}')}"
    assert claude_code.stop_running_turn() is False      # nothing is running any more


def test_a_budget_stop_reaps_the_tools_too_not_only_the_cli(tmp_path, marker, monkeypatch):
    """`_reap` used to `proc.kill()` the CLI alone: its detached shell and the grep under it lived on."""
    monkeypatch.setenv("VEXA_HARNESS_REAP_GRACE_SEC", "0.2")
    gen = claude_code._exec_subprocess_stdin(_fake_cli(tmp_path, marker), str(tmp_path), "hi",
                                             injectable=False)
    next(gen)                                             # the init line: the turn is running
    assert _wait_for(lambda: len(_pids_with(f"sleep {marker}")) >= 2)
    gen.close()                                           # the phase budget stops reading
    assert _wait_for(lambda: not _pids_with(f"sleep {marker}"), 5), \
        f"tool processes outlived the CLI: {_pids_with(f'sleep {marker}')}"


def test_the_process_tree_never_includes_this_process():
    assert os.getpid() not in proctree.members(os.getpid())


# ── the worker: a press during the turn kills it and the turn ENDS, said ────────────────────────

class _Stream:
    def __init__(self):
        self.out, self.kv, self.entries = [], {}, []

    def xrange(self, *a, **k):
        return []

    def xrevrange(self, *a, **k):
        return []

    def xread(self, *a, **k):
        return None

    def xadd(self, topic, fields):
        self.out.append(json.loads(fields["event"]))

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v):
        self.kv[k] = v


class _Harness:
    def __init__(self):
        self.killed = threading.Event()

    def stop_running_turn(self):
        self.killed.set()                   # the real one kills the CLI; its stdout then hits EOF
        return True


def test_stop_pressed_during_a_silent_tool_ends_the_turn_and_says_so(monkeypatch):
    monkeypatch.setattr(engine, "STOP_POLL_S", 0.05)
    stream, harness = _Stream(), _Harness()

    def turn(prompt):
        yield {"type": "tool-call", "tool": "Bash", "callId": "c1"}
        # a tool that prints nothing: the turn loop has no "between events" to look up in
        harness.killed.wait(30)
        yield {"type": "done", "ok": False, "reply": ""}

    def press():
        time.sleep(0.3)
        stream.kv["stop"] = str(int(time.time() * 1000))

    threading.Thread(target=press, daemon=True).start()
    t0 = time.monotonic()
    engine.serve(stream, in_topic="in", out_topic="out", start={"entrypoint": {"inline": "grep it all"}},
                 turn=turn, idle_ms=1, harness=harness, stop_key="stop")
    assert time.monotonic() - t0 < 10
    assert harness.killed.is_set()
    kinds = [e["type"] for e in stream.out]
    assert {"type": "turn-truncated", "turn_id": "t0", "reason": engine.STOPPED_REASON} in stream.out
    assert kinds[-1] == "turn-complete"
    assert stream.kv["stop"] == ""                       # the press is consumed, once


def test_a_stop_pressed_before_the_turn_started_is_not_obeyed():
    stream, harness = _Stream(), _Harness()
    stream.kv["stop"] = str(int(time.time() * 1000) - 60_000)    # a Stop meant for an earlier turn
    watch = engine.StopWatch(stream, "stop", harness)
    assert watch.check() is False and not harness.killed.is_set()
    assert stream.kv["stop"] == ""


# ── (2) every shell command has a wall-clock limit, and the agent is told it timed out ──────────

def _guard(cmd: str, limit: int = 1, **env):
    return subprocess.run([str(GUARD), cmd], capture_output=True, text=True, timeout=60,
                          env={**os.environ, "VEXA_SHELL_TIMEOUT_SEC": str(limit), **env})


def test_the_shell_guard_kills_a_command_at_its_limit_and_says_timed_out(marker, tmp_path):
    t0 = time.monotonic()
    # the shape the CLI hands its prefix: eval the command, then record the cwd
    r = _guard(f"eval 'sleep {marker} | cat' && pwd -P >| {tmp_path}/cwd", limit=1)
    assert time.monotonic() - t0 < 15
    assert r.returncode == 124
    assert "timed out" in r.stderr and "1s" in r.stderr and "shell_timeout_sec" in r.stderr
    assert _wait_for(lambda: not _pids_with(f"sleep {marker}"), 5), "the pipeline outlived the limit"


def test_a_command_inside_the_limit_runs_untouched(tmp_path):
    r = _guard(f"eval 'echo hello; exit 3' && pwd -P >| {tmp_path}/cwd", limit=30)
    assert r.stdout == "hello\n" and r.returncode == 3 and r.stderr == ""


def test_the_cli_is_pointed_at_the_guard_and_its_own_timers_sit_behind_it(monkeypatch):
    monkeypatch.setenv("VEXA_SHELL_TIMEOUT_SEC", "45")
    env = claude_code._cli_env()
    assert env["CLAUDE_CODE_SHELL_PREFIX"] == str(GUARD)
    assert env["VEXA_SHELL_TIMEOUT_SEC"] == "45"
    assert int(env["BASH_DEFAULT_TIMEOUT_MS"]) > 45_000 and int(env["BASH_MAX_TIMEOUT_MS"]) > 45_000
    # the CLI must not move a slow command to the background and wait on it (what happened live)
    assert env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"


@pytest.mark.parametrize("raw", ["", "0", "abc", "999999", "-5"])
def test_an_unreadable_limit_is_the_default_never_no_limit(monkeypatch, raw):
    monkeypatch.setenv("VEXA_SHELL_TIMEOUT_SEC", raw)
    assert claude_code.shell_timeout_sec() == 120


def test_the_limit_is_company_policy_and_only_narrows(tmp_path):
    from control_plane import turn_budget

    def pol(d, line):
        d.mkdir(parents=True)
        (d / "POLICIES.md").write_text(f"---\n{line}\n---\n")
        return d

    g = pol(tmp_path / "g", "shell_timeout_sec: 300")
    ws = pol(tmp_path / "ws", "shell_timeout_sec: 60")
    me = pol(tmp_path / "me", "shell_timeout_sec: 3000")      # tries to raise: clipped
    assert turn_budget.shell_timeout(g, [ws, me]) == 60
    assert turn_budget.shell_timeout(g, [me]) == 300
    assert turn_budget.shell_timeout(tmp_path / "none", []) is None
    env = {}
    from control_plane.dispatch import stamp_turn_budget
    stamp_turn_budget(env, global_dir=str(g), mounts=[{"slug": "ws", "path": str(ws)}], target="ws")
    assert env["VEXA_SHELL_TIMEOUT_SEC"] == "60"


# ── (3) the agent does not read its own CLI transcripts ──────────────────────────────────────────

@pytest.mark.parametrize("cmd", [
    "eval 'cd /workspaces/.system/2; grep -o -i -h pin .claude/projects/-workspaces-2/*.jsonl | head -5' && pwd -P >| /dev/null",
    "eval 'cat ~/.claude/projects/-workspaces-2/x.jsonl' && pwd -P >| /dev/null",
    "eval 'ls /tmp/.vexa-session' && pwd -P >| /dev/null",
])
def test_the_shell_refuses_a_command_that_reads_the_transcripts(cmd):
    r = _guard(cmd, limit=30)
    assert r.returncode == 126
    assert "refused" in r.stderr and "transcripts" in r.stderr
    assert r.stdout == ""


def test_the_file_tools_are_denied_the_transcripts_too():
    argv = claude_code.build_argv("hi", workspace="/workspaces/2")
    rules = argv[argv.index("--disallowedTools") + 1].split(",")
    assert "Read(//workspaces/**/.claude/projects/**)" in rules
    assert "Read(~/.claude/projects/**)" in rules
    assert "Read(//tmp/.vexa-session/**)" in rules


# ── (4) the 409 says what is happening and offers the act that ends it ─────────────────────────

def test_a_busy_conversation_answers_a_previous_reply_is_still_running_with_stop():
    from control_plane import session_sync

    body = session_sync.answer("session_conflict", "s-1")
    assert body["detail"] == "A previous reply is still running in this conversation."
    assert body["fault"]["action"] == "stop"
    assert body["fault"]["remedy"].startswith("Stop it")
    assert "session conflict" not in json.dumps(body).lower()


from tests.test_inbox import client, fake_redis, _HEADERS, _UNIT  # noqa: E402,F401 — fixtures


def test_post_chat_stop_reaches_the_running_unit_and_the_lease_holder(client, fake_redis):
    from control_plane import session_sync

    r = client.post("/api/chat/stop", headers=_HEADERS, json={"session": "main"})
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["stopped"] >= 1
    pressed = int(fake_redis.get(units.stop_key(_UNIT)))
    assert abs(pressed - time.time() * 1000) < 60_000
    # the unit HOLDING the conversation (another mount generation, another participant) is told too
    from control_plane import api_shared
    fake_redis.set(session_sync.lease_key("s-9"), "unit-holder")
    assert api_shared.stop_turns("redis://fake", ["unit-mine"], session_id="s-9") == 2
    assert fake_redis.get(units.stop_key("unit-holder")) and fake_redis.get(units.stop_key("unit-mine"))
