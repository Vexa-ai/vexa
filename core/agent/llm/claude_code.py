"""claude_code.py — the Claude Code harness ADAPTER (vendor-named like runtime's docker_backend.py).

Everything this codebase knows about the ``claude`` CLI lives in THIS file: the headless argv, the
``--output-format stream-json`` parser, the ``~/.claude`` continuity/skills wiring, and the
Anthropic-credential preflight. The rest of the system sees only ``HarnessPort`` UnitEvents.

This is the proven ``claude -p --allowedTools --resume`` pattern (stream-json → SSE). The
subprocess is an INJECTED runner (``HarnessExec``), so the parser is offline-provable with a fake.

Credentials: ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN`` / ``ANTHROPIC_BASE_URL`` (or the
``HOST_CLAUDE_CREDENTIALS`` subscription mount brokered by the runtime) — this adapter's concern
only; other runners declare their own.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Iterable, Iterator, Optional

from llm.errors import looks_like_auth_failure, preflight_provider_guard
from llm.ports import HarnessExec, harness_subprocess_env


# Tools whose SUCCESS means a document now exists that the person should be looking at. The
# vocabulary is explicit rather than a prefix match: "a tool whose name contains write" would catch
# a future `workspace_write_policy` or a `write_transcript` and open tabs nobody asked for.
_WRITER_TOOLS = frozenset({
    "mcp__vexa__workspace_write",
    "Write",
    "Edit",
    "NotebookEdit",
})


def _written_artifact(tool: str, args: dict) -> "tuple[str, str] | None":
    """`(workspace, path)` the call is about to write, or None. Read off the ARGUMENTS, at tool-use
    time, because the result carries only a summary string.

    Two dialects, because two kinds of tool write workspace files:
      * the MCP verb takes `path` (workspace-relative) and `slug` (empty = the caller's own desk);
      * the harness tools take an absolute container path under `/workspaces/<slug>/<rel>`.
    Anything else — a write outside the store, a shape we do not recognise — returns None and no
    tab is opened. A tab pointing at a path we guessed is worse than no tab: it opens a page that
    can never load, which is the failure the scaffold's `meeting:note` rule already names."""
    if tool == "mcp__vexa__workspace_write":
        rel = str(args.get("path") or "").strip().lstrip("/")
        slug = str(args.get("slug") or "").strip()
        return (slug, rel) if rel else None
    raw = str(args.get("file_path") or args.get("notebook_path") or "").strip()
    if not raw.startswith("/workspaces/"):
        return None
    rest = raw[len("/workspaces/"):]
    slug, _, rel = rest.partition("/")
    return (slug, rel) if slug and rel else None


def _short(content: object, n: int = 80) -> str:
    s = content if isinstance(content, str) else json.dumps(content, default=str)
    s = " ".join(s.split())
    return s[:n]


def parse_stream_json(lines: Iterable[str]) -> Iterator[dict]:
    """Normalize Claude Code `--output-format stream-json` JSONL into UnitEvent dicts.

    assistant text → message-delta · assistant tool_use → tool-call · user tool_result →
    tool-result · result → done. Malformed lines are skipped (fail-soft on the wire, P18 keeps the
    structured ones).

    With ``--include-partial-messages`` the stream also carries ``stream_event`` lines wrapping the
    Anthropic streaming events; each ``content_block_delta`` with ``delta.type=="text_delta"`` becomes
    an INCREMENTAL message-delta so the UI renders token-by-token. When partial deltas have been
    emitted, the consolidated full ``text`` block on the trailing ``assistant`` message is SUPPRESSED
    (else the prose doubles). The ``result`` event still carries the full ``reply``.
    """
    streamed_partial = False  # saw any text_delta → don't re-emit the consolidated assistant text
    # callId -> (workspace, path) for writes still in flight. Per-stream, so a call id can never
    # collide across turns, and popped on the matching result so nothing accumulates.
    pending_writes: dict[str, tuple[str, str]] = {}
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        t = obj.get("type")
        if t == "stream_event":
            event = obj.get("event", {}) or {}
            if event.get("type") == "content_block_delta":
                delta = event.get("delta", {}) or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    streamed_partial = True
                    yield {"type": "message-delta", "text": delta["text"]}
        elif t == "assistant":
            for block in obj.get("message", {}).get("content", []) or []:
                bt = block.get("type")
                if bt == "text" and block.get("text"):
                    if not streamed_partial:  # no partials → emit the whole block (back-compat)
                        yield {"type": "message-delta", "text": block["text"]}
                elif bt == "tool_use":
                    tool_name = block.get("name", "")
                    call_id = block.get("id", "")
                    # THE PANEL FOLLOWS THE WRITE, and the record is what makes it follow (decision
                    # 18: layout is a function of the chat's state, never of the client's guess).
                    # The founder watched the agent create a shared workspace and write its README
                    # while the panel sat on `_global/README.md` — the document it had just made was
                    # the one thing not on screen. The argument names the file; remember it now,
                    # because the tool RESULT carries only a summary string and by then the path is
                    # gone.
                    if tool_name in _WRITER_TOOLS:
                        target = _written_artifact(tool_name, block.get("input", {}) or {})
                        if target:
                            pending_writes[call_id] = target
                    yield {
                        "type": "tool-call",
                        "tool": tool_name,
                        "args": block.get("input", {}),
                        "callId": call_id,
                    }
        elif t == "user":
            for block in obj.get("message", {}).get("content", []) or []:
                if block.get("type") == "tool_result":
                    call_id = block.get("tool_use_id", "")
                    ok = not block.get("is_error", False)
                    yield {
                        "type": "tool-result",
                        "callId": call_id,
                        "ok": ok,
                        "summary": _short(block.get("content")),
                    }
                    # ONLY ON SUCCESS. A failed write must not open a tab: the file is not there,
                    # and a tab on a path that does not exist is exactly the "page that can never
                    # load" this stream is careful about elsewhere. `pop` either way, so a failed
                    # call cannot leave an entry that a later, unrelated result matches.
                    target = pending_writes.pop(call_id, None)
                    if target and ok:
                        workspace, path = target
                        yield {
                            "type": "artifact",
                            "workspace": workspace,
                            "path": path,
                            "focus": True,
                        }
        elif t == "result":
            reply = obj.get("result", "")
            done = {
                "type": "done",
                "reply": reply,
                "sessionId": obj.get("session_id"),
                "ok": obj.get("is_error") is not True and obj.get("subtype") != "error",
            }
            if not done["ok"] and looks_like_auth_failure(reply):
                # The CLI's own auth text ("Not logged in · Please run /login") is an internal of
                # THIS adapter — /login doesn't exist for an API consumer. Rewrite to the
                # platform-actionable message; the raw text rides along in `detail` (additive).
                done["detail"] = _short(reply, 200)
                done["reply"] = (
                    "Model credentials are missing or expired for this deployment. "
                    "Set or refresh one of HOST_CLAUDE_CREDENTIALS, ANTHROPIC_API_KEY, "
                    "ANTHROPIC_AUTH_TOKEN, CLAUDE_CODE_OAUTH_TOKEN or VEXA_LLM_API_KEY, "
                    "or configure a model under Settings → Models."
                )
            yield done


def build_argv(
    prompt: str,
    *,
    allowed_tools: Iterable[str] = (),
    session: Optional[str] = None,
    model: Optional[str] = None,
    mcp_config: Optional[str] = None,
    stdin_mode: bool = False,
    effort: Optional[str] = None,
) -> list[str]:
    """The headless Claude Code argv — `claude -p <prompt> --output-format stream-json [...]`.

    `--permission-mode acceptEdits` auto-accepts Read/Edit/Write so the turn runs fully headless; the
    `--allowedTools` scope is the capability gate (the model writes entities, `run_harness_turn`
    does the git commit). `--mcp-config <file>` + `--strict-mcp-config` attach EXACTLY the unit's
    granted MCP tools (the toolbelt) and nothing else. The container sandbox is the other
    enforcement layer.

    `effort` — when set — pins the session's reasoning effort (`--effort low|medium|high|xhigh`).
    Backends that validate the OpenAI-compatible `reasoning_effort` field (e.g. vLLM/LiteLLM model
    groups) reject the CLI's default `high` when it is outside their allowlist; an explicit value
    overrides that default. Unset ⇒ no flag ⇒ the CLI's own behaviour, unchanged.
    """
    if stdin_mode:
        # prompt travels via stdin (stream-json) so the pipe stays open for mid-turn injection
        argv = ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                "--verbose", "--include-partial-messages", "--permission-mode", "acceptEdits"]
    else:
        argv = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
                "--include-partial-messages", "--permission-mode", "acceptEdits"]
    tools = list(allowed_tools)
    if tools:
        argv += ["--allowedTools", ",".join(tools)]
    if mcp_config:
        argv += ["--mcp-config", mcp_config, "--strict-mcp-config"]
    if session:
        argv += ["--resume", session]
    if model:
        argv += ["--model", model]
    if effort:
        argv += ["--effort", effort]
    return argv


