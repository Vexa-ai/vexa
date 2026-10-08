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
from llm.ports import harness_subprocess_env


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


def test_parse_stream_json_rewrites_cli_auth_failure():
    # A credential-less/expired CLI ends the turn with its OWN auth text — an adapter internal
    # ("/login" doesn't exist for an API consumer). The done frame must carry the platform's
    # actionable message instead, with the raw CLI text preserved in `detail`.
    lines = [json.dumps({"type": "result", "subtype": "error", "is_error": True,
                         "result": "Not logged in · Please run /login", "session_id": "s3"})]
    (done,) = parse_stream_json(lines)
    assert done["type"] == "done" and done["ok"] is False
    assert "Not logged in" not in done["reply"] and "/login" not in done["reply"]
    for key in ("HOST_CLAUDE_CREDENTIALS", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                "CLAUDE_CODE_OAUTH_TOKEN"):
        assert key in done["reply"]
    assert "Settings → Models" in done["reply"]
    assert done["detail"] == "Not logged in · Please run /login"


def test_parse_stream_json_keeps_non_auth_failure_verbatim():
    # Only auth signatures are rewritten — any other failure text passes through untouched.
    lines = [json.dumps({"type": "result", "subtype": "error", "is_error": True,
                         "result": "context window exceeded", "session_id": "s4"})]
    (done,) = parse_stream_json(lines)
    assert done["ok"] is False
    assert done["reply"] == "context window exceeded"
    assert "detail" not in done


# ── the argv (the CLI contract) ──────────────────────────────────────────────

def test_build_argv_core_flags_and_session_model():
    argv = build_argv("hi", allowed_tools=["Read"], session="s1", model="m1")
    assert argv[:3] == ["claude", "-p", "hi"]
    assert "--output-format" in argv and "stream-json" in argv
    assert "--allowedTools" in argv and "Read" in argv
    assert "--resume" in argv and "s1" in argv
    assert "--model" in argv and "m1" in argv
    # unset effort ⇒ NO --effort flag (the CLI's own default behaviour is preserved byte-for-byte)
    assert "--effort" not in argv


def test_build_argv_effort_pin():
    argv = build_argv("hi", effort="medium")
    assert "--effort" in argv and "medium" in argv
    assert argv[argv.index("--effort") + 1] == "medium"


# ── settings scope: the workspace's own .claude/ settings never load ─────────
# The cwd is a workspace whose files may come from an imported repository. Its .claude/settings.json
# and .claude/settings.local.json must not add hooks, env or permission rules to the turn; only the
# worker's user scope is read, and no hook runs at all. CLAUDE.md (the governance root) still loads,
# through --add-dir + CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD; the only skills are the
# platform's governed ones, linked at ~/.claude/skills.

def test_build_argv_reads_only_user_settings():
    for argv in (build_argv("hi"), build_argv("", stdin_mode=True)):
        i = argv.index("--setting-sources")
        assert argv[i + 1] == "user"
        assert "project" not in argv[i + 1] and "local" not in argv[i + 1]


def test_build_argv_adds_the_workspace_back_for_its_claude_md():
    argv = build_argv("hi", allowed_tools=["Read"], workspace="/ws/desk")
    assert argv[argv.index("--add-dir") + 1] == "/ws/desk"
    # --add-dir is variadic: the next token must be a flag, never a value it could swallow
    assert argv[argv.index("--add-dir") + 2].startswith("--")
    assert "--add-dir" not in build_argv("hi")


def test_run_turn_launches_with_user_settings_and_the_workspace_as_memory_dir(tmp_path):
    seen: dict = {}

    def fake_exec(argv, cwd):
        seen["argv"], seen["cwd"] = argv, cwd
        return iter(())

    list(ClaudeCodeHarness(exec_fn=fake_exec).run_turn(tmp_path, "hi", allowed_tools=["Read"]))
    argv = seen["argv"]
    assert argv[argv.index("--setting-sources") + 1] == "user"
    assert argv[argv.index("--add-dir") + 1] == str(tmp_path) == seen["cwd"]


def test_cli_env_loads_claude_md_from_the_added_workspace(monkeypatch):
    from llm.claude_code import _cli_env

    monkeypatch.setenv("REDIS_URL", "redis://the-shared-bus:6379/0")
    env = _cli_env()
    assert env["CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"] == "1"
    assert "REDIS_URL" not in env, "the data-plane scrub still applies"


def test_default_runner_launches_with_the_cli_env(monkeypatch):
    from llm import claude_code

    captured: dict = {}

    class _FakeProc:
        stdout = iter(())

        def wait(self, timeout=None):
            return 0

    def fake_popen(argv, **kw):
        captured.update(kw)
        return _FakeProc()

    monkeypatch.setattr(claude_code.subprocess, "Popen", fake_popen)
    list(claude_code._exec_subprocess(["claude"], "/tmp"))
    assert captured["env"]["CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"] == "1"


