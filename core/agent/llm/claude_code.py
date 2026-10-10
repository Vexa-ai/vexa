"""claude_code.py — the Claude Code harness ADAPTER (vendor-named like runtime's docker_backend.py).

Everything this codebase knows about the ``claude`` CLI lives in THIS file and its one companion:
the headless argv, the ``--output-format stream-json`` parser, the ``~/.claude`` continuity wiring
and the Anthropic-credential preflight here; the per-turn skills staging in ``claude_skills.py``.
What a tool result means to the panel is shared by every harness and lives in ``tool_events.py``.
The rest of the system sees only ``HarnessPort`` UnitEvents.

This is the proven ``claude -p --allowedTools --resume`` pattern (stream-json → SSE). The
subprocess is an INJECTED runner (``HarnessExec``), so the parser is offline-provable with a fake.

Credentials: ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN`` / ``ANTHROPIC_BASE_URL`` (or the
``HOST_CLAUDE_CREDENTIALS`` subscription mount brokered by the runtime) — this adapter's concern
only; other runners declare their own.
"""
from __future__ import annotations

import json
import logging
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Iterable, Iterator, Optional

from llm.errors import looks_like_auth_failure, preflight_provider_guard, provider_host
from llm import fault_wire
from llm import faults as provider_faults
from llm import workspace_paths as wpaths
from llm.ports import (HarnessExec, close_event_stream, harness_identity_kwargs, harness_subprocess_env,
                       max_output_tokens)
from llm.claude_skills import _link_skills_into_home
from llm.tool_events import (_BOT_TOOLS, _FOCUS_TOOLS, _OPEN_TOOLS, _TERMS_TOOLS, _WRITER_TOOLS,
                             _bot_artifact, _open_event, _published_terms, _short,
                             _workspace_focus, _written_artifact)