# ── mid-turn injection (VEXA_MIDTURN_INJECT=1) ────────────────────────────────────
# In stdin mode the CLI keeps reading `--input-format stream-json` user messages while a turn runs —
# a message written here joins the CURRENT turn (the engine polls the unit in-stream between output
# events and calls inject_user_message). The mailbox is module-level because the injection point
# (engine.serve) sits four frozen contracts away from the subprocess handle.
import threading as _threading

_STDIN_LOCK = _threading.Lock()
_ACTIVE_STDIN = None  # the running turn's proc.stdin, when stdin mode is active


def midturn_enabled() -> bool:
    return os.environ.get("VEXA_MIDTURN_INJECT", "") == "1"


def _user_message_json(text: str) -> str:
    return json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}})


def inject_user_message(text: str) -> bool:
    """Write a user message into the RUNNING turn's stdin. False = no active stdin (caller should
    leave the message queued for the between-turns loop instead)."""
    with _STDIN_LOCK:
        w = _ACTIVE_STDIN
        if w is None:
            return False
        try:
            w.write(_user_message_json(text) + "\n")
            w.flush()
            return True
        except Exception:  # noqa: BLE001 — a closing pipe just means the turn is ending
            return False


def _reap_grace() -> float:
    """How long a finished-with stdout is given to bring the CLI down on its own, before it is
    killed. Tunable only so a test can prove the kill path in a fraction of a second."""
    try:
        return float(os.environ.get("VEXA_HARNESS_REAP_GRACE_SEC", "5"))
    except ValueError:
        return 5.0


