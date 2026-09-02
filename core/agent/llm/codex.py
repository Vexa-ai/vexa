"""Codex app-server HarnessPort adapter.

Codex's non-interactive ``exec`` command is a one-prompt process. Vexa uses ``app-server`` instead:
one JSON-RPC connection per turn, durable thread rollouts under the private continuity root, and
``turn/steer`` for user input that arrives while the turn is in flight. All Codex protocol details
stay in this vendor-named module; callers see only the frozen UnitEvent stream.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from llm.ports import harness_subprocess_env


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
        event = _tool_completed(params.get("item") or {})
        return [event] if event else []
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
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    auto = {name.removeprefix("mcp__") for name in allowed_tools if name.startswith("mcp__")}
    servers = raw.get("mcpServers") or {}
    selected = {name: spec for name, spec in servers.items() if name in auto}
    return {"mcp_servers": selected} if selected else {}


def _link_sessions_into_workspace(work: Path) -> None:
    """Keep Codex rollouts durable without moving the subscription auth file into the workspace."""
    # `.claude/` is the frozen, already-ignored agent plumbing root in every existing workspace.
    # Nest Codex state there so upgrading an old workspace cannot make continuity files/symlinks
    # visible to the turn's commit-all path.
    ws_sessions = work / ".claude" / "codex" / "sessions"
    ws_sessions.mkdir(parents=True, exist_ok=True)
    home_codex = Path(os.environ.get("HOME", "/root")) / ".codex"
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
    pass


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
        auth = Path(os.environ.get("HOME", "/root")) / ".codex" / "auth.json"
        try:
            if auth.is_file() and json.loads(auth.read_text(encoding="utf-8")):
                return None
        except (OSError, ValueError, TypeError):
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
                raise _RpcFailure(_short(message["error"], 240))
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
        return self._process_factory(
            ["codex", "app-server", "--stdio"], cwd=str(work), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env,
        )

    def run_turn(self, work: Path, prompt: str, *, allowed_tools: Iterable[str] = (),
                 session: Optional[str] = None, model: Optional[str] = None,
                 mcp_config: Optional[str] = None) -> Iterator[dict]:
        proc = self._spawn(work)
        reply_parts: list[str] = []
        thread_id = session
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
                        raise _RpcFailure(_short(message["error"], 240))
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
                    if not ok and not reply:
                        reply = error.get("message") or f"Codex turn {turn.get('status', 'failed')}"
                    yield {"type": "done", "reply": reply, "sessionId": thread_id, "ok": ok}
                    return
        except _RpcFailure as exc:
            yield {"type": "done", "reply": str(exc), "sessionId": thread_id, "ok": False}
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