logger = logging.getLogger("llm.claude_code")


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
    # callIds of `transcript_terms` calls still in flight — the same per-stream,
    # popped-on-result discipline as `pending_writes`, so one turn's result can never be
    # matched to another call's id.
    pending_terms: set[str] = set()
    # callIds of in-flight `bot_send` calls — same per-stream, popped-on-result discipline.
    pending_bots: set[str] = set()
    # callIds of in-flight `open_page` calls — same discipline again. A turn may open twice (the
    # transcript, then the note) and a set keyed on the call id is what keeps the second answer from
    # being matched to the first ask.
    pending_opens: set[str] = set()
    # callIds of in-flight `workspace_new` calls — same discipline again (Vexa-ai/vexa#1603).
    pending_focus: set[str] = set()
    # THE PROVIDER'S FAILURE, WHEREVER THE CLI PUT IT (P18). The CLI reports a provider refusal —
    # OpenRouter's 402, out of credit — as a synthetic assistant message (`API Error: 402 {…}`,
    # sometimes labelled `error: "billing_error"`), then a `result` with `is_error`; and when it dies
    # before the stream starts it prints the reason as plain text, which used to be skipped as a
    # malformed line. All three are kept here, bounded, and read by `_provider_fault` below.
    api_error, sdk_error, model_id = "", "", ""
    stray: list[str] = []
    saw_result = False
    try:
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                obj = None
            if not isinstance(obj, dict):
                stray.append(raw[:500])      # plain stderr/stdout text: kept, never shown raw
                del stray[:-20]
                continue
            t = obj.get("type")
            if t == "system" and obj.get("subtype") == "init" and obj.get("model"):
                model_id = str(obj["model"])
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
                        if _is_api_error(obj, block["text"]):
                            # The provider's refusal, not the agent's words: it becomes the turn's
                            # typed fault below instead of a raw `API Error: 402 {json}` bubble.
                            api_error, sdk_error = block["text"], str(obj.get("error") or "")
                            continue
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
                        elif tool_name in _TERMS_TOOLS:
                            pending_terms.add(call_id)
                        elif tool_name in _BOT_TOOLS:
                            pending_bots.add(call_id)
                        elif tool_name in _OPEN_TOOLS:
                            pending_opens.add(call_id)
                        elif tool_name in _FOCUS_TOOLS:
                            pending_focus.add(call_id)
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
                        # THE CHIPS (decision 35). Same success-only rule as the artifact below:
                        # a failed read must not paint a transcript.
                        was_terms = call_id in pending_terms
                        pending_terms.discard(call_id)
                        if was_terms and ok:
                            ev = _published_terms(block.get("content"))
                            if ev:
                                yield ev
                        # THE BOT IS IN THE ROOM — open its transcript. Success-only, like the two
                        # above: a send that failed must not front a transcript that will stay empty.
                        was_bot = call_id in pending_bots
                        pending_bots.discard(call_id)
                        if was_bot and ok:
                            ev = _bot_artifact(block.get("content"))
                            if ev:
                                yield ev
                        # SOMEBODY ASKED TO SEE SOMETHING. Success-only like the three above, and
                        # the tool's own `opened: false` is a second refusal inside `_open_event`:
                        # "no transcript for this meeting" is a successful CALL whose answer is no.
                        was_open = call_id in pending_opens
                        pending_opens.discard(call_id)
                        if was_open and ok:
                            ev = _open_event(block.get("content"))
                            if ev:
                                yield ev
                        # A WORKSPACE CAME INTO THE ROOM. Success-only like the rest: a create
                        # that failed made no place, and a chip for a workspace that does not
                        # exist is the worst of the three failures on this seam.
                        was_focus = call_id in pending_focus
                        pending_focus.discard(call_id)
                        if was_focus and ok:
                            ev = _workspace_focus(block.get("content"))
                            if ev:
                                yield ev
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
                saw_result = True
                reply = obj.get("result", "")
                done = {
                    "type": "done",
                    "reply": reply,
                    "sessionId": obj.get("session_id"),
                    "ok": obj.get("is_error") is not True and obj.get("subtype") != "error",
                }
                if done["ok"] and api_error and str(reply or "").strip() in ("", api_error.strip()):
                    done["ok"] = False      # a "success" whose only answer was the provider's refusal
                fault = (_provider_fault(str(reply or "") or api_error, sdk_error, model_id,
                                         status=obj.get("api_error_status"))
                         if not done["ok"] else None)
                if not done["ok"] and looks_like_auth_failure(reply):
                    # The CLI's own auth text ("Not logged in · Please run /login") is an internal of
                    # THIS adapter — /login doesn't exist for an API consumer. Rewrite to the
                    # platform-actionable message; the raw text rides along in `detail` (additive).
                    done["detail"] = _short(reply, 200)
                    done["reply"] = (
                        "Model credentials are missing or expired for this deployment. "
                        "Set or refresh one of HOST_CLAUDE_CREDENTIALS, ANTHROPIC_API_KEY, "
                        "ANTHROPIC_AUTH_TOKEN or CLAUDE_CODE_OAUTH_TOKEN, "
                        "or configure a model under Settings → Models."
                    )
                    if fault is not None and fault.status is None:
                        # No status to read means the CLI's own wording — which is exactly the
                        # text this branch exists to keep off the person's screen.
                        fault = provider_faults.ProviderFault(
                            kind=fault.kind, provider=fault.provider, model=fault.model,
                            detail="the model credential is missing or expired",
                            remedy=done["reply"])
                elif fault is not None:
                    done["detail"] = _short(reply, 200)
                    done["reply"] = fault.sentence()
                if fault is not None:
                    done["fault"] = fault.as_dict()
                yield done
        if not saw_result:
            # THE CLI DIED WITHOUT A RESULT. A provider failure it printed as text (or as its
            # synthetic message) still ends the turn, typed — never a turn that just stops.
            fault = _provider_fault(api_error or "\n".join(stray), sdk_error, model_id)
            if fault is not None:
                yield {"type": "done", "reply": fault.sentence(), "sessionId": None, "ok": False,
                       "fault": fault.as_dict()}
    finally:
        # THE KILL HAPPENS HERE, on every interpreter. `lines` is `_exec_subprocess`'s generator and
        # its `finally` is what reaps the CLI child; a `for` loop hands that last hop to refcount
        # finalization, which on CPython 3.12.3 did not run it at all (Vexa-ai/vexa#1434) — the
        # phase's budget then stopped READING the process without stopping it. Closing explicitly is
        # what makes the budget's stop a kill rather than a hope.
        close_event_stream(lines)


#: The CLI's own wording for a provider's refusal: ``API Error: 402 <the provider's message>``.
_API_ERROR_TEXT = re.compile(r"^\s*api error:\s*\d{3}\b", re.IGNORECASE)