def _reap(proc, grace: "float | None" = None) -> None:
    """Wait for the CLI, then KILL it if it will not go.

    ⚠ `finally: proc.wait()` alone is a HANG waiting to happen, and it became reachable the moment a
    caller could stop consuming early (the write-back phase's budget closes the generator, which
    raises GeneratorExit at the yield and runs this finally while the CLI is still mid-turn). A bare
    wait there blocks the worker forever on a process nobody is reading any more — the budget would
    have produced a permanent stall in place of the temporary one it exists to remove.

    ⚠ AND CLOSING STDOUT IS NOT ENOUGH, which is the version of this that looked fine. A child that
    keeps writing dies of SIGPIPE the moment the pipe closes, so the first test of this passed with
    the kill path deleted. A child that has stopped writing — which is what the CLI is doing for
    most of a turn, waiting on a model — never notices, and waits out the whole budget's worth of
    nothing. The kill is for that one, and the test now uses a child that sleeps.

    On the normal path the CLI has already exited by the time stdout hits EOF, so the grace costs
    nothing."""
    grace = _reap_grace() if grace is None else grace
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    except TypeError:
        # a Popen-shaped test double whose wait() takes no timeout — the seam, not the CLI
        proc.wait()


def _exec_subprocess_stdin(argv: list[str], cwd: str, first_message: str) -> Iterator[str]:
    """stdin-mode exec: the prompt travels as the first stream-json user message and stdin STAYS
    OPEN for mid-turn injection; a `result` line closes it (turn over → CLI exits)."""
    global _ACTIVE_STDIN
    proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1,
                            env=harness_subprocess_env())
    assert proc.stdout is not None and proc.stdin is not None
    try:
        proc.stdin.write(_user_message_json(first_message) + "\n")
        proc.stdin.flush()
        with _STDIN_LOCK:
            _ACTIVE_STDIN = proc.stdin
        for line in proc.stdout:
            yield line
            if '"type":"result"' in line or '"type": "result"' in line:
                with _STDIN_LOCK:
                    _ACTIVE_STDIN = None
                try:
                    proc.stdin.close()
                except Exception:  # noqa: BLE001
                    pass
    finally:
        with _STDIN_LOCK:
            _ACTIVE_STDIN = None
        try:
            proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        _reap(proc)


