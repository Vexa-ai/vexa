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
