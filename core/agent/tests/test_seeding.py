"""seeding — the 'passes checks' gate + the single workspace-seeding primitive (template copy → git init).
Preserves the coverage the retired chat_runner._ensure_workspace carried."""
from __future__ import annotations

from shared.seeding import REQUIRED_SEED_PATHS, seed_workspace, validate_seed

def test_validate_seed_requires_claude_md(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    assert validate_seed(seed)                      # missing CLAUDE.md → problems
    (seed / "CLAUDE.md").write_text("root\n")
    assert validate_seed(seed) == []                # now valid
    assert REQUIRED_SEED_PATHS == ("CLAUDE.md",)


def test_validate_seed_rejects_non_directory(tmp_path):
    assert validate_seed(tmp_path / "nope")         # does not exist → problem


def test_seed_workspace_copies_template_and_inits_git(tmp_path):
    seed = tmp_path / "seed"
    (seed / "agents").mkdir(parents=True)
    (seed / "CLAUDE.md").write_text("conventions\n")
    (seed / "agents" / "meeting.md").write_text("cfg\n")

    ws = tmp_path / "ws" / "u1"
    seed_workspace(ws, seed)

    assert (ws / ".git").exists()
    assert (ws / "CLAUDE.md").read_text() == "conventions\n"
    assert (ws / "agents" / "meeting.md").exists()   # nested template dirs copied


def test_seed_workspace_is_idempotent_on_existing_repo(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "CLAUDE.md").write_text("root\n")
    ws = tmp_path / "ws" / "u1"
    seed_workspace(ws, seed)
    seed_workspace(ws, seed)                          # second call no-ops (repo already exists)
    assert (ws / ".git").exists()


# ── Phase 3: engine._ensure_repo now seeds from the validated template (not an inline CLAUDE.md) ──
def test_ensure_repo_seeds_from_template(tmp_path, monkeypatch):
    from worker.engine import _ensure_repo
    seed = tmp_path / "seed"
    (seed / "agents").mkdir(parents=True)
    (seed / "CLAUDE.md").write_text("template root\n")
    (seed / "agents" / "meeting.md").write_text("cfg\n")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(seed))

    ws = tmp_path / "ws"
    _ensure_repo(ws)
    assert (ws / ".git").exists()
    assert (ws / "CLAUDE.md").read_text() == "template root\n"   # from the TEMPLATE, not the fallback
    assert (ws / "agents" / "meeting.md").exists()                # full template tree copied


def test_ensure_repo_falls_back_when_no_template(tmp_path, monkeypatch):
    from worker.engine import _ensure_repo
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(tmp_path / "missing"))
    ws = tmp_path / "ws"
    _ensure_repo(ws)
    assert (ws / ".git").exists()
    assert "durable memory" in (ws / "CLAUDE.md").read_text()     # bare fallback memory root