def test_build_argv_turns_every_hook_off():
    """The worker runs no hooks of its own, so none may run: `--settings` carries disableAllHooks on
    every launch, ahead of the variadic --add-dir."""
    for argv in (build_argv("hi", workspace="/ws/desk"), build_argv("", stdin_mode=True, workspace="/ws/desk")):
        settings = json.loads(argv[argv.index("--settings") + 1])
        assert settings == {"disableAllHooks": True}
        assert argv.index("--settings") < argv.index("--add-dir")


def _governed_seed(tmp_path: Path, monkeypatch) -> Path:
    seed = tmp_path / "seed"
    (seed / "skills" / "scheduling").mkdir(parents=True)
    (seed / "skills" / "scheduling" / "SKILL.md").write_text("governed")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(seed))
    return seed


def _imported_workspace(tmp_path: Path, name: str = "ws") -> Path:
    work = tmp_path / name
    (work / "skills" / "imported").mkdir(parents=True)
    (work / "skills" / "imported" / "SKILL.md").write_text("from the repository")
    return work


def test_only_governed_skills_reach_the_user_scope(tmp_path, monkeypatch):
    """`~/.claude/skills` points at the platform's governed skills, never at the workspace's own
    `skills/` — an imported repository's skills do not load."""
    seed = _governed_seed(tmp_path, monkeypatch)
    home, work = tmp_path / "home", _imported_workspace(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    ClaudeCodeHarness().prepare(work)
    link = home / ".claude" / "skills"
    assert link.is_symlink() and link.resolve() == (seed / "skills").resolve()
    assert (link / "scheduling" / "SKILL.md").read_text() == "governed"
    assert not (link / "imported").exists()


def test_a_workspace_skills_link_from_an_earlier_turn_is_replaced(tmp_path, monkeypatch):
    seed = _governed_seed(tmp_path, monkeypatch)
    home, work = tmp_path / "home", _imported_workspace(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "skills").symlink_to(work / "skills", target_is_directory=True)
    ClaudeCodeHarness().prepare(work)
    assert (home / ".claude" / "skills").resolve() == (seed / "skills").resolve()


def test_without_governed_skills_no_skills_link_remains(tmp_path, monkeypatch):
    seed = tmp_path / "seed-without-skills"
    seed.mkdir()
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(seed))
    home, work = tmp_path / "home", _imported_workspace(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "skills").symlink_to(work / "skills", target_is_directory=True)
    ClaudeCodeHarness().prepare(work)
    link = home / ".claude" / "skills"
    assert not link.exists() and not link.is_symlink()