def _exec_subprocess(argv: list[str], cwd: str) -> Iterator[str]:
    # harness_subprocess_env: the model's Bash tool runs INSIDE this subprocess, so it must not inherit
    # the worker's data-plane secrets — ``REDIS_URL`` (which would let Bash reach the shared redis and
    # read/write another tenant's tc:meeting:* / unit:*:in streams, crossing the tenancy boundary the
    # mounts enforce on the filesystem) nor the minted per-dispatch bearer token. It also drops the
    # git repo-discovery redirects (a hook-exported GIT_DIR would re-point the workspace's git ops).
    proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                            env=harness_subprocess_env())
    assert proc.stdout is not None
    try:
        yield from proc.stdout
    finally:
        try:
            proc.stdout.close()
        except Exception:  # noqa: BLE001
            pass
        _reap(proc)


def _link_chat_into_workspace(work: Path) -> None:
    """Save + resume chats FROM THE WORKSPACE. claude-code stores a conversation's transcript at
    ``~/.claude/projects/<cwd-slug>/<session>.jsonl`` — inside the container, so it is wiped when the
    per-turn container is recreated (no memory). Symlink that dir into the workspace's ``.claude/projects``
    so the chat is written to the durable git folder and ``--resume`` reads it back across turns. We keep
    it under ``.claude`` (excluded from the governance ``git clean``) so a rejected turn never wipes the
    history; it persists on the workspace volume.

    SAFETY: only the disposable per-turn container HOME may be rewritten. Outside the container
    (a host test run, a developer shell) ``~/.claude/projects`` holds the developer's REAL session
    transcripts — this function must never delete data it didn't create. A pre-existing directory
    is therefore replaced only when EMPTY (``rmdir``, which cannot destroy content); a non-empty
    one is left alone and the link is skipped — the turn still works, without cross-turn resume."""
    ws_projects = work / ".claude" / "projects"
    ws_projects.mkdir(parents=True, exist_ok=True)
    home_claude = Path(os.environ.get("HOME", "/root")) / ".claude"
    home_claude.mkdir(parents=True, exist_ok=True)
    link = home_claude / "projects"
    try:
        if link.is_symlink():
            if os.readlink(link) == str(ws_projects):
                return
            link.unlink()
        elif link.is_dir():
            if any(link.iterdir()):
                return  # real transcripts live here — never delete, skip the link
            link.rmdir()  # empty dir: safe to replace, nothing can be lost
        elif link.exists():
            return  # some other filesystem object — don't clobber
        link.symlink_to(ws_projects, target_is_directory=True)
    except OSError:
        pass  # best-effort; a fresh turn still works, just without cross-turn resume


