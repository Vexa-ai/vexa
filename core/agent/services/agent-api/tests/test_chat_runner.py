"""MVP0 chat-runner L2 test — over a fake claude exec (no real claude/subscription needed).

Proves: a per-subject workspace is seeded from the template + git-initialized; a turn that writes a
conformant entity commits (the governance passes); the session id is captured for resume.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_api.chat_runner import SubprocessChatRunner


def test_chat_runner_seeds_runs_and_commits(tmp_path: Path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "CLAUDE.md").write_text("conventions\n")
    wsroot = tmp_path / "ws"

    class FakeRunner(SubprocessChatRunner):
        def _exec_claude(self, argv, cwd):  # the "model" writes a conformant entity, then finishes
            f = Path(cwd) / "kg/entities/person/jane.md"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("---\nid: jane\ntitle: Jane\ntype: person\n---\nbody\n")
            yield json.dumps({"type": "result", "subtype": "success", "result": "wrote jane", "session_id": "S1"})

    r = FakeRunner(str(wsroot), seed_dir=str(seed))
    evs = list(r.run("create jane", subject="u1"))

    assert any(e["type"] == "commit" for e in evs)
    ws = wsroot / "u1"
    assert (ws / "CLAUDE.md").exists()                      # seeded from the template
    assert (ws / "kg/entities/person/jane.md").exists()     # the model's entity landed
    assert (ws / ".claude" / ".session").read_text() == "S1"  # session captured for resume


def test_chat_runner_recovers_from_stale_resume(tmp_path: Path):
    """A stale --resume (e.g. the session store was lost on a container recreate) fails immediately;
    the runner drops the bad session and retries fresh, so the turn still lands + a new session sticks."""
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "CLAUDE.md").write_text("conventions\n")
    wsroot = tmp_path / "ws"

    class FlakyResumeRunner(SubprocessChatRunner):
        def _exec_claude(self, argv, cwd):
            if "--resume" in argv:  # the stale session — claude errors instantly, writes nothing
                yield json.dumps({"type": "result", "subtype": "error", "is_error": True, "session_id": "STALE"})
                return
            f = Path(cwd) / "kg/entities/person/jane.md"  # fresh retry: the real turn writes + succeeds
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("---\nid: jane\ntitle: Jane\ntype: person\n---\nbody\n")
            yield json.dumps({"type": "result", "subtype": "success", "result": "wrote jane", "session_id": "S2"})

    r = FlakyResumeRunner(str(wsroot), seed_dir=str(seed))
    ws = r._ensure_workspace("u1")
    (ws / ".claude").mkdir(parents=True, exist_ok=True)
    (ws / ".claude" / ".session").write_text("STALE")  # plant the bad pointer

    evs = list(r.run("create jane", subject="u1"))

    assert any(e["type"] == "commit" for e in evs)               # the fresh retry committed
    assert not any(e["type"] == "done" and not e.get("ok", True) for e in evs)  # the failed attempt is swallowed
    assert (ws / "kg/entities/person/jane.md").exists()          # the entity landed on retry
    assert (ws / ".claude" / ".session").read_text() == "S2"     # stale session replaced
