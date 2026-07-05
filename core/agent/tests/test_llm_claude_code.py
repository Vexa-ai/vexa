"""L2: the claude-code harness adapter — stream-json normalization (relocated from
test_unit_foundation), the generic run_harness_turn commit orchestration driven through the
adapter with a fake exec, and the transcript-size accounting behind resume ids."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import yaml

from llm import run_harness_turn
from llm.claude_code import ClaudeCodeHarness, build_argv, parse_stream_json


def _git(d: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=str(d), check=True, capture_output=True, text=True)


def _init_repo(d: Path) -> None:
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    (d / "AGENT.md").write_text("seed\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "seed")


def _entity(fm: dict, body: str = "body") -> str:
    return "---\n" + yaml.safe_dump(fm, sort_keys=True).strip() + "\n---\n" + body


GOOD = {"type": "person", "id": "jane-liu", "title": "Jane Liu"}
BAD = {"title": "no type or id"}  # missing required type + id → workspace.v1 violation


# ── stream-json normalization ────────────────────────────────────────────────

def test_parse_stream_json_normalizes():
    lines = [
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"}]}}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Write", "input": {"path": "x"}, "id": "t1"}]}}),
        json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}),
        json.dumps({"type": "result", "subtype": "success", "result": "done", "session_id": "s1"}),
        "not json — skipped",
    ]
    evs = list(parse_stream_json(lines))
    assert [e["type"] for e in evs] == ["message-delta", "tool-call", "tool-result", "done"]
    assert evs[0]["text"] == "hello"
    assert evs[1]["tool"] == "Write" and evs[1]["callId"] == "t1"
    assert evs[2]["ok"] is True
    assert evs[3]["sessionId"] == "s1" and evs[3]["ok"] is True


def test_parse_stream_json_partial_messages_stream_incrementally():
    # Captured --include-partial-messages JSONL shape: stream_event(content_block_delta/text_delta)*
    # then the consolidated assistant text block, then result. The deltas must surface incrementally
    # AND the trailing full block must NOT re-emit (else the text doubles).
    lines = [
        json.dumps({"type": "stream_event", "event": {"type": "message_start"}}),
        json.dumps({"type": "stream_event", "event": {"type": "content_block_start", "index": 0}}),
        json.dumps({"type": "stream_event", "event": {
            "type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hel"}}}),
        json.dumps({"type": "stream_event", "event": {
            "type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "lo "}}}),
        json.dumps({"type": "stream_event", "event": {
            "type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "world"}}}),
        json.dumps({"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}}),
        # the consolidated assistant message the CLI emits at block close — must be suppressed:
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Hello world"}]}}),
        json.dumps({"type": "result", "subtype": "success", "result": "Hello world", "session_id": "s2"}),
    ]
    evs = list(parse_stream_json(lines))
    assert [e["type"] for e in evs] == ["message-delta", "message-delta", "message-delta", "done"]
    assert [e["text"] for e in evs[:3]] == ["Hel", "lo ", "world"]
    # incremental deltas concatenate to the full text with no duplication:
    assert "".join(e["text"] for e in evs[:3]) == "Hello world"
    # the result still carries the full reply (commit messages / non-streaming consumers):
    assert evs[3]["reply"] == "Hello world"


# ── the argv (the CLI contract) ──────────────────────────────────────────────

def test_build_argv_core_flags_and_session_model():
    argv = build_argv("hi", allowed_tools=["Read"], session="s1", model="m1")
    assert argv[:3] == ["claude", "-p", "hi"]
    assert "--output-format" in argv and "stream-json" in argv
    assert "--allowedTools" in argv and "Read" in argv
    assert "--resume" in argv and "s1" in argv
    assert "--model" in argv and "m1" in argv


# ── run_harness_turn: conformant + free-zone writes both commit ──────────────

def test_run_harness_turn_commits_conformant(tmp_path: Path):
    repo = tmp_path / "ws"
    repo.mkdir()
    _init_repo(repo)

    def fake_exec(argv, cwd):  # the "model" writes a conformant entity via its tools, then finishes
        f = Path(cwd) / "kg/entities/person/jane-liu.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(_entity(GOOD))
        yield json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "wrote jane"}]}})
        yield json.dumps({"type": "result", "subtype": "success", "result": "wrote jane", "session_id": "s1"})

    evs = list(run_harness_turn(repo, "create jane", ClaudeCodeHarness(exec_fn=fake_exec)))
    assert any(e["type"] == "commit" for e in evs)
    assert (repo / "kg/entities/person/jane-liu.md").exists()
    log = subprocess.run(["git", "log", "--oneline"], cwd=str(repo), capture_output=True, text=True).stdout
    assert "wrote jane" in log


def test_run_harness_turn_commits_nonconformant_free_zone(tmp_path: Path):
    """Free zone: governance is prompt-only — a non-conformant entity write is NOT reverted;
    it commits like any other write (no enforcement gate)."""
    repo = tmp_path / "ws"
    repo.mkdir()
    _init_repo(repo)

    def fake_exec(argv, cwd):  # the "model" writes a non-conformant entity (missing type+id)
        f = Path(cwd) / "kg/entities/person/bad.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(_entity(BAD))
        yield json.dumps({"type": "result", "subtype": "success", "result": "x", "session_id": "s1"})

    evs = list(run_harness_turn(repo, "create bad", ClaudeCodeHarness(exec_fn=fake_exec)))
    assert not any(e["type"] == "rejected" for e in evs), "free zone never rejects"
    assert any(e["type"] == "commit" for e in evs), "the write must commit"
    assert (repo / "kg/entities/person/bad.md").exists()


def test_run_harness_turn_propose_only_touches_no_git(tmp_path: Path):
    repo = tmp_path / "ws"
    repo.mkdir()
    _init_repo(repo)

    def fake_exec(argv, cwd):
        yield json.dumps({"type": "result", "subtype": "success", "result": "looked", "session_id": "s1"})

    evs = list(run_harness_turn(repo, "look", ClaudeCodeHarness(exec_fn=fake_exec), commit=False))
    assert [e["type"] for e in evs] == ["done"]  # no commit event on the propose-only path


# ── per-mount commit + attribution (WP-A1.2 / D4) ────────────────────────────

def _author_of(repo: Path) -> tuple[str, str, str, str]:
    """(author name, author email, committer name, committer email) of HEAD."""
    fmt = "%an%n%ae%n%cn%n%ce"
    out = subprocess.run(["git", "log", "-1", f"--pretty=format:{fmt}"], cwd=str(repo),
                         capture_output=True, text=True).stdout.splitlines()
    return tuple(out)  # type: ignore[return-value]


def test_run_harness_turn_commits_each_changed_mount_independently(tmp_path: Path):
    """The active mount set → ONE commit per changed mount (WP-A1.2). The primary and an extra mount
    are separate repos: a turn that writes both yields TWO commit events, one landing in each repo."""
    private = tmp_path / "private"; private.mkdir(); _init_repo(private)
    shared = tmp_path / "shared"; shared.mkdir(); _init_repo(shared)

    def fake_exec(argv, cwd):  # the "model" writes into BOTH mounts
        (Path(cwd) / "note.md").write_text("private note")
        (shared / "doc.md").write_text("shared doc")
        yield json.dumps({"type": "result", "subtype": "success", "result": "wrote both", "session_id": "s1"})

    evs = list(run_harness_turn(private, "write both", ClaudeCodeHarness(exec_fn=fake_exec),
                                extra_mounts=[shared]))
    commits = [e for e in evs if e["type"] == "commit"]
    assert len(commits) == 2, "one commit per changed mount"
    # each mount got its OWN commit (distinct repos, distinct HEADs)
    assert (private / "note.md").exists() and (shared / "doc.md").exists()
    assert "wrote both" in subprocess.run(["git", "log", "--oneline"], cwd=str(private), capture_output=True, text=True).stdout
    assert "wrote both" in subprocess.run(["git", "log", "--oneline"], cwd=str(shared), capture_output=True, text=True).stdout


def test_run_harness_turn_only_commits_the_mount_that_changed(tmp_path: Path):
    """An extra mount with NO writes must not get an empty commit — only the changed mount commits."""
    private = tmp_path / "private"; private.mkdir(); _init_repo(private)
    shared = tmp_path / "shared"; shared.mkdir(); _init_repo(shared)
    shared_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(shared), capture_output=True, text=True).stdout.strip()

    def fake_exec(argv, cwd):  # writes ONLY the private mount
        (Path(cwd) / "note.md").write_text("only private")
        yield json.dumps({"type": "result", "subtype": "success", "result": "one", "session_id": "s1"})

    evs = list(run_harness_turn(private, "write private", ClaudeCodeHarness(exec_fn=fake_exec),
                                extra_mounts=[shared]))
    assert len([e for e in evs if e["type"] == "commit"]) == 1
    # shared HEAD is unmoved (no empty commit)
    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(shared), capture_output=True, text=True).stdout.strip() == shared_head


def test_run_harness_turn_attributes_commit_to_the_principal(tmp_path: Path):
    """Attribution (D4): author = the dispatch principal, committer = the platform — on EACH mount."""
    private = tmp_path / "private"; private.mkdir(); _init_repo(private)
    shared = tmp_path / "shared"; shared.mkdir(); _init_repo(shared)

    def fake_exec(argv, cwd):
        (Path(cwd) / "n.md").write_text("p")
        (shared / "d.md").write_text("s")
        yield json.dumps({"type": "result", "subtype": "success", "result": "x", "session_id": "s1"})

    list(run_harness_turn(private, "write", ClaudeCodeHarness(exec_fn=fake_exec),
                          author=("Jane Doe", "jane@example.com"), extra_mounts=[shared]))
    for repo in (private, shared):
        an, ae, cn, ce = _author_of(repo)
        assert (an, ae) == ("Jane Doe", "jane@example.com"), "author = principal"
        assert (cn, ce) == ("Vexa", "platform@vexa.ai"), "committer = platform"


# ── transcript-size accounting (resume-cost cap) ─────────────────────────────

def test_transcript_bytes_sums_matching_session_files(tmp_path: Path):
    proj = tmp_path / ".claude" / "projects" / "-workspace"
    proj.mkdir(parents=True)
    (proj / "sid-1.jsonl").write_text("x" * 100)
    (proj / "other.jsonl").write_text("y" * 999)
    other = tmp_path / ".claude" / "projects" / "-elsewhere"
    other.mkdir(parents=True)
    (other / "sid-1.jsonl").write_text("x" * 23)
    h = ClaudeCodeHarness()
    assert h.transcript_bytes(tmp_path, "sid-1") == 123
    assert h.transcript_bytes(tmp_path, "missing") == 0


# ── ~/.claude/projects linking — must NEVER destroy real transcripts ─────────

def _prepare_with_home(monkeypatch, home: Path, work: Path) -> Path:
    """Run prepare() against an explicit fake HOME; returns the ``~/.claude/projects`` path."""
    monkeypatch.setenv("HOME", str(home))
    ClaudeCodeHarness().prepare(work)
    return home / ".claude" / "projects"


def test_prepare_refuses_to_delete_real_transcripts(tmp_path: Path, monkeypatch):
    # Regression for the 2026-07-03 incident: a host test run pointed prepare() at the developer's
    # real HOME and rmtree'd every Claude Code transcript. A non-empty projects dir was not created
    # by this adapter — it must survive prepare() byte-for-byte, with the link skipped.
    home = tmp_path / "home"
    transcript = home / ".claude" / "projects" / "-real-project" / "s1.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text('{"precious": true}')
    link = _prepare_with_home(monkeypatch, home, work=tmp_path / "ws")
    assert link.is_dir() and not link.is_symlink()  # still the real dir, not a link
    assert transcript.read_text() == '{"precious": true}'


def test_prepare_links_fresh_and_empty_homes(tmp_path: Path, monkeypatch):
    ws = tmp_path / "ws"
    target = str(ws / ".claude" / "projects")
    # fresh container HOME (no projects dir at all) → link created
    link = _prepare_with_home(monkeypatch, tmp_path / "h1", ws)
    assert link.is_symlink() and os.readlink(link) == target
    # an EMPTY dir (the CLI pre-creates one) holds nothing → safe to replace with the link
    home2 = tmp_path / "h2"
    (home2 / ".claude" / "projects").mkdir(parents=True)
    link = _prepare_with_home(monkeypatch, home2, ws)
    assert link.is_symlink() and os.readlink(link) == target


def test_prepare_repoints_stale_symlink_and_is_idempotent(tmp_path: Path, monkeypatch):
    ws = tmp_path / "ws"
    target = str(ws / ".claude" / "projects")
    home = tmp_path / "home"
    old_ws = tmp_path / "old"
    (old_ws / ".claude" / "projects").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "projects").symlink_to(old_ws / ".claude" / "projects")
    link = _prepare_with_home(monkeypatch, home, ws)  # stale link from a previous workspace → repointed
    assert os.readlink(link) == target
    link = _prepare_with_home(monkeypatch, home, ws)  # second turn, already correct → no-op
    assert os.readlink(link) == target
