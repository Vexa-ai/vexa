"""Codex app-server HarnessPort: JSON-RPC normalization, steering, grants, continuity."""
from __future__ import annotations

import json
import os
from pathlib import Path

from llm.codex import CodexHarness, _link_sessions_into_workspace, _mcp_config


class _Input:
    def __init__(self):
        self.lines: list[str] = []
        self.closed = False

    def write(self, value: str):
        self.lines.append(value)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _Process:
    def __init__(self, lines):
        self.stdin = _Input()
        self.stdout = iter(json.dumps(line) + "\n" for line in lines)
        self.stopped = False

    def poll(self):
        return 0 if self.stopped else None

    def terminate(self):
        self.stopped = True

    def kill(self):
        self.stopped = True

    def wait(self, timeout=None):
        self.stopped = True
        return 0


def _turn_lines(thread_id="thr_1"):
    return [
        {"id": 1, "result": {}},
        {"id": 2, "result": {"thread": {"id": thread_id}}},
        {"id": 3, "result": {"turn": {"id": "turn_1", "status": "inProgress"}}},
        {"method": "item/agentMessage/delta", "params": {"delta": "Hello ", "threadId": thread_id,
                                                               "turnId": "turn_1", "itemId": "a1"}},
        {"method": "item/started", "params": {"threadId": thread_id, "turnId": "turn_1",
                                                   "startedAtMs": 1, "item": {
                                                       "type": "commandExecution", "id": "cmd_1",
                                                       "command": "git status", "status": "inProgress"}}},
        {"method": "item/completed", "params": {"threadId": thread_id, "turnId": "turn_1",
                                                     "completedAtMs": 2, "item": {
                                                         "type": "commandExecution", "id": "cmd_1",
                                                         "command": "git status", "status": "completed",
                                                         "aggregatedOutput": "clean", "exitCode": 0}}},
        {"method": "item/agentMessage/delta", "params": {"delta": "world", "threadId": thread_id,
                                                               "turnId": "turn_1", "itemId": "a1"}},
        {"method": "turn/completed", "params": {"threadId": thread_id, "turn": {
            "id": "turn_1", "status": "completed", "items": []}}},
    ]


def test_codex_turn_normalizes_stream_and_returns_thread(tmp_path: Path):
    proc = _Process(_turn_lines())
    harness = CodexHarness(process_factory=lambda *a, **k: proc)

    events = list(harness.run_turn(tmp_path, "say hi"))

    assert [event["type"] for event in events] == [
        "message-delta", "tool-call", "tool-result", "message-delta", "done"]
    assert events[1] == {"type": "tool-call", "tool": "Bash",
                         "args": {"command": "git status"}, "callId": "cmd_1"}
    assert events[2]["ok"] is True and events[2]["summary"] == "clean"
    assert events[-1] == {"type": "done", "reply": "Hello world",
                          "sessionId": "thr_1", "ok": True}
    sent = [json.loads(line) for line in proc.stdin.lines]
    assert [message["method"] for message in sent] == [
        "initialize", "initialized", "thread/start", "turn/start"]
    assert sent[-1]["params"]["sandboxPolicy"]["type"] == "externalSandbox"


def test_codex_resume_and_same_turn_steer(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VEXA_MIDTURN_INJECT", "1")
    proc = _Process(_turn_lines("thr_old"))
    harness = CodexHarness(process_factory=lambda *a, **k: proc)
    turn = harness.run_turn(tmp_path, "first", session="thr_old")

    assert next(turn)["text"] == "Hello "  # turn/start response has installed active turn id
    assert harness.midturn_enabled() is True
    assert harness.inject_user_message("focus on tests") is True
    list(turn)

    sent = [json.loads(line) for line in proc.stdin.lines]
    assert sent[2]["method"] == "thread/resume" and sent[2]["params"]["threadId"] == "thr_old"
    steer = next(message for message in sent if message["method"] == "turn/steer")
    assert steer["params"] == {
        "threadId": "thr_old", "expectedTurnId": "turn_1",
        "input": [{"type": "text", "text": "focus on tests"}],
    }


def test_codex_mcp_translation_includes_only_auto_granted_servers(tmp_path: Path):
    config = tmp_path / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {
        "mail": {"command": "mail-mcp"}, "billing": {"command": "billing-mcp"}}}))
    assert _mcp_config(str(config), ["Read", "mcp__mail"]) == {
        "mcp_servers": {"mail": {"command": "mail-mcp"}}}


def test_codex_session_link_is_durable_and_never_clobbers_real_home(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()
    monkeypatch.setenv("HOME", str(home))

    _link_sessions_into_workspace(work)
    link = home / ".codex" / "sessions"
    assert link.is_symlink() and os.readlink(link) == str(work / ".claude" / "codex" / "sessions")

    home2 = tmp_path / "home2"
    real = home2 / ".codex" / "sessions"
    real.mkdir(parents=True)
    (real / "keep.jsonl").write_text("important")
    monkeypatch.setenv("HOME", str(home2))
    _link_sessions_into_workspace(work)
    assert not real.is_symlink() and (real / "keep.jsonl").read_text() == "important"


def test_codex_preflight_accepts_subscription_auth(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    auth = home / ".codex" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text('{"tokens":{"access_token":"redacted"}}')
    monkeypatch.setenv("HOME", str(home))
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    assert CodexHarness().preflight() is None


def test_codex_ignores_inherited_claude_model_pin(tmp_path: Path):
    proc = _Process(_turn_lines())
    harness = CodexHarness(process_factory=lambda *a, **k: proc)
    list(harness.run_turn(tmp_path, "hi", model="claude-opus-5"))
    start = next(json.loads(line) for line in proc.stdin.lines
                 if json.loads(line).get("method") == "thread/start")
    assert "model" not in start["params"]
