"""chat_runner.py — the MVP0 chat runner: a real claude turn over a per-subject git workspace.

Runs `claude -p --output-format stream-json` as a subprocess (claude-code is installed in this image;
the subscription is bind-mounted at ~/.claude). The per-subject workspace is a local git repo seeded
from the workspace template (CLAUDE.md + conventions). `run_unit_turn` enforces the workspace.v1
governance (non-conformant entity writes reverted) and commits. Session continuity via
`.claude/.session`.

MVP0 simplification: the turn runs in the control-plane container with a per-subject workspace dir.
Per-person container ISOLATION via the runtime kernel (one warm runtime.v1 workload per person,
docker-exec'd) is the MVP1 hardening — see DECISIONS.md.
"""
from __future__ import annotations

import itertools
import shutil
import subprocess
from pathlib import Path
from typing import Iterator, Optional

from .decision_claude import run_unit_turn


class SubprocessChatRunner:
    def __init__(self, workspaces_dir: str, *, seed_dir: Optional[str] = None, model: Optional[str] = None) -> None:
        self._root = Path(workspaces_dir)
        self._seed = Path(seed_dir) if seed_dir else None
        self._model = model

    def _ensure_workspace(self, subject: str) -> Path:
        ws = self._root / subject
        if (ws / ".git").exists():
            return ws
        ws.mkdir(parents=True, exist_ok=True)
        if self._seed and self._seed.exists():
            for item in self._seed.iterdir():
                dst = ws / item.name
                if item.is_dir():
                    shutil.copytree(item, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dst)
        for args in (("init", "-q"), ("config", "user.email", "agent@vexa"), ("config", "user.name", "vexa-agent")):
            subprocess.run(["git", *args], cwd=str(ws), check=True, capture_output=True, text=True)
        subprocess.run(["git", "add", "-A"], cwd=str(ws), check=True, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-q", "-m", "seed", "--allow-empty"], cwd=str(ws), check=True, capture_output=True, text=True)
        return ws

    def _exec_claude(self, argv: list[str], cwd: str) -> Iterator[str]:
        proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                yield line
        finally:
            proc.wait()

    def _turn(self, ws: Path, prompt: str, session: Optional[str]) -> Iterator[dict]:
        return run_unit_turn(str(ws), prompt, self._exec_claude, session=session, model=self._model)

    def run(self, prompt: str, *, subject: str, session: Optional[str] = None) -> Iterator[dict]:
        ws = self._ensure_workspace(subject)
        sess_file = ws / ".claude" / ".session"
        resume = session or (sess_file.read_text().strip() if sess_file.exists() else None)
        captured: Optional[str] = None

        gen = self._turn(ws, prompt, resume)
        first = next(gen, None)
        # A stale --resume (e.g. the session store was lost on a container recreate) fails immediately:
        # the first event is a `done` with ok=false and no content. Drop the session and retry fresh.
        if resume and first is not None and first.get("type") == "done" and not first.get("ok", True):
            if sess_file.exists():
                sess_file.unlink()
            gen = self._turn(ws, prompt, None)
            first = next(gen, None)

        stream = gen if first is None else itertools.chain([first], gen)
        for ev in stream:
            if ev.get("type") == "done" and ev.get("sessionId"):
                captured = ev["sessionId"]
            yield ev
        if captured:
            (ws / ".claude").mkdir(parents=True, exist_ok=True)
            (ws / ".claude" / ".session").write_text(captured)