def _link_skills_into_workspace(work: Path) -> None:
    """Expose the user's GOVERNED skills to the CLI. Skills live as VISIBLE, git-tracked files under the
    workspace's ``skills/<name>/SKILL.md`` (the ``skills/`` tree mirrors the ``agents/`` config home —
    not a dotfile, so it shows in the Files surface and is committed). claude-code auto-discovers skills
    from ``.claude/skills``, which is governance-excluded; so we point ``.claude/skills`` at the real
    ``skills/`` dir via a symlink. The real files stay durable + committed; the CLI finds them through
    the link. Idempotent: create ``skills/`` if absent, then (re)point a stale/wrong symlink — but never
    clobber a real ``.claude/skills`` directory."""
    skills = work / "skills"
    link = work / ".claude" / "skills"
    try:
        # The two mkdirs are INSIDE the guard on purpose. This function is documented best-effort —
        # "the turn still works, just without workspace skills" — but the directory creation used to
        # sit outside it, so a cwd bound READ-ONLY (the post-meeting room run, where the ruling is
        # that the turn writes no desk) raised an uncaught OSError and killed the turn during
        # PREPARE, before a single token. On a ro cwd whose seed already carries `skills/` both
        # mkdirs are no-ops and the link is found already correct; on one that does not, we now skip
        # exactly as the docstring always promised.
        skills.mkdir(parents=True, exist_ok=True)
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            if os.readlink(link) == str(skills):
                return
            link.unlink()
        elif link.exists():
            return  # a real dir already there — don't clobber
        link.symlink_to(skills, target_is_directory=True)
    except OSError:
        pass  # best-effort; the turn still works, just without workspace skills


class ClaudeCodeHarness:
    """``HarnessPort`` adapter for the Claude Code CLI. ``exec_fn`` is injectable for tests."""

    name = "claude-code"

    def __init__(self, exec_fn: Optional[HarnessExec] = None) -> None:
        self._exec: HarnessExec = exec_fn or _exec_subprocess

    def run_turn(self, work: Path, prompt: str, *, allowed_tools: Iterable[str] = (),
                 session: Optional[str] = None, model: Optional[str] = None,
                 mcp_config: Optional[str] = None) -> Iterator[dict]:
        effort = os.environ.get("VEXA_AGENT_EFFORT") or None
        if midturn_enabled() and self._exec is _exec_subprocess:
            argv = build_argv(prompt, allowed_tools=allowed_tools, session=session, model=model,
                              mcp_config=mcp_config, stdin_mode=True, effort=effort)
            yield from parse_stream_json(_exec_subprocess_stdin(argv, str(work), prompt))
        else:
            argv = build_argv(prompt, allowed_tools=allowed_tools, session=session, model=model,
                              mcp_config=mcp_config, effort=effort)
            yield from parse_stream_json(self._exec(argv, str(work)))

    def prepare(self, work: Path, chat_root: Optional[Path] = None) -> None:
        # chats are saved to / resumed from the PRIVATE continuity root (the _system mount when the
        # dispatch declares one — the flat model can make the cwd a SHARED workspace, and chats are
        # private), not ~/.claude; skills stay cwd-scoped (.claude/skills → <work>/skills)
        _link_chat_into_workspace(chat_root or work)
        _link_skills_into_workspace(work)

    def transcript_bytes(self, work: Path, session_id: str) -> int:
        total = 0
        for path in (work / ".claude" / "projects").glob(f"*/{session_id}.jsonl"):
            try:
                total += path.stat().st_size
            except OSError:
                continue
        return total

    def preflight(self) -> Optional[str]:
        return preflight_provider_guard()

    def midturn_enabled(self) -> bool:
        return midturn_enabled()

    def inject_user_message(self, text: str) -> bool:
        return inject_user_message(text)
