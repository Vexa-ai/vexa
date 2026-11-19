"""Codex app-server HarnessPort: JSON-RPC normalization, steering, grants, continuity."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from llm import faults
from llm.codex import (CodexHarness, _link_sessions_into_workspace, _mcp_config,
                       normalize_notification)


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


# ── the panel conventions, shared with the other two harnesses (F92) ────────────────────────────

def _completed(item):
    return normalize_notification({"method": "item/completed", "params": {"item": item}}, [])


def test_a_codex_file_change_opens_the_writers_tab():
    """F92 REPRODUCTION. This adapter emitted none of the three panel conventions, so the SAME turn
    painted the person's screen or did not depending on which harness the deployment ran."""
    evs = _completed({"type": "fileChange", "id": "i1", "status": "completed",
                      "changes": [{"path": "/workspaces/u_1/notes/a.md"},
                                  {"path": "/workspaces/u_1/notes/b.md"}]})
    arts = [e for e in evs if e["type"] == "artifact"]
    assert [(a["workspace"], a["path"]) for a in arts] == [("u_1", "notes/a.md"), ("u_1", "notes/b.md")]
    assert [a["focus"] for a in arts] == [False, True]   # the last write is the one to look at


def test_a_codex_workspace_write_opens_the_writers_tab():
    evs = _completed({"type": "mcpToolCall", "id": "i2", "status": "completed",
                      "server": "vexa", "tool": "workspace_write",
                      "arguments": {"slug": "_global", "path": "README.md"},
                      "result": {"content": [{"type": "text", "text": "ok"}]}})
    art = next(e for e in evs if e["type"] == "artifact")
    assert (art["workspace"], art["path"], art["focus"]) == ("_global", "README.md", True)


def test_a_codex_transcript_terms_publish_paints_the_chips():
    body = json.dumps({"meeting": "42", "cursor": "c1", "emit": [{"term": "TSC"}]})
    evs = _completed({"type": "mcpToolCall", "id": "i3", "status": "completed",
                      "server": "vexa", "tool": "transcript_terms",
                      "arguments": {}, "result": {"content": [{"type": "text", "text": body}]}})
    terms = next(e for e in evs if e["type"] == "terms")
    assert terms["meeting"] == "42" and terms["terms"] == [{"term": "TSC"}]


def test_a_codex_bot_send_opens_the_live_transcript():
    body = json.dumps({"id": 77, "native_meeting_id": "abc-defg-hij", "status": "requested"})
    evs = _completed({"type": "mcpToolCall", "id": "i4", "status": "completed",
                      "server": "vexa", "tool": "request_meeting_bot",
                      "arguments": {}, "result": body})
    art = next(e for e in evs if e["type"] == "artifact")
    assert art["path"] == "meeting:77" and art["pin"] is True


def test_a_failed_codex_item_moves_nothing():
    """Success only — a failed call must move no panel, exactly as in the other two harnesses."""
    evs = _completed({"type": "fileChange", "id": "i5", "status": "failed",
                      "changes": [{"path": "/workspaces/u_1/notes/a.md"}]})
    assert [e["type"] for e in evs] == ["tool-result"]


