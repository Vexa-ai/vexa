"""Codex app-server HarnessPort adapter.

Codex's non-interactive ``exec`` command is a one-prompt process. Vexa uses ``app-server`` instead:
one JSON-RPC connection per turn, durable thread rollouts under the private continuity root, and
``turn/steer`` for user input that arrives while the turn is in flight. All Codex protocol details
stay in this vendor-named module; callers see only the frozen UnitEvent stream.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import threading
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

# THE PANEL CONVENTIONS ARE SHARED, IMPORTED (F92) — the writer's tab, decision 35's transcript
# chips and decision 30.4's bot-send open. This adapter emitted NONE of them, so the same turn
# painted the person's screen or did not depending on which harness the deployment ran.
# `llm.tool_events` owns them. One vocabulary, three harnesses.
from llm.tool_events import (_BOT_TOOLS, _TERMS_TOOLS, _WRITER_TOOLS, _bot_artifact,
                             _published_terms, _written_artifact)
from llm import faults as provider_faults
from llm import workspace_paths as wpaths
from llm.ports import harness_identity_kwargs, harness_subprocess_env, tools_identity


def _short(value: object, n: int = 120) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, default=str)
    return " ".join(text.split())[:n]


def _tool_started(item: dict) -> Optional[dict]:
    kind = item.get("type")
    call_id = item.get("id", "")
    if kind == "commandExecution":
        return {"type": "tool-call", "tool": "Bash",
                "args": {"command": item.get("command", "")}, "callId": call_id}
    if kind == "fileChange":
        paths = [change.get("path") for change in item.get("changes", []) if change.get("path")]
        return {"type": "tool-call", "tool": "Edit", "args": {"paths": paths}, "callId": call_id}
    if kind == "mcpToolCall":
        server, tool = item.get("server", ""), item.get("tool", "")
        return {"type": "tool-call", "tool": f"mcp__{server}__{tool}",
                "args": item.get("arguments") or {}, "callId": call_id}
    if kind == "webSearch":
        return {"type": "tool-call", "tool": "WebSearch",
                "args": {"query": item.get("query", "")}, "callId": call_id}
    if kind == "imageView":
        return {"type": "tool-call", "tool": "Read",
                "args": {"path": item.get("path", "")}, "callId": call_id}
    return None


def _result_content(item: dict) -> object:
    """The item's result in a shape the shared panel helpers read: a string, or Claude's list of
    content blocks. Codex hands MCP results back as ``{"content": [{"type": "text", ...}]}``, which
    IS that list one level down; anything else is serialised so a JSON body still survives."""
    result = item.get("result")
    if isinstance(result, (str, list)):
        return result
    if isinstance(result, dict):
        if isinstance(result.get("content"), list):
            return result["content"]
        return json.dumps(result, default=str)
    return item.get("aggregatedOutput") or ""


def _panel_events(item: dict) -> list[dict]:
    """The panel moves a SUCCESSFUL codex item earns — the same three conventions `claude_code` and
    `openai_agent` apply, through the same helpers. A failed item moves nothing."""
    kind = item.get("type")
    events: list[dict] = []
    if kind == "fileChange":
        # Codex reports one item per edit batch; each changed path is its own artifact, and the
        # LAST one is the document the person should be looking at, so only it takes focus.
        targets = [_written_artifact("Write", {"file_path": change.get("path")})
                   for change in item.get("changes", []) if change.get("path")]
        found = [t for t in targets if t]
        for i, (slug, rel) in enumerate(found):
            events.append({"type": "artifact", "workspace": slug, "path": rel,
                           "focus": i == len(found) - 1})
        return events
    if kind != "mcpToolCall":
        return events
    name = f"mcp__{item.get('server', '')}__{item.get('tool', '')}"
    if name in _WRITER_TOOLS:
        target = _written_artifact(name, item.get("arguments") or {})
        if target:
            events.append({"type": "artifact", "workspace": target[0], "path": target[1],
                           "focus": True})
    elif name in _TERMS_TOOLS:
        ev = _published_terms(_result_content(item))
        if ev:
            events.append(ev)
    elif name in _BOT_TOOLS:
        ev = _bot_artifact(_result_content(item))
        if ev:
            events.append(ev)
    return events


def _tool_completed(item: dict) -> Optional[dict]:
    kind = item.get("type")
    if kind not in {"commandExecution", "fileChange", "mcpToolCall", "webSearch", "imageView"}:
        return None
    status = item.get("status")
    ok = status not in {"failed", "declined", "cancelled"} and item.get("error") in (None, "")
    summary = (item.get("aggregatedOutput") or item.get("result") or item.get("error")
               or status or "completed")
    return {"type": "tool-result", "callId": item.get("id", ""), "ok": ok,
            "summary": _short(summary)}


def normalize_notification(message: dict, reply_parts: list[str]) -> list[dict]:
    """Normalize one app-server notification into zero or more frozen UnitEvents."""
    method = message.get("method")
    params = message.get("params") or {}
    if method == "item/agentMessage/delta":
        delta = params.get("delta", "")
        if delta:
            reply_parts.append(delta)
            return [{"type": "message-delta", "text": delta}]
        return []
    if method == "item/started":
        event = _tool_started(params.get("item") or {})
        return [event] if event else []
    if method == "item/completed":
        item = params.get("item") or {}
        event = _tool_completed(item)
        if not event:
            return []
        # Panel moves ride AFTER the tool-result, and only on success — the same ordering and the
        # same success-only rule the other two harnesses apply.
        return [event] + (_panel_events(item) if event.get("ok") else [])
    return []


def _final_reply(turn: dict, reply_parts: list[str]) -> str:
    if reply_parts:
        return "".join(reply_parts)
    messages = [item.get("text", "") for item in turn.get("items", [])
                if item.get("type") == "agentMessage" and item.get("text")]
    return messages[-1] if messages else ""


def _mcp_config(path: Optional[str], allowed_tools: Iterable[str]) -> dict:
    """Translate Claude-shaped MCP launch JSON into Codex config, attaching AUTO grants only.

    ``ToolGrant`` places auto-approved servers in ``allowed_tools`` as ``mcp__<server>`` and gated
    servers only in the JSON. Codex has no Vexa approval callback yet, so gated servers are omitted
    entirely: fail closed rather than silently broadening authority.
    """
    if not path:
        return {}
    p = Path(path)           # the private attachment, read nofollow — see `engine._mcp_endpoint`
    text = wpaths.read_text_inside(p.parent, p.name)
    try:
        raw = json.loads(text) if text is not None else {}
    except (ValueError, TypeError):
        return {}
    auto = {name.removeprefix("mcp__") for name in allowed_tools if name.startswith("mcp__")}
    servers = raw.get("mcpServers") or {}
    selected = {name: spec for name, spec in servers.items() if name in auto}
    return {"mcp_servers": selected} if selected else {}


def codex_home() -> Path:
    """Where Codex keeps its state and its subscription ``auth.json``: ``CODEX_HOME`` (the runtime
    names it for every worker it spawns, and the Codex CLI reads the same variable), else
    ``$HOME/.codex``."""
    configured = (os.environ.get("CODEX_HOME") or "").strip()
    return Path(configured) if configured else Path(os.environ.get("HOME", "/root")) / ".codex"


def _tools_codex_home(ident: "tuple[int, int]") -> Path:
    """The Codex home for a harness that runs as the tools user. The runtime's ``CODEX_HOME`` holds the
    subscription ``auth.json`` as a read-only bind of a host file whose mode is the host's, which that
    user may not be able to read; so it gets a home of its own beside it, holding a copy of the
    credential (0600, its own) and the same durable sessions link. Without a mounted credential the
    runtime's home serves as it is."""
    home = codex_home()
    # NOTHING HERE FOLLOWS A LINK. ``CODEX_HOME`` is handed to the tools user for the turn, and
    # ``<home>-tools`` sits beside it (in a world-writable /tmp in the image), so either the
    # credential or the copy's home can be a link the tools user planted: read through, root would
    # copy any file it can read into a file then given to the tools user; written through, root
    # would write the credential where the link points and chown that path away.
    data = wpaths.read_bytes_inside(home, "auth.json")      # a link at auth.json is no credential
    if data is None:
        return home
    parent, name = home.parent, f"{home.name}-tools"
    st = wpaths.stat_inside(parent, name)
    if st is not None and not stat.S_ISDIR(st.st_mode):
        wpaths.unlink_inside(parent, name)                  # a planted link: removed, never entered
    try:
        fd = wpaths.dir_fd_inside(parent, (name,), create=True, mode=0o700)
    except (wpaths.PathRefused, OSError):
        return home                                         # no safe home of its own: serve as is
    try:
        os.fchmod(fd, 0o700)
        wpaths.write_bytes_inside(parent, f"{name}/auth.json", data, mode=0o600,
                                  before_replace=lambda f: os.fchown(f, *ident))
        sessions = home / "sessions"
        if sessions.is_symlink():
            try:
                os.lstat("sessions", dir_fd=fd)
            except FileNotFoundError:
                os.symlink(os.readlink(sessions), "sessions", target_is_directory=True, dir_fd=fd)
            try:
                os.chown("sessions", *ident, dir_fd=fd, follow_symlinks=False)
            except OSError:
                pass
        os.fchown(fd, *ident)                               # the directory itself, by its fd
    finally:
        os.close(fd)
    return parent / name