def _is_api_error(obj: dict, text: str) -> bool:
    """Is this assistant text block the CLI relaying a provider failure, not the model speaking?

    Measured on CLI 2.1.293 against an endpoint answering OpenRouter's 402: the CLI emits ONE
    assistant message with ``model: "<synthetic>"``, ``error: "unknown"`` and
    ``is_api_error_message: true`` whose text is ``API Error: 402 <message>``, then a ``result``
    with ``is_error: true`` and ``api_error_status: 402``. Any of its marks is enough; the bare
    ``API Error: <status>`` prefix is read too, so a build that drops the marks still cannot put
    the provider's text in the chat as if the agent had said it."""
    if obj.get("error") or obj.get("is_api_error_message") is True:
        return True
    if _API_ERROR_TEXT.match(str(text)):
        return True
    model = str((obj.get("message") or {}).get("model") or "")
    return model == "<synthetic>" and str(text).lstrip().lower().startswith("api error")


def _provider_fault(text: str, sdk_error: str, model: str,
                    status: object = None) -> "provider_faults.ProviderFault | None":
    """The typed fault for what the CLI reported, against the endpoint it was pointed at. ``status``
    is the result's ``api_error_status`` when the CLI wrote one — the provider's HTTP status, read
    as such rather than out of the prose."""
    host = provider_host()
    code = status if isinstance(status, int) and not isinstance(status, bool) and status >= 400 else None
    return provider_faults.classify(status=code, text=text or None, sdk_error=sdk_error or None,
                                    model=model,
                                    provider=host if host != "unknown" else "api.anthropic.com")


#: The settings every launch adds on top of the user scope: no hooks run in the worker.
NO_HOOKS_SETTINGS = json.dumps({"disableAllHooks": True})


def build_argv(
    prompt: str,
    *,
    allowed_tools: Iterable[str] = (),
    session: Optional[str] = None,
    model: Optional[str] = None,
    mcp_config: Optional[str] = None,
    stdin_mode: bool = False,
    effort: Optional[str] = None,
    workspace: Optional[str] = None,
) -> list[str]:
    """The headless Claude Code argv — `claude -p --input-format stream-json --output-format
    stream-json [...]`. The PROMPT IS NEVER IN ARGV: every process can read every other process's
    command line, so the turn's text travels on the CLI's stdin as its first stream-json user message
    (`_exec_subprocess_stdin`), whether or not mid-turn injection is on. ``prompt`` and
    ``stdin_mode`` are kept for callers; neither changes the argv.

    `--permission-mode acceptEdits` auto-accepts Read/Edit/Write so the turn runs fully headless; the
    `--allowedTools` scope is the capability gate (the model writes entities, `run_harness_turn`
    does the git commit). `--mcp-config <file>` + `--strict-mcp-config` attach EXACTLY the unit's
    granted MCP tools (the toolbelt) and nothing else. The container sandbox is the other
    enforcement layer.

    `effort` — when set — pins the session's reasoning effort (`--effort low|medium|high|xhigh`).
    Backends that validate the OpenAI-compatible `reasoning_effort` field (e.g. vLLM/LiteLLM model
    groups) reject the CLI's default `high` when it is outside their allowlist; an explicit value
    overrides that default. Unset ⇒ no flag ⇒ the CLI's own behaviour, unchanged.

    `--setting-sources user` — ALWAYS. The cwd is a workspace whose files come from wherever the
    person imported them, and a repository may carry its own `.claude/settings.json` or
    `.claude/settings.local.json`. Loaded as project/local settings, those could add hooks,
    environment, permission rules or helper commands to the turn. Only the user scope — the
    worker's own per-subject HOME — is read, and the turn's capabilities come from this argv alone.

    `--settings {"disableAllHooks": true}` — ALWAYS. The worker runs no hooks of its own, so no hook
    may run in it, from any settings scope or plugin. The worker images also carry the same key in
    the managed settings file, the scope `--setting-sources` cannot turn off.

    `--add-dir <workspace>` — the cwd again, as an additional directory, so its `CLAUDE.md` (the
    workspace's governance root, which every turn must read) still loads as project memory once
    project settings are off. The CLI reads `CLAUDE.md` from an added directory only with
    `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1`, which `_cli_env` sets; an added directory's
    `.claude/` skills, commands and agents load through the `project` source, which is off. The
    skills the CLI sees are the platform's and the workspace's own `skills/`, staged into the user
    scope (`_link_skills_into_home`) with the frontmatter that could grant tools removed from the
    workspace's.
    """
    # the prompt travels via stdin (stream-json): off the command line, and the pipe can stay open
    # for mid-turn injection when that is on
    argv = ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json",
            "--verbose", "--include-partial-messages", "--permission-mode", "acceptEdits"]
    argv += ["--setting-sources", "user", "--settings", NO_HOOKS_SETTINGS]
    if workspace:
        argv += ["--add-dir", workspace]
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