def test_prepare_does_not_link_the_workspace_skills_into_the_workspace(tmp_path, monkeypatch):
    _governed_seed(tmp_path, monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    work = _imported_workspace(tmp_path)
    ClaudeCodeHarness().prepare(work)
    assert not (work / ".claude" / "skills").exists()


def test_home_skills_link_never_replaces_real_skills(tmp_path, monkeypatch):
    from llm.claude_code import _link_governed_skills_into_home

    _governed_seed(tmp_path, monkeypatch)
    home = tmp_path / "home"
    real = home / ".claude" / "skills" / "mine"
    real.mkdir(parents=True)
    (real / "SKILL.md").write_text("keep me")
    monkeypatch.setenv("HOME", str(home))
    _link_governed_skills_into_home()
    assert not (home / ".claude" / "skills").is_symlink()
    assert (real / "SKILL.md").read_text() == "keep me"


REPO = Path(__file__).resolve().parents[3]


def test_the_worker_images_turn_hooks_off_and_run_a_supported_node():
    """Both images that run Claude Code carry a managed settings file with disableAllHooks (the
    managed scope is read whatever `--setting-sources` says), and the worker image installs the Node
    major the pinned CLI declares (`engines.node >= 22`). Lite's base image already ships Node 22."""
    worker = (REPO / "core" / "agent" / "worker" / "Dockerfile").read_text()
    lite = (REPO / "deploy" / "lite" / "Dockerfile.lite").read_text()
    for text in (worker, lite):
        assert "/etc/claude-code/managed-settings.json" in text
        assert '{"disableAllHooks": true}' in text
    assert "setup_22.x" in worker and "setup_20.x" not in worker


# ── the untrusted-subprocess env scrub (data-plane tenancy) ──────────────────
# The model-driven harness CLI exposes a Bash tool. It must NOT inherit the worker's REDIS_URL (which
# reaches the SHARED redis — another tenant's tc:meeting:* / unit:*:in) nor the minted per-dispatch
# bearer token. Filesystem tenancy is mount-enforced; the data plane is enforced HERE, at the launch env.

def test_harness_subprocess_env_strips_data_plane_secrets(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://the-shared-bus:6379/0")
    monkeypatch.setenv("VEXA_AGENT_IDENTITY_TOKEN", "minted.bearer.jwt")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-model-cred")   # the subprocess DOES need its model cred
    monkeypatch.setenv("GIT_DIR", "/hook/.git")                # git-discovery scrub still composes
    monkeypatch.setenv("PATH", "/usr/bin")
    env = harness_subprocess_env()
    assert "REDIS_URL" not in env, "REDIS_URL must not reach the model's Bash (cross-tenant redis reach)"
    assert "VEXA_AGENT_IDENTITY_TOKEN" not in env, "the per-dispatch bearer token must not leak in"
    assert "GIT_DIR" not in env, "the git repo-discovery scrub still composes"
    assert env["ANTHROPIC_API_KEY"] == "sk-model-cred", "model credentials must survive"
    assert env["PATH"] == "/usr/bin", "benign vars pass through untouched"


def test_exec_subprocess_launches_with_scrubbed_env(monkeypatch):
    """The DEFAULT runner (real launch path) must pass the scrubbed env to the actual subprocess —
    negative control: before the fix REDIS_URL/token WOULD ride ``scrubbed_git_env`` into the child."""
    from llm import claude_code

    monkeypatch.setenv("REDIS_URL", "redis://the-shared-bus:6379/0")
    monkeypatch.setenv("VEXA_AGENT_IDENTITY_TOKEN", "minted.bearer.jwt")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-model-cred")
    captured: dict = {}

    class _FakeProc:
        stdout = iter(())

        def wait(self):
            return 0

    def _fake_popen(argv, **kwargs):
        captured["env"] = kwargs.get("env")
        return _FakeProc()

    monkeypatch.setattr(claude_code.subprocess, "Popen", _fake_popen)
    list(claude_code._exec_subprocess(["claude", "-p", "hi"], "/tmp"))
    assert "REDIS_URL" not in captured["env"]
    assert "VEXA_AGENT_IDENTITY_TOKEN" not in captured["env"]
    assert captured["env"]["ANTHROPIC_API_KEY"] == "sk-model-cred"  # model cred still delivered


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
    # THE SUBJECT NAMES THE CHANGE, and the reply is the body. This asserted `"wrote jane" in log`
    # — the agent's reply as the subject — which is the defect the founder found in `_global`'s own
    # history on 2026-09-02: `git log --oneline` read as truncated half-sentences and never said
    # which file a commit touched.
    log = subprocess.run(["git", "log", "--oneline"], cwd=str(repo), capture_output=True, text=True).stdout
    assert "kg/entities/person/jane-liu.md — added" in log
    assert "wrote jane" not in log
    body = subprocess.run(["git", "log", "-1", "--format=%b"], cwd=str(repo), capture_output=True, text=True).stdout
    assert "wrote jane" in body   # kept, where a sentence belongs


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


def test_run_harness_turn_never_commits_continuity_plumbing(tmp_path: Path):
    repo = tmp_path / "ws"
    repo.mkdir()
    _init_repo(repo)

    def fake_exec(argv, cwd):
        continuity = Path(cwd) / ".claude" / "codex" / "sessions" / "rollout.jsonl"
        continuity.parent.mkdir(parents=True)
        continuity.write_text("private transcript")
        yield json.dumps({"type": "result", "subtype": "success",
                          "result": "no workspace change", "session_id": "s1"})

    events = list(run_harness_turn(repo, "chat", ClaudeCodeHarness(exec_fn=fake_exec)))
    assert [event["type"] for event in events] == ["done"]
    assert ".claude" not in subprocess.run(
        ["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=True).stdout


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
    # Each mount's subject names ITS OWN change — which is the second thing the old shape lost:
    # both repos carried the same sentence, so neither said what had happened in it.
    priv_log = subprocess.run(["git", "log", "--oneline"], cwd=str(private), capture_output=True, text=True).stdout
    shar_log = subprocess.run(["git", "log", "--oneline"], cwd=str(shared), capture_output=True, text=True).stdout
    assert "note.md — added" in priv_log and "doc.md — added" in shar_log
    assert "wrote both" not in priv_log and "wrote both" not in shar_log
    for repo_dir in (private, shared):
        body = subprocess.run(["git", "log", "-1", "--format=%b"], cwd=str(repo_dir), capture_output=True, text=True).stdout
        assert "wrote both" in body


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
