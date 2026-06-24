"""worker.py — the in-container agent harness (the ``vexa-agent`` image entrypoint).

Runs INSIDE a runtime-spawned, ISOLATED container (agents never run in the control plane). It reads
its dispatch from env — the mounted workspace, the minted token, ``REDIS_URL`` + the ``unit:<id>:in/out``
Stream topics, the ``start`` — runs the claude turn over the mounted ``/workspace`` via the SAME governed
``run_unit_turn`` (workspace.v1 revalidate + commit) the control plane proved, and ``XADD``s each
UnitEvent to its output Stream. Then it blocks on the input Stream for the next message (chat
continuity) until idle — TTL-on-idle by the harness. Continuity is the **session file** in the
workspace, so a reaped+respawned container resumes instantly.

The redis loop is factored into ``serve()`` with the turn-runner INJECTED, so it is offline-provable
with a fake redis + a fake turn (no docker, no claude).
"""
from __future__ import annotations

import itertools
import json
import os
import subprocess
from pathlib import Path
from typing import Callable, Iterator, Protocol

from .decision_claude import run_unit_turn

# A turn-runner: given a prompt, yield the turn's UnitEvents (message-delta/tool-call/commit/...).
TurnFn = Callable[[str], Iterator[dict]]


class _Stream(Protocol):
    """The slice of redis the harness needs (XADD out, XREAD in) — a fake satisfies it in tests."""

    def xadd(self, name: str, fields: dict) -> str: ...
    def xread(self, streams: dict, count: int = 1, block: int | None = None) -> list: ...


# ── the claude turn over the mounted workspace (reuses the governed run_unit_turn) ───────────────

def _exec_claude(argv: list[str], cwd: str) -> Iterator[str]:
    proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    assert proc.stdout is not None
    try:
        yield from proc.stdout
    finally:
        proc.wait()


def _ensure_repo(work: Path) -> None:
    """The mounted workspace folder is a git repo (continuity + the governance undo). If the bind is
    empty (first dispatch for this subject), initialize it with a seed commit so ``run_unit_turn`` has a
    HEAD to commit onto. Idempotent."""
    work.mkdir(parents=True, exist_ok=True)
    if (work / ".git").exists():
        return
    for args in (
        ("init", "-q"),
        ("config", "user.email", "agent@vexa"),
        ("config", "user.name", "vexa-agent"),
    ):
        subprocess.run(["git", *args], cwd=str(work), check=True, capture_output=True, text=True)
    if not (work / "CLAUDE.md").exists():
        (work / "CLAUDE.md").write_text("# Workspace\n\nThe agent's durable memory — knowledge, tasks, notes as files.\n")
    subprocess.run(["git", "add", "-A"], cwd=str(work), check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed", "--allow-empty"], cwd=str(work), check=True, capture_output=True, text=True)


def run_turn_over_workspace(work: Path, prompt: str, *, model: str | None = None) -> Iterator[dict]:
    """One governed claude turn over the mounted workspace: resume from the session file, run
    ``run_unit_turn`` (which revalidates entity writes vs workspace.v1 and commits), and persist the
    captured session id. A stale ``--resume`` (the server session expired) retries fresh once."""
    _ensure_repo(work)
    sess_file = work / ".claude" / ".session"
    resume = sess_file.read_text().strip() if sess_file.exists() else None
    allowed = ["Read", "Write", "Edit"]
    gen = run_unit_turn(str(work), prompt, _exec_claude, allowed_tools=allowed, session=resume, model=model)
    first = next(gen, None)
    if resume and first is not None and first.get("type") == "done" and not first.get("ok", True):
        if sess_file.exists():
            sess_file.unlink()
        gen = run_unit_turn(str(work), prompt, _exec_claude, allowed_tools=allowed, session=None, model=model)
        first = next(gen, None)
    captured: str | None = None
    for ev in (gen if first is None else itertools.chain([first], gen)):
        if ev.get("type") == "done" and ev.get("sessionId"):
            captured = ev["sessionId"]
        yield ev
    if captured:
        (work / ".claude").mkdir(parents=True, exist_ok=True)
        sess_file.write_text(captured)


def start_prompt(start: dict) -> str | None:
    """The first prompt from the dispatch ``start`` — an inline ask, a plan path, or None (session-only)."""
    ep = start.get("entrypoint") or {}
    if ep.get("inline"):
        return ep["inline"]
    if ep.get("path"):
        return f"Read and execute the plan at {ep['path']}."
    return None  # a session start serves the input Stream with no first prompt


# ── the harness loop (redis + the turn injected) ─────────────────────────────────────────────────

def serve(stream: _Stream, *, out_topic: str, in_topic: str, turn: TurnFn, start: dict, idle_ms: int) -> None:
    """Run the entrypoint turn (if any), then serve interactive messages on ``in_topic`` until idle.

    Each turn's UnitEvents are XADD'd to ``out_topic`` (tagged with a turn id), followed by a
    ``turn-complete`` marker. An empty blocking read (idle) returns — the process exits and the
    container is reaped (TTL-on-idle). A ``{"type":"stop"}`` message exits immediately.
    """
    def run_message(prompt: str, turn_id: str) -> None:
        for ev in turn(prompt):
            stream.xadd(out_topic, {"event": json.dumps({**ev, "turn_id": turn_id})})
        stream.xadd(out_topic, {"event": json.dumps({"type": "turn-complete", "turn_id": turn_id})})

    first = start_prompt(start)
    if first:
        run_message(first, "t0")

    last = "$"
    n = 0
    while True:
        resp = stream.xread({in_topic: last}, count=1, block=idle_ms)
        if not resp:
            return  # idle → exit 0 → container reaped
        for _name, entries in resp:
            for entry_id, fields in entries:
                last = entry_id
                msg = json.loads(fields.get("turn", "{}"))
                if msg.get("type") == "stop":
                    return
                n += 1
                run_message(msg.get("prompt", ""), f"t{n}")


def main() -> None:  # pragma: no cover — the container entrypoint (wired in tests via serve())
    import redis

    work = Path(os.environ.get("VEXA_WORKSPACE_PATH", "/workspace"))
    model = os.environ.get("VEXA_AGENT_MODEL") or None
    client = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    serve(
        client,
        out_topic=os.environ["VEXA_UNIT_OUT_TOPIC"],
        in_topic=os.environ["VEXA_UNIT_IN_TOPIC"],
        turn=lambda prompt: run_turn_over_workspace(work, prompt, model=model),
        start=json.loads(os.environ.get("VEXA_START", "{}")),
        idle_ms=int(os.environ.get("VEXA_IDLE_TIMEOUT_SEC", "120")) * 1000,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