def _link_sessions_into_workspace(work: Path) -> None:
    """Keep Codex rollouts durable without moving the subscription auth file into the workspace."""
    # `.claude/` is the frozen, already-ignored agent plumbing root in every existing workspace.
    # Nest Codex state there so upgrading an old workspace cannot make continuity files/symlinks
    # visible to the turn's commit-all path.
    ws_sessions = work / ".claude" / "codex" / "sessions"
    ws_sessions.mkdir(parents=True, exist_ok=True)
    home_codex = codex_home()
    home_codex.mkdir(parents=True, exist_ok=True)
    link = home_codex / "sessions"
    try:
        if link.is_symlink():
            if os.readlink(link) == str(ws_sessions):
                return
            link.unlink()
        elif link.is_dir():
            if any(link.iterdir()):
                return
            link.rmdir()
        elif link.exists():
            return
        link.symlink_to(ws_sessions, target_is_directory=True)
    except OSError:
        pass


class _RpcFailure(RuntimeError):
    """A JSON-RPC call the app server answered with an error, or a stream that ended early. ``error``
    is the server's own error object, when there was one, for :func:`_turn_fault`."""

    def __init__(self, message: str, error: object = None) -> None:
        super().__init__(message)
        self.error = error


# ── a failed turn ends TYPED (P18, S66) ─────────────────────────────────────────────────────────
#: Codex's own error label (app-server v2 ``TurnError.codexErrorInfo``) → the provider-fault kind it
#: names. A label for one of Codex's OWN limits (``sessionBudgetExceeded``, ``contextWindowExceeded``,
#: ``sandboxError``, ``other``…) names none, and the error's text is read instead.
_CODEX_ERROR_KIND = {
    "usageLimitExceeded": provider_faults.UNPAID,
    "rateLimitExceeded": provider_faults.RATE_LIMITED,
    "serverOverloaded": provider_faults.UNAVAILABLE,
    "internalServerError": provider_faults.UNAVAILABLE,
    "unauthorized": provider_faults.UNAUTHORIZED,
    "badRequest": provider_faults.REFUSED,
}
#: The ``codexErrorInfo`` variants that say the request never got a whole answer. Each may carry the
#: upstream's ``httpStatusCode``, which then names the kind.
_CODEX_TRANSPORT = ("httpConnectionFailed", "responseStreamConnectionFailed",
                    "responseStreamDisconnected", "responseTooManyFailedAttempts")