def _cli_env() -> dict[str, str]:
    """The Claude Code subprocess env: ``harness_subprocess_env()`` plus the one switch that makes
    the CLI read ``CLAUDE.md`` from an ``--add-dir`` directory. ``build_argv`` turns project settings
    off and adds the workspace back as an additional directory, so this is what keeps the
    workspace's ``CLAUDE.md`` loading as project memory."""
    env = harness_subprocess_env()
    env["CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"] = "1"
    # THE OUTPUT CAP (`llm.ports.max_output_tokens`). The CLI asks for 32000 output tokens unless
    # told otherwise; the deployment's one dial is mapped onto the CLI's own variable here.
    cap = max_output_tokens()
    if cap is not None:
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(cap)
    return env


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


def _exec_subprocess_stdin(argv: list[str], cwd: str, first_message: str, *,
                           injectable: bool = True) -> Iterator[str]:
    """The CLI exec: the prompt travels as the first stream-json user message on stdin (never in
    argv), and stdin stays open until a `result` line closes it (turn over → CLI exits). With
    ``injectable`` (mid-turn injection on) the open stdin is also published for
    ``inject_user_message``."""
    global _ACTIVE_STDIN
    proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1,
                            env=_cli_env(), **harness_identity_kwargs())
    assert proc.stdout is not None and proc.stdin is not None
    try:
        proc.stdin.write(_user_message_json(first_message) + "\n")
        proc.stdin.flush()
        if injectable:
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
    # harness_identity_kwargs: the CLI — and so the model's tools — run as the tools user, not as
    # the worker (llm/ports.py).
    proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                            env=_cli_env(), **harness_identity_kwargs())
    assert proc.stdout is not None
    try:
        yield from proc.stdout
    finally:
        try:
            proc.stdout.close()
        except Exception:  # noqa: BLE001
            pass
        _reap(proc)


def _nofollow_dirs(base: Path, *names: str) -> Optional[Path]:
    """``base/names…``, each level created if absent and opened through the level above without
    following a link (``workspace_paths.dir_fd_inside``): ``None`` when any level is a link or not a
    directory. ``base`` itself (the mount root, HOME) is the deployment's, and is made if absent."""
    try:
        os.close(wpaths.dir_fd_inside(base, names, create=True))
    except (OSError, wpaths.PathRefused):
        return None
    return base.joinpath(*names)


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
    # The continuity root is a mount the model's tools can write (``_system``), so a level of it may
    # be a link the turn planted: each level is created and opened without following one, and a
    # link anywhere skips the chat link (the turn still runs, without cross-turn resume).
    ws_projects = _nofollow_dirs(work, ".claude", "projects")
    home_claude = _nofollow_dirs(Path(os.environ.get("HOME", "/root")), ".claude")
    if ws_projects is None or home_claude is None:
        logger.warning("chat continuity not linked: a level of %s/.claude/projects or of HOME/.claude "
                       "is a link or not a directory", work)
        return
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


#: The dispatch's mark on a worker whose model route is the person's OWN endpoint
#: (``control_plane.dispatch.subject_route_env``). Present on that route and on no other.
SUBJECT_ROUTE_ENV = "VEXA_MODEL_ROUTE"
#: The CLI's credential file, relative to its config directory — where the runtime stages the
#: deployment's subscription in a process-backend HOME (``runtime_kernel.profiles``, a test holds
#: the two equal).
CREDENTIAL_FILE = ".credentials.json"


def cli_credential_path() -> Path:
    """Where the CLI reads a stored credential in a worker: its config directory, ``$HOME/.claude``
    (nothing sets another one — neither the dispatch nor the runtime forwards one)."""
    return Path(os.environ.get("HOME", "/root")) / ".claude" / CREDENTIAL_FILE


