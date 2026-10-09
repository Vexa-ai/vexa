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
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, Iterator, Optional

import yaml

from llm.errors import looks_like_auth_failure, preflight_provider_guard
from llm.ports import HarnessExec, close_event_stream, harness_subprocess_env


# Tools whose SUCCESS means a document now exists that the person should be looking at. The
# vocabulary is explicit rather than a prefix match: "a tool whose name contains write" would catch
# a future `workspace_write_policy` or a `write_transcript` and open tabs nobody asked for.
_WRITER_TOOLS = frozenset({
    "mcp__vexa__workspace_write",
    "Write",
    "Edit",
    "NotebookEdit",
})


# THE TRANSCRIPT-TERM PUBLISH (PRD decision 35). `transcript_terms` is the only tool whose SUCCESS
# is meant to paint something on the meeting view, so it is the only one whose RESULT BODY is read
# here rather than summarised. A closed vocabulary for the same reason `_WRITER_TOOLS` is one: a
# prefix match would let any future tool ending in `_terms` drive somebody's transcript.
_TERMS_TOOLS = frozenset({
    "mcp__vexa__transcript_terms",
    "transcript_terms",
})

# The send that puts a bot in a room NOW.
_BOT_TOOLS = frozenset({
    "mcp__vexa__request_meeting_bot",
    "request_meeting_bot",
})

# THE ASK TO OPEN SOMETHING (Vexa-ai/vexa#1586). Every other panel move on this page is a SIDE
# EFFECT of doing something else — a write opens its file, a send opens its transcript. This one is
# the act itself: the founder typed "open meeting transcript", the agent read the 677 segments and
# described them, and he answered *"it did not open the transcript"*. Asked to open something, the
# only move it had was to describe it.
#
# The tool is served by the vexa MCP rather than being a harness builtin, and that is what makes it
# work on BOTH runners from one implementation: `claude-code` drives a CLI whose tool list is the
# CLI's own and cannot reach a Python builtin (`llm/JOBS.md` states this for `spawn_job`), while an
# MCP verb is reachable by every runner that attaches the server. The event derivation below is
# shared the same way `_bot_artifact` is.
_OPEN_TOOLS = frozenset({
    "mcp__vexa__open_page",
    "open_page",
})


# A WORKSPACE MADE FROM THIS CONVERSATION JOINS IT (Vexa-ai/vexa#1603). The founder asked for *"a
# new workspace where we will collect everything we know about ILM"*, got one, and was then told
# *"the new workspace isn't in my native mount stack (it's reached via the workspace_* tools)"* —
# *"not native workspace??"*. Creating a place IS the act of bringing it into the room; reaching it
# through the tools afterwards is the defect. So the create emits its own event, exactly as a send
# emits the transcript's: the chip shows it, the panel mounts it, and agent-api reads the same
# event on the way past to put it in the session's focus for every later turn and every other
# browser.
#
# A CLOSED VOCABULARY, for the reason `_WRITER_TOOLS` is one: a prefix match on "workspace" would
# put every listing, read and purpose-edit into somebody's focus.
#
# …AND SO DOES THE VERB THAT MOVES THE TARGET (Vexa-ai/vexa#1611). `workspace_target` is what an
# agent calls when the person says *"work in the OeNB workspace"*; it emits the SAME event, because
# a `focus` says "this workspace is where this conversation is working" and that has always meant
# both halves — it is in the chat's mount set, and it is the one writes go to. Two event kinds for
# one sentence is how a chip and a record come to disagree.
_FOCUS_TOOLS = frozenset({
    "mcp__vexa__workspace_new",
    "workspace_new",
    "mcp__vexa__workspace_target",
    "workspace_target",
    "workspace_import",
    "mcp__vexa__workspace_import",
    "workspace_import_status",
    "mcp__vexa__workspace_import_status",
})