def _codex_provider() -> str:
    """The host a Codex turn's model requests go to: the OpenAI API for a key, else the ChatGPT
    backend a subscription signs in to."""
    if any((os.environ.get(key) or "").strip() for key in ("OPENAI_API_KEY", "CODEX_API_KEY")):
        return "api.openai.com"
    return "chatgpt.com"


def _turn_fault(error: object, model: str = "") -> "Optional[provider_faults.ProviderFault]":
    """The typed provider fault for a failed Codex turn — ``turn.error`` (``{message,
    codexErrorInfo, additionalDetails}``) or a JSON-RPC error — or None when nothing in it names one.
    Nothing here is Codex's raw text: ``faults.classify`` keeps only a safe ``detail``."""
    err = error if isinstance(error, dict) else {"message": str(error or "")}
    info = err.get("codexErrorInfo")
    if info is None and isinstance(err.get("data"), dict):
        info = err["data"].get("codexErrorInfo")
    status: Optional[int] = None
    kind: Optional[str] = None
    transport = False
    if isinstance(info, str):
        kind = _CODEX_ERROR_KIND.get(info)
    elif isinstance(info, dict):
        for name in _CODEX_TRANSPORT:
            if name not in info:
                continue
            transport = True
            variant = info.get(name)
            code = variant.get("httpStatusCode") if isinstance(variant, dict) else None
            if isinstance(code, int) and not isinstance(code, bool) and provider_faults.kind_for_status(code):
                status = code
    text = " ".join(str(err.get(k)) for k in ("message", "additionalDetails")
                    if isinstance(err.get(k), str) and err.get(k).strip())
    return provider_faults.classify(status=status, text=text or None, provider=_codex_provider(),
                                    model=model, kind=kind, transport=transport)