def clear_deployment_credential() -> Optional[str]:
    """On the person's own route, leave the CLI no stored credential: ``None`` when there is none
    left to find, else the reason the turn cannot start.

    Every key the CLI reads from its environment is the person's on this route (the dispatch stamps
    them, the empty string included), but a CLI with no key of its own signs in with the file in its
    config directory, and anything there is the deployment's. The file is this workload's own copy, so
    it is removed for the rest of the worker's life; a file that cannot be removed stops the turn
    before the CLI starts. On every other route the file is the credential the turn runs on and is
    left alone."""
    if os.environ.get(SUBJECT_ROUTE_ENV) != "subject":
        return None
    path = cli_credential_path()
    try:
        path.unlink()           # a link is removed itself, never followed
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("could not remove the stored model credential at %s: %s", path, exc)
    if os.path.lexists(path):
        return ("This turn runs on your own model endpoint, and the agent's environment still holds "
                "another model credential it could not remove. The turn was not started; an "
                "operator must not mount a model credential into the agent's config directory.")
    return None


def credential_conflict_fault() -> dict:
    """The refusal above, typed (P18, unit.v1 `Fault`): the worker would not start the turn in the
    environment it was given — not the model provider, which was never asked."""
    return {
        "source": fault_wire.AgentWorker.SOURCE,
        "kind": fault_wire.AgentWorker.CREDENTIAL_CONFLICT,
        "status": None,
        "detail": ("this turn runs on your own model endpoint, and the agent's environment holds "
                   "another model credential it could not remove, so the turn was not started"),
        "remedy": "An operator must not mount a model credential into the agent's config directory.",
    }


class ClaudeCodeHarness:
    """``HarnessPort`` adapter for the Claude Code CLI. ``exec_fn`` is injectable for tests."""

    name = "claude-code"

    def __init__(self, exec_fn: Optional[HarnessExec] = None) -> None:
        self._exec: HarnessExec = exec_fn or _exec_subprocess

    def run_turn(self, work: Path, prompt: str, *, allowed_tools: Iterable[str] = (),
                 session: Optional[str] = None, model: Optional[str] = None,
                 mcp_config: Optional[str] = None) -> Iterator[dict]:
        refused = clear_deployment_credential()
        if refused:
            yield {"type": "done", "reply": refused, "sessionId": session, "ok": False,
                   "fault": credential_conflict_fault()}
            return
        effort = os.environ.get("VEXA_AGENT_EFFORT") or None
        argv = build_argv(prompt, allowed_tools=allowed_tools, session=session, model=model,
                          mcp_config=mcp_config, stdin_mode=True, effort=effort, workspace=str(work))
        if self._exec is _exec_subprocess:
            # the real CLI: the prompt goes in on stdin, never on the command line
            yield from parse_stream_json(_exec_subprocess_stdin(argv, str(work), prompt,
                                                                injectable=midturn_enabled()))
        else:
            yield from parse_stream_json(self._exec(argv, str(work)))

    def prepare(self, work: Path, chat_root: Optional[Path] = None) -> None:
        # chats are saved to / resumed from the PRIVATE continuity root (the _system mount when the
        # dispatch declares one — the flat model can make the cwd a SHARED workspace, and chats are
        # private), not ~/.claude; skills are the platform's plus the workspace's own, in the user scope
        _link_chat_into_workspace(chat_root or work)
        _link_skills_into_home(work)

    def transcript_bytes(self, work: Path, session_id: str) -> int:
        """The stored size of this session's transcript under ``work/.claude/projects``, reached
        without following a link (``workspace_paths``): a linked folder or file is not this chat's."""
        total = 0
        name = f"{session_id}.jsonl"
        for slug in wpaths.list_dirs_inside(work, ".claude/projects", allow=(".claude",)):
            st = wpaths.stat_inside(work, f".claude/projects/{slug}/{name}", allow=(".claude",))
            if st is not None and stat.S_ISREG(st.st_mode):
                total += st.st_size
        return total

    def preflight(self) -> Optional[str]:
        return preflight_provider_guard()

    def midturn_enabled(self) -> bool:
        return midturn_enabled()

    def inject_user_message(self, text: str) -> bool:
        return inject_user_message(text)
