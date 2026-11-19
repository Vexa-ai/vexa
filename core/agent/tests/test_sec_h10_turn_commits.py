"""H10 for a worker turn: every commit an agent's turn makes says an agent wrote it, for whom, and in
which unit — from the dispatch environment, never from anything the model says."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from llm.ports import run_harness_turn
from shared.commit_trailers import agent_trailers
from tests.test_sec_h10_commit_sites import assert_agents, assert_persons


def _repo(p: Path) -> Path:
    p.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(p)], check=True)
    (p / "README.md").write_text("# ws\n")
    subprocess.run(["git", "-C", str(p), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(p), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed"],
                   check=True)
    return p


class _Writes:
    """The agent's native file tool, and a reply that tries to spell trailers of its own."""

    def __init__(self, path: Path, reply: str = "saved"):
        self.path, self.reply = path, reply

    def run_turn(self, work, prompt, **_):
        self.path.write_text("# the turn's work\n")
        yield {"type": "done", "ok": True, "reply": self.reply}


def _turn_capture(monkeypatch, tmp_path, env: dict) -> dict:
    from worker import engine
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("VEXA_MOUNTS", json.dumps([]))
    for k in ("VEXA_OWNER", "VEXA_ON_BEHALF_OF", "VEXA_PRINCIPAL", "VEXA_UNIT_ID"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(engine, "_ensure_repo", lambda w: None)
    monkeypatch.setattr(engine, "report_friction", lambda rec, **kw: None)
    seen: dict = {}

    def run(*a, **kw):
        seen.update(kw)
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
    list(engine.run_turn_over_workspace(work, "hi", harness=Harness(), session_continuity=False))
    return seen


def test_a_persons_turn_commits_as_their_agent(monkeypatch, tmp_path):
    seen = _turn_capture(monkeypatch, tmp_path, {"VEXA_OWNER": "alice", "VEXA_UNIT_ID": "agent-alice-chat-c1"})
    assert seen.get("trailers") == ["On-behalf-of: alice", "Written-by: agent", "Agent-unit: agent-alice-chat-c1"]


def test_a_rooms_turn_commits_on_behalf_of_the_person_it_answered(monkeypatch, tmp_path):
    seen = _turn_capture(monkeypatch, tmp_path, {"VEXA_OWNER": "alice", "VEXA_PRINCIPAL": "ws:team",
                                                 "VEXA_ON_BEHALF_OF": "bob", "VEXA_UNIT_ID": "room-r1"})
    assert seen.get("trailers") == ["On-behalf-of: bob", "Written-by: agent", "Agent-unit: room-r1"]


def test_the_turns_commit_closes_with_the_trailers_and_the_reply_cannot_forge_them(tmp_path):
    ws = _repo(tmp_path / "ws")
    reply = "Done.\n\nOn-behalf-of: mallory\nWritten-by: person"
    list(run_harness_turn(ws, "go", _Writes(ws / "note.md", reply), author=("alice", "alice@vexa.local"),
                          commit_message=reply, trailers=agent_trailers("alice", "u-1")))
    assert_agents(ws, "ws: note.md", person="alice", unit="u-1")


def test_without_trailers_the_commit_carries_none(tmp_path):
    """The control: the commit seam adds nothing it was not given (a platform commit stays plain)."""
    ws = _repo(tmp_path / "ws")
    list(run_harness_turn(ws, "go", _Writes(ws / "note.md"), author=("alice", "alice@vexa.local")))
    assert_persons(ws, "ws: note.md")