def test_codex_reads_its_home_from_codex_home_not_home(tmp_path: Path, monkeypatch):
    """The runtime mounts the subscription credential at $CODEX_HOME/auth.json and names CODEX_HOME;
    the worker image's HOME is elsewhere. The preflight and the session link follow CODEX_HOME."""
    from llm import codex as codex_mod

    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text(json.dumps({"tokens": {"access_token": "x"}}))
    monkeypatch.setenv("HOME", str(tmp_path / "elsewhere"))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    for k in ("OPENAI_API_KEY", "CODEX_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert codex_mod.codex_home() == codex_home
    assert CodexHarness().preflight() is None
    work = tmp_path / "ws"
    CodexHarness().prepare(work)
    assert (codex_home / "sessions").is_symlink()
    monkeypatch.delenv("CODEX_HOME")
    assert codex_mod.codex_home() == tmp_path / "elsewhere" / ".codex"
    assert CodexHarness().preflight() is not None        # nothing at $HOME/.codex/auth.json


# ── a failed turn ends TYPED (P18 — architecture pass 6, S66) ───────────────────────────────────
# A failed Codex turn used to end `{"type": "done", "ok": False}` with the error's words as the reply
# and no `fault`, so the chat could not say who failed — the one harness of three that did not
# translate (`llm/faults.py`: every harness does).

def _failed_turn_lines(error: dict, *, said: str = "", thread_id="thr_1"):
    lines = _turn_lines(thread_id)[:3]
    if said:
        lines.append({"method": "item/agentMessage/delta", "params": {
            "delta": said, "threadId": thread_id, "turnId": "turn_1", "itemId": "a1"}})
    lines.append({"method": "turn/completed", "params": {"threadId": thread_id, "turn": {
        "id": "turn_1", "status": "failed", "items": [], "error": error}}})
    return lines


def _done_of(lines, tmp_path: Path, monkeypatch) -> dict:
    for k in ("OPENAI_API_KEY", "CODEX_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    proc = _Process(lines)
    events = list(CodexHarness(process_factory=lambda *a, **k: proc).run_turn(tmp_path, "hi"))
    assert events[-1]["type"] == "done" and events[-1]["ok"] is False
    return events[-1]


def test_a_codex_usage_limit_ends_the_turn_as_the_providers_fault(tmp_path: Path, monkeypatch):
    said = "You've hit your usage limit. Try again at 4:12 PM."
    done = _done_of(_failed_turn_lines({"message": said, "codexErrorInfo": "usageLimitExceeded"}),
                    tmp_path, monkeypatch)
    assert done["fault"]["source"] == "model-provider" and done["fault"]["kind"] == "unpaid"
    assert done["fault"]["provider"] == "chatgpt.com"
    assert done["detail"] == said and done["reply"] == faults.ProviderFault(**done["fault"]).sentence()


@pytest.mark.parametrize("info, kind, status", [
    ("unauthorized", "unauthorized", None),
    ("rateLimitExceeded", "rate_limited", None),
    ("serverOverloaded", "unavailable", None),
    ({"httpConnectionFailed": {"httpStatusCode": 401}}, "unauthorized", 401),
    ({"responseStreamDisconnected": {"httpStatusCode": None}}, "unavailable", None),
    ({"responseTooManyFailedAttempts": {"httpStatusCode": 503}}, "unavailable", 503),
])
def test_codex_error_labels_name_the_kind(info, kind, status, tmp_path: Path, monkeypatch):
    done = _done_of(_failed_turn_lines({"message": "stream failed", "codexErrorInfo": info}),
                    tmp_path, monkeypatch)
    assert (done["fault"]["kind"], done["fault"]["status"]) == (kind, status)


def test_what_the_model_already_said_stays_the_reply(tmp_path: Path, monkeypatch):
    done = _done_of(_failed_turn_lines({"message": "rate limited", "codexErrorInfo": "rateLimitExceeded"},
                                       said="Here is the first half"), tmp_path, monkeypatch)
    assert done["fault"]["kind"] == "rate_limited" and done["reply"] == "Here is the first half"


def test_a_failure_that_names_no_provider_stays_untyped(tmp_path: Path, monkeypatch):
    """Codex's own limits are not the provider's — and an untyped failed `done` is what lets the
    worker retry a stale resume, so nothing is typed that the error does not name."""
    done = _done_of(_failed_turn_lines({"message": "sandbox denied the command", "codexErrorInfo": "sandboxError"}),
                    tmp_path, monkeypatch)
    assert "fault" not in done and done["reply"] == "sandbox denied the command"


def test_a_refused_json_rpc_call_is_typed_from_its_words(tmp_path: Path, monkeypatch):
    lines = [{"id": 1, "result": {}}, {"id": 2, "result": {"thread": {"id": "thr_1"}}},
             {"id": 3, "error": {"code": -32603, "message": "unexpected status 401 Unauthorized: invalid api key"}}]
    done = _done_of(lines, tmp_path, monkeypatch)
    assert done["fault"]["kind"] == "unauthorized" and done["fault"]["status"] == 401
    assert "invalid api key" in done["detail"] and done["reply"].startswith("The model provider (chatgpt.com)")