def _tool_result_text(content: object) -> str:
    """The tool result as one string, whichever shape the harness handed it in.

    Claude Code emits a tool result either as a bare string or as a list of content blocks; both
    reach here, and a reader that handles only one of them fails SILENTLY on the other — which for
    this seam means chips that simply never appear and nothing anywhere saying why."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content
                       if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _published_terms(content: object) -> "dict | None":
    """The `terms` event a `transcript_terms` result asks for, or None.

    ONLY WHEN THE AGENT PUBLISHED. The tool answers a bare look-up call with ``emit: []`` — that
    call was the agent reading the room, and painting its raw output would put every capitalised
    word in the meeting on the person's screen. An empty publish is a NON-EVENT rather than an empty
    event: an empty event would clear the chips the previous Highlight put there."""
    try:
        obj = json.loads(_tool_result_text(content))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    emit = obj.get("emit")
    if not isinstance(emit, list) or not emit:
        return None
    return {"type": "terms", "meeting": str(obj.get("meeting") or ""),
            "cursor": str(obj.get("cursor") or ""), "terms": emit}


def _bot_artifact(content: object) -> "dict | None":
    """The panel move a successful `request_meeting_bot` earns, or None (F73, decision 30.4).

    The founder watched the agent finish a send and then offer him a LINK into the product he was
    already looking at. The fix is not a better sentence — the panel is the product's own surface and
    moving it is the harness's job, not something the model should be asked to remember. So the send
    itself opens the live transcript beside the chat.

    BY THE ROW, NEVER THE NATIVE ID. `path` is the literal string ``meeting:`` + the meeting row id;
    a personal room's native id spans every meeting ever held in it, so it names a series and the
    resolver would pick whichever occurrence is newest. The send answers with the meeting row it
    created (`id`) — or, for a bot already in that room, `{"status": "already_exists", "meeting":
    row}`. No row, no event — a panel aimed at a guess is the failure this whole seam is careful
    about, and a meeting whose bot already `failed` opens nothing.

    `pin` and `focus` are separate and both are wanted here: pin KEEPS the transcript in the strip so
    it survives the next thing opened, focus FRONTS it now.

    …AND THE NATIVE ID RIDES ALONG (Vexa-ai/vexa#1597). This event is the only place in the system
    where "a bot was sent, from THIS chat, into THAT meeting" is stated, and agent-api reads it to
    BIND the meeting to the chat's session. The row addresses the meeting as the panel addresses it;
    the native id is how everything that talks to meeting-api addresses it (`stop_bot`, the
    transcript API), so the binding carries both rather than making a second lookup the price of
    knowing the second one. Nothing renders it — the client already reads a native id off the
    meetings list — so an absent one costs the binding a field, never the event."""
    try:
        obj = json.loads(_tool_result_text(content))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("status") == "already_exists":
        obj = obj.get("meeting") if isinstance(obj.get("meeting"), dict) else {}
    if str(obj.get("status") or "").lower() == "failed":
        return None
    row = str(obj.get("id") or "").strip()
    if not row:
        return None
    ev = {"type": "artifact", "path": f"meeting:{row}", "pin": True, "focus": True}
    native = str(obj.get("native_meeting_id") or "").strip()
    if native:
        ev["native"] = native
    return ev


def _workspace_focus(content: object) -> "dict | None":
    """The `focus` event a successful `workspace_new` or `workspace_target` earns, or None
    (Vexa-ai/vexa#1603, Vexa-ai/vexa#1611).

    ONE FIELD DECIDES IT: `created`, the workspace id the create route minted, which the tool
    returns for a create and for nothing else — or `targeted`, the id `workspace_target` returns
    for a workspace it confirmed this person can write. No id, no event — a focus aimed at a guess
    would put a chat permanently over a workspace that does not exist, the same failure
    `_bot_artifact` is careful about one object along.

    TWO NAMES, ONE FIELD, deliberately: the tools answer different questions ("I made this" versus
    "we are working here") and a result that said `created` for a target would be a lie in the
    transcript. What they MEAN to the chat is identical, which is why one event carries both.

    `name` rides along because this is the only moment the workspace's HUMAN name is in hand on
    this path; the client shows names, never slugs. It is display only and an absent one costs the
    event a field, never the focus.

    THE EVENT CLAIMS NO ACCESS. Whether the next turn mounts this read-write is decided by
    membership, server-side, in `shared_active_mounts` — not by a string a harness wrote. What this
    event says is only "this workspace is now part of this conversation", which is the one thing
    the harness is in a position to know."""
    try:
        obj = json.loads(_tool_result_text(content))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("status") == "completed" and isinstance(obj.get("result"), dict):
        obj = {"targeted": obj["result"].get("workspace"), "name": obj["result"].get("name")}
    wid = str(obj.get("created") or obj.get("targeted") or "").strip()
    # A slug is one path segment and never a dot-namespaced reserved one. Same shape check the
    # store itself applies; refusing here keeps a malformed answer out of a durable session record.
    if not wid or "/" in wid or wid.startswith("."):
        return None
    ev = {"type": "focus", "workspace": wid}
    name = str(obj.get("name") or "").strip()
    if name:
        ev["name"] = name
    return ev


def _open_event(content: object) -> "dict | None":
    """The `open` event a successful `open_page` result asks for, or None (Vexa-ai/vexa#1586).

    The TOOL decides whether anything is there — it resolves the target against the person's own
    workspaces and meetings and answers `opened: false` with a reason when it is not — so this reads
    the answer rather than re-deriving it. A refusal paints nothing: the agent was told, in words,
    and its one-line reply is that reason.

    `path` carries the SAME two dialects an `artifact` event does, so the client resolves both
    through the one function it already has (`pageForArtifact`): a workspace-relative path for a
    document, and the literal `meeting:<row id>` for the live transcript canvas. A transcript is not
    a file (founder ruling 2026-09-01) and this is where that stays true.

    There is no `focus` flag and that is deliberate. An `artifact` is the turn saying "I wrote
    this"; an `open` is a person having asked to see it, so it always comes to the front — including
    over a page the reader opened moments ago, which for `artifact` is the case that must NOT
    interrupt them."""
    try:
        obj = json.loads(_tool_result_text(content))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict) or not obj.get("opened"):
        return None
    path = str(obj.get("path") or "").strip()
    if not path:
        return None
    return {"type": "open", "target": str(obj.get("target") or ""),
            "workspace": str(obj.get("workspace") or ""), "path": path}


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
    try:
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
                        "ANTHROPIC_AUTH_TOKEN or CLAUDE_CODE_OAUTH_TOKEN, "
                        "or configure a model under Settings → Models."
                    )
                yield done
    finally:
        # THE KILL HAPPENS HERE, on every interpreter. `lines` is `_exec_subprocess`'s generator and
        # its `finally` is what reaps the CLI child; a `for` loop hands that last hop to refcount
        # finalization, which on CPython 3.12.3 did not run it at all (Vexa-ai/vexa#1434) — the
        # phase's budget then stopped READING the process without stopping it. Closing explicitly is
        # what makes the budget's stop a kill rather than a hope.
        close_event_stream(lines)


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
    if stdin_mode:
        # prompt travels via stdin (stream-json) so the pipe stays open for mid-turn injection
        argv = ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                "--verbose", "--include-partial-messages", "--permission-mode", "acceptEdits"]
    else:
        argv = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
                "--include-partial-messages", "--permission-mode", "acceptEdits"]
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


def _exec_subprocess_stdin(argv: list[str], cwd: str, first_message: str) -> Iterator[str]:
    """stdin-mode exec: the prompt travels as the first stream-json user message and stdin STAYS
    OPEN for mid-turn injection; a `result` line closes it (turn over → CLI exits)."""
    global _ACTIVE_STDIN
    proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1,
                            env=_cli_env())
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
                            env=_cli_env())
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


def _governed_skills_dir() -> Optional[Path]:
    """The platform's governed skills: the ``skills/`` of the workspace seed this deployment ships
    (``shared.seeding.resolve_seed_dir`` — read-only in the image). None when the seed has none."""
    from shared.seeding import resolve_seed_dir

    skills = resolve_seed_dir() / "skills"
    return skills if skills.is_dir() else None


#: Frontmatter a WORKSPACE skill does not carry into the worker. In Claude Code 2.1.293
#: `allowed-tools` is a grant, not a limit: the tools it lists run without a permission check for the
#: turn that invokes the skill, and workspace trust never gates it, so in a headless turn it reaches
#: past `--allowedTools` (a gated MCP tool, the bot verbs a room run withholds, Bash where the turn
#: has none). Measured in the worker image: with `Skill` allowed, a workspace skill's
#: `allowed-tools: Bash` ran Bash under `--allowedTools Read,Skill`. `hooks` registers hooks for the
#: rest of the session; `disableAllHooks` turned them off in the same measurement, and dropping the
#: key keeps that true on its own. A skill declaring either key also needs its own approval before
#: it can be invoked, which a headless turn refuses, so the copy is also what makes it usable. Keys
#: are compared case- and underscore-insensitively, although the CLI reads only the exact names.
WORKSPACE_SKILL_DROPPED_KEYS = frozenset({"allowed-tools", "hooks"})

#: Where the per-turn skill set is assembled, under the worker's HOME. ``~/.claude/skills`` links to
#: the newest ``turn-*`` directory in it; every directory there was made by ``_link_skills_into_home``.
SKILLS_STAGE_DIR = ".vexa-skills"


def _frontmatter_key(key: object) -> str:
    return str(key).strip().lower().replace("_", "-")


def _sanitized_workspace_skill(text: str) -> "tuple[dict, str] | None":
    """A workspace ``SKILL.md`` rewritten without ``WORKSPACE_SKILL_DROPPED_KEYS``: ``(frontmatter,
    file text)``. The frontmatter is re-emitted from the parsed mapping, so the CLI reads exactly
    the keys kept here, and a file with none gets an empty block ahead of its text. None — the skill
    is not loaded — when the frontmatter does not close or does not parse to a mapping: a parser
    more lenient than this one might still find a grant in it."""
    rest = text.lstrip("\ufeff")
    meta: dict = {}
    lines = rest.splitlines(keepends=True)
    if lines and lines[0].strip() == "---":
        close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if close is None:
            return None
        try:
            parsed = yaml.safe_load("".join(lines[1:close]))
        except Exception:  # noqa: BLE001 — YAMLError, or a ValueError from an impossible date
            return None
        if parsed is None:
            parsed = {}
        if not isinstance(parsed, dict):
            return None
        meta = {k: v for k, v in parsed.items()
                if _frontmatter_key(k) not in WORKSPACE_SKILL_DROPPED_KEYS}
        rest = "".join(lines[close + 1:])
    head = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, default_flow_style=False,
                          width=1 << 20)
    return meta, f"---\n{head}---\n{rest}"


def _skill_dir(d: Path) -> bool:
    """A loadable skill folder: ``<name>/SKILL.md``, not hidden, not the CLI's reserved ``synced``."""
    return (not d.name.startswith(".") and d.name.lower() != "synced"
            and d.is_dir() and (d / "SKILL.md").is_file())


def _stage_workspace_skill(src: Path, dst: Path, taken: set) -> None:
    """Stage one workspace skill as a real folder holding the sanitized ``SKILL.md``, with every
    other entry linked back to the workspace so the skill's scripts and references resolve. Hidden
    entries are not linked: a ``.claude-plugin/`` would make the folder a plugin, which brings its
    own hooks, agents and MCP servers. A skill whose name is taken by a platform skill is skipped."""
    try:
        staged = _sanitized_workspace_skill((src / "SKILL.md").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return
    if staged is None:
        return
    meta, text = staged
    if str(meta.get("name") or src.name) in taken:
        return
    dst.mkdir()
    (dst / "SKILL.md").write_text(text, encoding="utf-8")
    for entry in src.iterdir():
        if entry.name != "SKILL.md" and not entry.name.startswith("."):
            (dst / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())


def _assemble_skills(stage: Path, work: Path) -> None:
    """The turn's skill set in ``stage``: every platform skill, linked as shipped, then every skill
    in the workspace's own ``skills/`` that does not reuse a platform skill's name, sanitized. A
    seeded workspace keeps a copy of the platform's skills, so on a name clash the platform's
    current version is the one that loads."""
    taken: set = set()
    platform = _governed_skills_dir()
    if platform is not None:
        for d in sorted(platform.iterdir()):
            if _skill_dir(d):
                (stage / d.name).symlink_to(d, target_is_directory=True)
                taken.add(d.name)
    own = work / "skills"
    if own.is_dir():
        for d in sorted(own.iterdir()):
            try:
                if _skill_dir(d) and d.name not in taken:
                    _stage_workspace_skill(d, stage / d.name, taken)
            except Exception:  # noqa: BLE001 — one unreadable skill, not all of them
                shutil.rmtree(stage / d.name, ignore_errors=True)


def _link_skills_into_home(work: Path) -> None:
    """Expose the platform's skills and the workspace's own through the USER scope, the only
    settings scope the CLI reads (``build_argv`` passes ``--setting-sources user``). Each turn the
    set is assembled afresh in ``~/.vexa-skills/turn-*`` and ``~/.claude/skills`` is repointed at it,
    so a skill removed from the workspace is gone next turn and nothing the CLI writes there (its
    ``synced/`` download, its ``.trash/``) lands in a workspace or in the image's seed. Workspace
    skills are copies made by ``_stage_workspace_skill``: they cannot grant tools or register hooks,
    and an edit made during a turn applies from the next one.

    SAFETY, exactly as ``_link_chat_into_workspace``: only the disposable per-subject HOME may be
    rewritten. Outside a worker (a host test run, a developer shell) ``~/.claude/skills`` holds the
    developer's own skills, so an existing directory is replaced only when EMPTY (``rmdir`` cannot
    destroy content); a non-empty one, or any other object, is left alone and the link is skipped —
    the turn still works, without skills. Earlier stages are removed with ``rmtree``, which unlinks
    the links inside them and never follows one into a workspace. Best-effort: never raises."""
    home = Path(os.environ.get("HOME", "/root"))
    home_claude = home / ".claude"
    link = home_claude / "skills"
    stages = home / SKILLS_STAGE_DIR
    try:
        # The old link goes FIRST, so a turn whose stage cannot be built runs with no skills rather
        # than with whatever an earlier turn, or an earlier version of this function, linked.
        if link.is_symlink():
            link.unlink()
        elif link.is_dir():
            if any(link.iterdir()):
                return  # real skills live here — never delete, skip the link
            link.rmdir()
        elif link.exists():
            return
        stages.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix="turn-", dir=stages))
        _assemble_skills(stage, work)
        home_claude.mkdir(parents=True, exist_ok=True)
        link.symlink_to(stage, target_is_directory=True)
        for old in stages.iterdir():
            if old != stage and old.name.startswith("turn-") and not old.is_symlink():
                shutil.rmtree(old, ignore_errors=True)
    except Exception:  # noqa: BLE001 — best-effort: the turn runs, without skills
        pass


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
                              mcp_config=mcp_config, stdin_mode=True, effort=effort,
                              workspace=str(work))
            yield from parse_stream_json(_exec_subprocess_stdin(argv, str(work), prompt))
        else:
            argv = build_argv(prompt, allowed_tools=allowed_tools, session=session, model=model,
                              mcp_config=mcp_config, effort=effort, workspace=str(work))
            yield from parse_stream_json(self._exec(argv, str(work)))

    def prepare(self, work: Path, chat_root: Optional[Path] = None) -> None:
        # chats are saved to / resumed from the PRIVATE continuity root (the _system mount when the
        # dispatch declares one — the flat model can make the cwd a SHARED workspace, and chats are
        # private), not ~/.claude; skills are the platform's plus the workspace's own, in the user scope
        _link_chat_into_workspace(chat_root or work)
        _link_skills_into_home(work)

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