def _failed_done(model_reply: str, failure: str, session: Optional[str], error: object,
                 model: str) -> dict:
    """The ``done`` a failed Codex turn ends on, carrying ``fault`` when the failure is the model
    provider's. Whatever the model already said stays the reply; with nothing said, the reply is the
    fault's sentence (its own words, ``failure``, move to ``detail``) — or, untyped, those words."""
    done: dict = {"type": "done", "reply": model_reply or failure, "sessionId": session, "ok": False}
    fault = _turn_fault(error, model)
    if fault is not None:
        done["fault"] = fault.as_dict()
        if not model_reply.strip():
            done["detail"] = _short(failure, 200)
            done["reply"] = fault.sentence()
    return done


ProcessFactory = Callable[..., subprocess.Popen]


class CodexHarness:
    """HarnessPort adapter backed by ``codex app-server --stdio``."""

    name = "codex"

    def __init__(self, process_factory: Optional[ProcessFactory] = None) -> None:
        self._process_factory = process_factory or subprocess.Popen
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._thread_id: Optional[str] = None
        self._turn_id: Optional[str] = None
        self._next_id = 1

    def prepare(self, work: Path, chat_root: Optional[Path] = None) -> None:
        _link_sessions_into_workspace(chat_root or work)

    def transcript_bytes(self, work: Path, session_id: str) -> int:
        total = 0
        for path in (work / ".claude" / "codex" / "sessions").rglob(f"*{session_id}*.jsonl"):
            try:
                total += path.stat().st_size
            except OSError:
                pass
        return total

    def preflight(self) -> Optional[str]:
        if any((os.environ.get(key) or "").strip()
               for key in ("OPENAI_API_KEY", "CODEX_API_KEY")):
            return None
        text = wpaths.read_text_inside(codex_home(), "auth.json")   # a linked credential is none
        try:
            if text is not None and json.loads(text):
                return None
        except (ValueError, TypeError):
            pass
        return ("Codex credentials are missing. Mount a subscription auth file with "
                "HOST_CODEX_CREDENTIALS (normally ~/.codex/auth.json after `codex login`) "
                "or provide OPENAI_API_KEY.")

    def midturn_enabled(self) -> bool:
        return os.environ.get("VEXA_MIDTURN_INJECT", "") == "1"

    def _id(self) -> int:
        with self._lock:
            value = self._next_id
            self._next_id += 1
            return value

    @staticmethod
    def _write(proc: subprocess.Popen, message: dict) -> None:
        if proc.stdin is None:
            raise _RpcFailure("codex app-server stdin is closed")
        proc.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        proc.stdin.flush()

    @staticmethod
    def _read(proc: subprocess.Popen) -> dict:
        if proc.stdout is None:
            raise _RpcFailure("codex app-server stdout is closed")
        for raw in proc.stdout:
            try:
                value = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict):
                return value
        raise _RpcFailure("codex app-server exited before completing the turn")

    def _request(self, proc: subprocess.Popen, method: str, params: dict) -> dict:
        request_id = self._id()
        self._write(proc, {"method": method, "id": request_id, "params": params})
        while True:
            message = self._read(proc)
            if message.get("id") != request_id:
                continue
            if message.get("error"):
                raise _RpcFailure(_short(message["error"], 240), message["error"])
            return message.get("result") or {}

    def inject_user_message(self, text: str) -> bool:
        with self._lock:
            proc, thread_id, turn_id = self._proc, self._thread_id, self._turn_id
            if proc is None or thread_id is None or turn_id is None or proc.poll() is not None:
                return False
            request_id = self._next_id
            self._next_id += 1
            try:
                self._write(proc, {
                    "method": "turn/steer", "id": request_id,
                    "params": {"threadId": thread_id, "expectedTurnId": turn_id,
                               "input": [{"type": "text", "text": text}]},
                })
                return True
            except Exception:  # noqa: BLE001 - a closing process means queue for the next turn
                return False

    def _spawn(self, work: Path) -> subprocess.Popen:
        env = harness_subprocess_env()
        # The app-server — and so the model's tools — run as the tools user, not as the worker.
        ident = tools_identity()
        if ident is not None:
            env["CODEX_HOME"] = str(_tools_codex_home(ident))
        return self._process_factory(
            ["codex", "app-server", "--stdio"], cwd=str(work), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env,
            **harness_identity_kwargs(),
        )

    def run_turn(self, work: Path, prompt: str, *, allowed_tools: Iterable[str] = (),
                 session: Optional[str] = None, model: Optional[str] = None,
                 mcp_config: Optional[str] = None) -> Iterator[dict]:
        proc = self._spawn(work)
        reply_parts: list[str] = []
        thread_id = session
        codex_model = ""
        with self._lock:
            self._proc = proc
            self._thread_id = session
            self._turn_id = None
        try:
            self._request(proc, "initialize", {
                "clientInfo": {"name": "vexa-agent", "title": "Vexa Agent", "version": "0.12"},
                "capabilities": {"experimentalApi": True},
            })
            self._write(proc, {"method": "initialized", "params": {}})

            config = _mcp_config(mcp_config, allowed_tools)
            common: dict = {
                "cwd": str(work), "approvalPolicy": "never",
                "developerInstructions": (
                    "Read and follow CLAUDE.md in the workspace before acting. "
                    "Treat it as this workspace's authoritative agent instructions."
                ),
            }
            # Settings → Models historically carries the Claude runner's model pin. Switching the
            # deployment runner must not feed `claude-*` into Codex; let the subscription choose its
            # account default unless an explicit Codex model is configured.
            codex_model = (os.environ.get("VEXA_CODEX_MODEL") or "").strip()
            if not codex_model and model and not model.lower().startswith("claude"):
                codex_model = model
            if codex_model:
                common["model"] = codex_model
            if config:
                common["config"] = config
            if session:
                result = self._request(proc, "thread/resume", {"threadId": session, **common})
            else:
                result = self._request(proc, "thread/start", {**common, "ephemeral": False})
            thread = result.get("thread") or {}
            thread_id = thread.get("id") or session
            if not thread_id:
                raise _RpcFailure("codex app-server returned no thread id")
            with self._lock:
                self._thread_id = thread_id

            request_id = self._id()
            self._write(proc, {
                "method": "turn/start", "id": request_id,
                "params": {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": prompt}],
                    # The runtime container is the enforcement boundary and mounts only the granted
                    # workspace set. Tell Codex it is externally sandboxed so nested sandboxing does
                    # not block additional granted mounts while approval prompts stay disabled.
                    "sandboxPolicy": {"type": "externalSandbox", "networkAccess": "restricted"},
                    "approvalPolicy": "never",
                },
            })
            while True:
                message = self._read(proc)
                if message.get("id") == request_id:
                    if message.get("error"):
                        raise _RpcFailure(_short(message["error"], 240), message["error"])
                    turn = (message.get("result") or {}).get("turn") or {}
                    with self._lock:
                        self._turn_id = turn.get("id")
                    continue
                for event in normalize_notification(message, reply_parts):
                    yield event
                if message.get("method") == "turn/completed":
                    turn = (message.get("params") or {}).get("turn") or {}
                    ok = turn.get("status") == "completed"
                    error = turn.get("error") or {}
                    reply = _final_reply(turn, reply_parts)
                    if ok:
                        yield {"type": "done", "reply": reply, "sessionId": thread_id, "ok": True}
                        return
                    said = error.get("message") if isinstance(error, dict) else None
                    failure = str(said or f"Codex turn {turn.get('status', 'failed')}")
                    yield _failed_done(reply, failure, thread_id, error, codex_model)
                    return
        except _RpcFailure as exc:
            yield _failed_done("", str(exc), thread_id,
                               exc.error if exc.error is not None else str(exc), codex_model)
        finally:
            with self._lock:
                self._proc = None
                self._thread_id = None
                self._turn_id = None
            try:
                if proc.stdin:
                    proc.stdin.close()
            except OSError:
                pass
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=3)
            except (subprocess.TimeoutExpired, TypeError):
                proc.kill()
