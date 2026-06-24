"""decision_claude.py — the claude-in-container unit turn (the LLM seam, tool-using path).

`ports.AgentDecisionPort` is the deterministic transcript→single-write path. THIS is the tool-using
path: a real Claude Code agent runs over the mounted per-person workspace and writes entities itself
via Read/Write/Edit/Bash. Because the model writes files directly (NOT through `WorkspacePort.write`),
the governance gate moves to a **post-write re-validation**: every changed `kg/entities/**.md` is
validated against `workspace.v1` before the commit is allowed; a non-conformant write is **reverted**,
not committed — a hallucinating model cannot land a bad entity (P8).

This is the proven `bbb:~/dev/quorum` pattern (`claude -p --allowedTools --resume`,
`--output-format stream-json` → SSE), designed to the v0.12 seams. The subprocess/`docker exec` is an
INJECTED runner (`ClaudeExec`), so the parser + the governance are offline-provable with a fake.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from . import contracts
from .adapters import _git, parse_entity

# A runner: given an argv + a cwd, yield the process's stdout lines (claude stream-json JSONL).
ClaudeExec = Callable[[list[str], str], Iterable[str]]

_ENTITY_RE = re.compile(r"^kg/entities/.+\.md$")


def _short(content: object, n: int = 80) -> str:
    s = content if isinstance(content, str) else json.dumps(content, default=str)
    s = " ".join(s.split())
    return s[:n]


def parse_stream_json(lines: Iterable[str]) -> Iterator[dict]:
    """Normalize Claude Code `--output-format stream-json` JSONL into UnitEvent dicts.

    assistant text → message-delta · assistant tool_use → tool-call · user tool_result →
    tool-result · result → done. Malformed lines are skipped (fail-soft on the wire, P18 keeps the
    structured ones).
    """
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        t = obj.get("type")
        if t == "assistant":
            for block in obj.get("message", {}).get("content", []) or []:
                bt = block.get("type")
                if bt == "text" and block.get("text"):
                    yield {"type": "message-delta", "text": block["text"]}
                elif bt == "tool_use":
                    yield {
                        "type": "tool-call",
                        "tool": block.get("name", ""),
                        "args": block.get("input", {}),
                        "callId": block.get("id", ""),
                    }
        elif t == "user":
            for block in obj.get("message", {}).get("content", []) or []:
                if block.get("type") == "tool_result":
                    yield {
                        "type": "tool-result",
                        "callId": block.get("tool_use_id", ""),
                        "ok": not block.get("is_error", False),
                        "summary": _short(block.get("content")),
                    }
        elif t == "result":
            yield {
                "type": "done",
                "reply": obj.get("result", ""),
                "sessionId": obj.get("session_id"),
                "ok": obj.get("is_error") is not True and obj.get("subtype") != "error",
            }


def changed_entity_files(work_dir: str | Path) -> list[str]:
    """The `kg/entities/**.md` paths changed in the working tree vs HEAD (porcelain).

    ``--untracked-files=all`` is REQUIRED: without it git collapses a fully-untracked directory to a
    single ``?? kg/`` line, hiding the individual new entity files the model just wrote.
    """
    out = _git(Path(work_dir), "status", "--porcelain", "--untracked-files=all")
    paths: list[str] = []
    for line in out.splitlines():
        p = line[3:].strip() if len(line) > 3 else ""
        if " -> " in p:  # rename: XY orig -> new
            p = p.split(" -> ", 1)[1]
        p = p.strip().strip('"')
        if _ENTITY_RE.match(p):
            paths.append(p)
    return paths


def revalidate_entities(work_dir: str | Path, paths: Optional[Iterable[str]] = None) -> list[tuple[str, str]]:
    """Validate each changed entity's frontmatter against workspace.v1. Returns (path, error) violations."""
    work = Path(work_dir)
    targets = list(paths) if paths is not None else changed_entity_files(work)
    violations: list[tuple[str, str]] = []
    for rel in targets:
        f = work / rel
        if not f.exists():  # deleted — nothing to validate
            continue
        frontmatter, _body = parse_entity(f.read_text())
        try:
            contracts.validate_entity_frontmatter(frontmatter)
        except Exception as e:  # jsonschema ValidationError — surface the path + reason (P18)
            violations.append((rel, str(e).splitlines()[0]))
    return violations


def build_argv(
    prompt: str,
    *,
    allowed_tools: Iterable[str] = (),
    session: Optional[str] = None,
    model: Optional[str] = None,
    mcp_config: Optional[str] = None,
) -> list[str]:
    """The headless Claude Code argv — `claude -p <prompt> --output-format stream-json [...]`.

    `--permission-mode acceptEdits` auto-accepts Read/Edit/Write so the turn runs fully headless; the
    `--allowedTools` scope is the capability gate (the model writes entities, the governance layer in
    `run_unit_turn` does the git commit). `--mcp-config <file>` + `--strict-mcp-config` attach EXACTLY
    the unit's granted MCP tools (the toolbelt) and nothing else. The container sandbox + post-write
    re-validation are the other two enforcement layers.
    """
    argv = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
            "--permission-mode", "acceptEdits"]
    tools = list(allowed_tools)
    if tools:
        argv += ["--allowedTools", ",".join(tools)]
    if mcp_config:
        argv += ["--mcp-config", mcp_config, "--strict-mcp-config"]
    if session:
        argv += ["--resume", session]
    if model:
        argv += ["--model", model]
    return argv


def run_unit_turn(
    work_dir: str | Path,
    prompt: str,
    exec_claude: ClaudeExec,
    *,
    allowed_tools: Iterable[str] = ("Read", "Write", "Edit"),
    session: Optional[str] = None,
    model: Optional[str] = None,
    mcp_config: Optional[str] = None,
    commit_message: Optional[str] = None,
    commit: bool = True,
) -> Iterator[dict]:
    """Run one claude turn over ``work_dir``, streaming normalized UnitEvents, then ENFORCE governance.

    After the turn: re-validate every changed entity against workspace.v1. On any violation → revert
    the working tree and emit ``{"type":"rejected", "violations":[...]}`` (nothing is committed). Else,
    if the tree changed, commit and emit ``{"type":"commit","sha":...}``. The model's raw file writes
    never escape the contract gate.
    """
    work = Path(work_dir)
    argv = build_argv(prompt, allowed_tools=allowed_tools, session=session, model=model, mcp_config=mcp_config)
    done: Optional[dict] = None
    for ev in parse_stream_json(exec_claude(argv, str(work))):
        if ev.get("type") == "done":
            done = ev
        yield ev

    if not commit:
        # propose-only / read-only turn (e.g. the meeting copilot): it writes nothing, so do NO git —
        # never touch a workspace another agent may be committing to (the index.lock collision).
        return

    violations = revalidate_entities(work)
    if violations:
        # Revert EVERY write of this turn — tracked (reset) and untracked (clean) — so a rejected turn
        # leaves the repo exactly at HEAD. Keep `.claude` (session continuity for the next turn).
        _git(work, "reset", "--hard", "HEAD")
        _git(work, "clean", "-fd", "-e", ".claude")
        yield {"type": "rejected", "violations": violations}
        return

    if _git(work, "status", "--porcelain"):
        _git(work, "add", "-A")
        msg = commit_message or ((done or {}).get("reply") or "agent turn")
        _git(work, "commit", "-m", msg.splitlines()[0][:72] if msg else "agent turn")
        yield {"type": "commit", "sha": _git(work, "rev-parse", "HEAD")}
