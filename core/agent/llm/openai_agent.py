"""openai_agent.py — OUR OWN agent loop over any OpenAI-compatible endpoint (``HarnessPort``).

PRD decision 37. The two harnesses that existed both drive a VENDOR CLI (`claude`, `codex`), so a
deployment that wants to run on a model we host ourselves — the CCC box serving Qwen3.8-27B over
vLLM — had nowhere to go: `openai_compat.py` is a COMPLETION port (prompt→text, no tools), and a
completion port cannot run this product's turns, which are tool loops from end to end.

This file is the missing third: the loop itself, in ~600 lines of raw httpx, no vendor SDK.

    POST {base}/chat/completions  with `tools` + `tool_choice: auto`
      → assistant text            → `message-delta`
      → assistant tool_calls      → `tool-call`, executed here, `tool-result`, fed back
      → no tool_calls             → `done`

Everything the rest of the system sees is the FROZEN UnitEvent stream of ``ports.py``, so the
terminal reducer, the SSE relay and the history reader cannot tell which harness produced a turn.
Three seams make that true and each one is load-bearing:

* **The transcript is written in the Claude Code shape** (`<chat_root>/.claude/projects/<slug>/
  <session>.jsonl`), because ``control_plane.workspace_reader.history`` reads that file — and the
  user turn is stored VERBATIM, the exact composed string the model was handed. That is what makes
  ``engine._prompt_key`` (a sha256 of those bytes) find the person's own words, and what makes the
  F51 phase mark and the machinery mark work: the reader tests for `[vexa-phase:writeback]` IN THE
  STORED PROMPT, so a harness that stored a paraphrase would silently un-hide every write-back
  exchange. This adapter therefore never rewrites, prefixes or trims the prompt on its way to disk.
* **The panel conventions are IMPORTED, not re-implemented** — the writer-tool tab, the
  `transcript_terms` chips (decision 35) and the `bot_send` transcript open (decision 30.4) come
  from ``llm.claude_code`` itself. A second spelling of those vocabularies is a second thing that
  can go stale; there is one.
* **MCP is attached from the SAME `mcp.json`** ``worker.engine.mcp_delegation_config`` already
  writes (the rig over streamable-http with `Authorization: Bearer <delegation token>`). Both
  transports of that file are supported — http for the rig, stdio for the offline eval stub — and
  the tools are exposed to the model under the same `mcp__<server>__<tool>` names the allow-set and
  the event conventions are written against.

WHAT IT DELIBERATELY DOES NOT HAVE. No `Bash`, no `WebSearch`/`WebFetch`, no skills discovery, no
mid-turn injection. The file tools are ours (`Read`/`Write`/`Edit`/`Glob`/`Grep`), minimal, and
sandboxed to the mount roots — a path outside them is refused rather than clamped, because a write
that silently lands somewhere else is worse than a failed call. Names in the allow-set that this
harness does not implement are simply not attached, and the turn says so through the tools it has.

SIZING (CCC-Inference-Deployment): the KV cache holds ~29 requests at 24k context, so the loop
carries a HARD per-turn budget — max tool calls, max wall seconds — and trims context (oldest tool
results first) to stay under ``VEXA_AGENT_CONTEXT_TOKENS``. A shared box is a shared box.

Config: ``VEXA_LLM_BASE_URL`` · ``VEXA_LLM_API_KEY`` (optional — CCC has no auth) ·
``VEXA_LLM_MODEL`` / ``VEXA_AGENT_MODEL`` · ``VEXA_LLM_EXTRA_BODY`` (merged into EVERY request —
Qwen needs `{"chat_template_kwargs":{"enable_thinking":false}}` or it reasons its whole budget away
and returns nothing parseable) · ``VEXA_AGENT_MAX_TOOL_CALLS`` · ``VEXA_AGENT_MAX_TURN_SEC`` ·
``VEXA_AGENT_CONTEXT_TOKENS`` · ``VEXA_AGENT_STREAM``.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Iterable, Iterator, Optional

import httpx

from llm.errors import LLMAuthError, LLMConfigError, LLMError
# The panel/chip/transcript vocabularies are the CLAUDE adapter's, imported rather than copied: the
# terminal must render an openai-agent turn identically, and two copies of a closed vocabulary drift.
from llm.claude_code import (_BOT_TOOLS, _TERMS_TOOLS, _WRITER_TOOLS, _bot_artifact,
                             _published_terms, _short, _written_artifact)
from llm.openai_compat import _parse_extra_body
from llm.ports import harness_subprocess_env

log = logging.getLogger(__name__)

# ── budgets ──────────────────────────────────────────────────────────────────────────────────────
# Defaults chosen against the CCC node's sizing table: ~29 concurrent requests at 24k context, and
# this product's turns are prefill-dominated (input is ~95% of the tokens moved). A turn that grows
# its own context without a ceiling is a turn that evicts everybody else's.
_DEFAULT_CONTEXT_TOKENS = 24_000
_DEFAULT_MAX_TOOL_CALLS = 40
_DEFAULT_MAX_TURN_SEC = 900.0
# A tool result is the one part of a turn that is both large and, once acted on, mostly spent — so
# it is what the trimmer eats first, and a stub is left in its place so the model can see that it
# read something rather than believing it never did.
_TRIM_STUB = "[trimmed: this tool result was dropped to stay inside the turn's context budget]"
_TOOL_RESULT_MAX_CHARS = 24_000     # one result can never be more than a third of a 24k budget


def _int_env(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, "") or default)
    except ValueError:
        return default


def _float_env(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, "") or default)
    except ValueError:
        return default


def _est_tokens(obj: object) -> int:
    """A chars/4 estimate. Deliberately crude and deliberately LOCAL: a tokenizer would be a model
    dependency in a module that must stay liftable, and the budget it guards is a safety margin on a
    shared box, not an accounting figure."""
    try:
        return max(1, len(json.dumps(obj, default=str)) // 4)
    except (TypeError, ValueError):
        return 1


# ── the MCP client (stdio + streamable-http), stdlib-shaped ─────────────────────────────────────

class _MCPServer:
    """One MCP server from the harness's `mcp.json`, in whichever transport it declares.

    Two transports because the two consumers need different ones and the file already expresses
    both: the worker writes `{"type":"http","url":…,"headers":{"Authorization":"Bearer …"}}` for the
    rig, and the offline eval attaches `{"type":"stdio","command":…,"args":[…]}` for the entity stub
    (#1414). Anything else is skipped with a warning — an unattachable server must not take the turn
    down, it must cost the turn that server's tools and say so in the log.
    """

    def __init__(self, name: str, spec: dict, *, timeout: float = 120.0,
                 client: Optional[httpx.Client] = None) -> None:
        self.name = name
        self._spec = spec or {}
        self._timeout = timeout
        self._id = 0
        self._proc: Optional[subprocess.Popen] = None
        self._http: Optional[httpx.Client] = client
        self._owns_http = client is None
        self._session_header: dict[str, str] = {}
        self.tools: list[dict] = []

    # -- transport --------------------------------------------------------------------------
    def _kind(self) -> str:
        t = str(self._spec.get("type") or "").strip().lower()
        if t:
            return t
        return "stdio" if self._spec.get("command") else ("http" if self._spec.get("url") else "")

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _rpc(self, method: str, params: Optional[dict] = None, *, notify: bool = False) -> dict:
        body: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            body["id"] = self._next_id()
        kind = self._kind()
        if kind == "stdio":
            return self._rpc_stdio(body, notify=notify)
        return self._rpc_http(body, notify=notify)

    def _rpc_stdio(self, body: dict, *, notify: bool) -> dict:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise LLMError(f"mcp {self.name}: stdio server is not running")
        proc.stdin.write(json.dumps(body) + "\n")
        proc.stdin.flush()
        if notify:
            return {}
        while True:
            line = proc.stdout.readline()
            if not line:
                raise LLMError(f"mcp {self.name}: stdio server closed while awaiting {body['method']}")
            try:
                msg = json.loads(line)
            except ValueError:
                continue                       # a server logging to stdout — skip, keep reading
            if msg.get("id") != body.get("id"):
                continue                       # a notification or an out-of-band message
            if msg.get("error"):
                raise LLMError(f"mcp {self.name}: {msg['error']}")
            return msg.get("result") or {}

    def _rpc_http(self, body: dict, *, notify: bool) -> dict:
        assert self._http is not None
        url = str(self._spec.get("url") or "")
        headers = {"Content-Type": "application/json",
                   # streamable-http may answer either way; both are handled below
                   "Accept": "application/json, text/event-stream"}
        headers.update({str(k): str(v) for k, v in (self._spec.get("headers") or {}).items()})
        headers.update(self._session_header)
        r = self._http.post(url, json=body, headers=headers)
        # The server's session id rides a RESPONSE header on initialize and must be echoed after.
        sid = r.headers.get("mcp-session-id") or r.headers.get("Mcp-Session-Id")
        if sid:
            self._session_header["Mcp-Session-Id"] = sid
        if r.status_code in (401, 403):
            raise LLMAuthError(f"mcp {self.name}: {r.status_code} from {url}: {r.text[:200]}")
        if r.status_code >= 400:
            raise LLMError(f"mcp {self.name}: {r.status_code} from {url}: {r.text[:200]}")
        if notify or r.status_code == 202 or not (r.content or b"").strip():
            return {}
        payload = self._decode_http(r, body.get("id"))
        if payload.get("error"):
            raise LLMError(f"mcp {self.name}: {payload['error']}")
        return payload.get("result") or {}

    @staticmethod
    def _decode_http(r: httpx.Response, want_id: object) -> dict:
        ctype = (r.headers.get("content-type") or "").lower()
        if "text/event-stream" in ctype:
            out: dict = {}
            for line in r.text.splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                try:
                    msg = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if isinstance(msg, dict) and (msg.get("id") == want_id or "result" in msg):
                    out = msg
            return out
        try:
            return r.json()
        except ValueError as exc:
            raise LLMError(f"mcp: malformed response: {exc}") from exc

    # -- lifecycle --------------------------------------------------------------------------
    def start(self) -> list[dict]:
        kind = self._kind()
        if kind == "stdio":
            argv = [str(self._spec.get("command") or ""), *[str(a) for a in (self._spec.get("args") or [])]]
            env = harness_subprocess_env()
            env.update({str(k): str(v) for k, v in (self._spec.get("env") or {}).items()})
            self._proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env)
        elif kind in ("http", "streamable-http", "sse"):
            if self._http is None:
                self._http = httpx.Client(timeout=self._timeout)
        else:
            raise LLMConfigError(f"mcp {self.name}: unsupported transport {kind!r}")
        self._rpc("initialize", {"protocolVersion": "2025-03-26",
                                 "capabilities": {},
                                 "clientInfo": {"name": "vexa-openai-agent", "version": "1"}})
        try:
            self._rpc("notifications/initialized", {}, notify=True)
        except LLMError:
            pass                                # a server that does not want the notification
        listed = self._rpc("tools/list", {})
        self.tools = [t for t in (listed.get("tools") or []) if isinstance(t, dict) and t.get("name")]
        return self.tools

    def call(self, tool: str, args: dict) -> tuple[bool, str]:
        """``(ok, text)`` for one `tools/call`. A transport failure is a FAILED TOOL, never an
        exception out of the loop: the model is told the call failed and can choose again, which is
        the whole point of running a loop rather than a pipeline."""
        try:
            res = self._rpc("tools/call", {"name": tool, "arguments": args})
        except (LLMError, OSError, ValueError) as exc:
            return False, f"{type(exc).__name__}: {exc}"
        text = "".join(b.get("text", "") for b in (res.get("content") or [])
                       if isinstance(b, dict) and b.get("type") == "text")
        if not text:
            text = json.dumps(res.get("structuredContent") or res, default=str)
        return (not res.get("isError")), text

    def close(self) -> None:
        if self._proc is not None:
            for stream in (self._proc.stdin, self._proc.stdout):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
            self._proc = None
        if self._http is not None and self._owns_http:
            self._http.close()
            self._http = None


def _load_mcp(mcp_config: Optional[str], *, http_client: Optional[httpx.Client] = None
              ) -> tuple[list[_MCPServer], dict[str, tuple[_MCPServer, str]]]:
    """Attach every server in the Claude-shaped `mcp.json` → (servers, function-name → (server, tool)).

    The exposed function name is `mcp__<server>__<tool>` — the SAME name claude-code gives it, which
    is what the allow-set (`worker.engine.VEXA_MCP_TOOLS`) and the panel conventions above are
    written against. OpenAI function names are capped at 64 chars; a longer one is truncated with a
    short digest so it stays unique and still callable."""
    servers: list[_MCPServer] = []
    index: dict[str, tuple[_MCPServer, str]] = {}
    if not mcp_config:
        return servers, index
    try:
        cfg = json.loads(Path(mcp_config).read_text())
    except (OSError, ValueError) as exc:
        log.warning("mcp config unreadable (%s) — running this turn without the toolbelt", exc)
        return servers, index
    for name, spec in (cfg.get("mcpServers") or {}).items():
        srv = _MCPServer(str(name), spec if isinstance(spec, dict) else {}, client=http_client)
        try:
            tools = srv.start()
        except (LLMError, LLMConfigError, LLMAuthError, OSError) as exc:
            log.warning("mcp %s: could not attach (%s) — the turn runs without its tools", name, exc)
            srv.close()
            continue
        servers.append(srv)
        for t in tools:
            index[_fn_name(str(name), str(t["name"]))] = (srv, str(t["name"]))
    return servers, index


def _fn_name(server: str, tool: str) -> str:
    name = f"mcp__{server}__{tool}"
    if len(name) <= 64:
        return name
    import hashlib
    return name[:56] + "_" + hashlib.sha256(name.encode()).hexdigest()[:7]


def _mcp_specs(index: dict[str, tuple[_MCPServer, str]]) -> list[dict]:
    specs = []
    for fn, (srv, tool) in index.items():
        schema = next((t.get("inputSchema") for t in srv.tools if t.get("name") == tool), None)
        desc = next((t.get("description") for t in srv.tools if t.get("name") == tool), "") or ""
        specs.append({"type": "function", "function": {
            "name": fn, "description": desc[:1024],
            "parameters": schema if isinstance(schema, dict) else {"type": "object", "properties": {}},
        }})
    return specs


# ── the built-in file tools (sandboxed to the mount roots) ──────────────────────────────────────

BUILTIN_SPECS: dict[str, dict] = {
    "Read": {"description": "Read a file from the mounted workspace. Absolute path under a mount.",
             "parameters": {"type": "object", "properties": {
                 "file_path": {"type": "string"},
                 "offset": {"type": "integer", "description": "1-based first line"},
                 "limit": {"type": "integer"}}, "required": ["file_path"]}},
    "Write": {"description": "Write a file in the mounted workspace, creating parent directories. "
                             "Overwrites. Absolute path under a mount.",
              "parameters": {"type": "object", "properties": {
                  "file_path": {"type": "string"}, "content": {"type": "string"}},
                  "required": ["file_path", "content"]}},
    "Edit": {"description": "Replace an exact string in a file. Fails if old_string is absent or "
                            "appears more than once and replace_all is false.",
             "parameters": {"type": "object", "properties": {
                 "file_path": {"type": "string"}, "old_string": {"type": "string"},
                 "new_string": {"type": "string"}, "replace_all": {"type": "boolean"}},
                 "required": ["file_path", "old_string", "new_string"]}},
    "Glob": {"description": "List files matching a glob pattern (** supported) under a directory.",
             "parameters": {"type": "object", "properties": {
                 "pattern": {"type": "string"}, "path": {"type": "string"}},
                 "required": ["pattern"]}},
    "Grep": {"description": "Search file contents for a regular expression under a directory.",
             "parameters": {"type": "object", "properties": {
                 "pattern": {"type": "string"}, "path": {"type": "string"},
                 "glob": {"type": "string"}, "case_insensitive": {"type": "boolean"}},
                 "required": ["pattern"]}},
}

_GREP_MAX_HITS = 200
_READ_MAX_CHARS = 100_000
_GLOB_MAX = 400


def mount_roots(work: Path) -> list[Path]:
    """Every directory this harness's file tools may touch: the turn's cwd plus each declared mount.

    Read from ``VEXA_MOUNTS`` the same way ``worker.engine.active_mounts`` does — by ENV, not by
    import, because this module owns no product imports. A malformed value costs the extra mounts,
    never the turn."""
    roots = [work.resolve()]
    raw = os.environ.get("VEXA_MOUNTS") or ""
    if raw:
        try:
            for m in json.loads(raw):
                if isinstance(m, dict) and m.get("path"):
                    roots.append(Path(str(m["path"])).resolve())
        except (ValueError, TypeError, OSError):
            log.warning("VEXA_MOUNTS is not valid JSON — file tools are scoped to the cwd only")
    out: list[Path] = []
    for r in roots:
        if r not in out:
            out.append(r)
    return out


class _Sandbox:
    """Path resolution that REFUSES rather than clamps. A tool call aimed outside the mounts is a
    mistake worth telling the model about — a silently rewritten path writes the right bytes to the
    wrong workspace, which is the one failure nobody can see afterwards."""

    def __init__(self, roots: list[Path]) -> None:
        self._roots = roots

    def resolve(self, raw: str) -> Path:
        if not raw:
            raise ValueError("no path given")
        p = Path(raw)
        if not p.is_absolute():
            p = self._roots[0] / p
        # resolve() without strict so a not-yet-existing file still normalises (Write creates it)
        p = Path(os.path.normpath(str(p)))
        try:
            real = p.resolve()
        except OSError:
            real = p
        for root in self._roots:
            if real == root or root in real.parents:
                return real
        raise ValueError(f"path {raw} is outside the mounted workspaces "
                         f"({', '.join(str(r) for r in self._roots)})")


def run_builtin(tool: str, args: dict, sandbox: _Sandbox) -> tuple[bool, str]:
    """One built-in file tool → ``(ok, text)``. Never raises: a bad call is a failed tool result."""
    try:
        if tool == "Read":
            path = sandbox.resolve(str(args.get("file_path") or ""))
            text = path.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            start = max(0, int(args.get("offset") or 1) - 1)
            limit = int(args.get("limit") or 2000)
            chunk = "\n".join(lines[start:start + limit])
            return True, chunk[:_READ_MAX_CHARS]
        if tool == "Write":
            path = sandbox.resolve(str(args.get("file_path") or ""))
            path.parent.mkdir(parents=True, exist_ok=True)
            content = args.get("content")
            path.write_text("" if content is None else str(content), encoding="utf-8")
            return True, f"wrote {path}"
        if tool == "Edit":
            path = sandbox.resolve(str(args.get("file_path") or ""))
            old, new = str(args.get("old_string") or ""), str(args.get("new_string") or "")
            text = path.read_text(encoding="utf-8")
            hits = text.count(old)
            if not old or hits == 0:
                return False, "old_string not found in the file"
            if hits > 1 and not args.get("replace_all"):
                return False, f"old_string appears {hits} times — pass replace_all or extend it"
            path.write_text(text.replace(old, new) if args.get("replace_all")
                            else text.replace(old, new, 1), encoding="utf-8")
            return True, f"edited {path}"
        if tool == "Glob":
            base = sandbox.resolve(str(args.get("path") or "")) if args.get("path") else sandbox.resolve(".")
            pattern = str(args.get("pattern") or "*")
            hits = sorted(str(p) for p in base.glob(pattern) if p.is_file())[:_GLOB_MAX]
            return True, "\n".join(hits) if hits else "(no matches)"
        if tool == "Grep":
            base = sandbox.resolve(str(args.get("path") or "")) if args.get("path") else sandbox.resolve(".")
            flags = re.IGNORECASE if args.get("case_insensitive") else 0
            rx = re.compile(str(args.get("pattern") or ""), flags)
            keep = str(args.get("glob") or "")
            out: list[str] = []
            for f in sorted(base.rglob("*")):
                if len(out) >= _GREP_MAX_HITS:
                    break
                if not f.is_file() or ".git" in f.parts:
                    continue
                if keep and not fnmatch.fnmatch(f.name, keep):
                    continue
                try:
                    for n, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                        if rx.search(line):
                            out.append(f"{f}:{n}:{line.strip()[:200]}")
                            if len(out) >= _GREP_MAX_HITS:
                                break
                except OSError:
                    continue
            return True, "\n".join(out) if out else "(no matches)"
    except (OSError, ValueError, re.error) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return False, f"unknown tool {tool}"


def _allowed(name: str, allow: set[str]) -> bool:
    """The allow-set semantics claude-code's ``--allowedTools`` has: an exact name, or the MCP
    server prefix (`mcp__vexa`) standing for all of its tools. An EMPTY set means unrestricted —
    the same as passing no flag."""
    if not allow:
        return True
    if name in allow:
        return True
    if name.startswith("mcp__"):
        server = name.split("__")[1] if len(name.split("__")) > 2 else ""
        return f"mcp__{server}" in allow
    return False


# ── the transcript (Claude Code's on-disk shape, so history keeps working) ──────────────────────

class _Transcript:
    """The session store: one JSONL per session under ``.claude/projects/<cwd-slug>/<sid>.jsonl``.

    The SHAPE is Claude Code's, because ``workspace_reader.history`` is the reader on the other end
    and its parser is the frozen contract, not this file's convenience. Each record additionally
    carries an ``oa`` field — the raw OpenAI-dialect message — so a resume rebuilds the conversation
    losslessly instead of re-deriving it from a rendering. The reader ignores fields it does not
    know; that is the whole trick."""

    def __init__(self, chat_root: Path, work: Path, session_id: str) -> None:
        slug = str(work.resolve()).replace("/", "-")
        self.dir = chat_root / ".claude" / "projects" / slug
        self.session_id = session_id
        self.path = self.dir / f"{session_id}.jsonl"

    def exists(self) -> bool:
        if self.path.exists():
            return True
        # the sid may have been written under another cwd-slug (a mount that moved) — accept it
        parent = self.dir.parent
        return parent.exists() and any(parent.glob(f"*/{self.session_id}.jsonl"))

    def _resolved(self) -> Path:
        if self.path.exists():
            return self.path
        for cand in self.dir.parent.glob(f"*/{self.session_id}.jsonl"):
            return cand
        return self.path

    def append(self, record: dict) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with self._resolved().open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            log.warning("could not append to the session transcript (%s) — history will be short", exc)

    def messages(self) -> list[dict]:
        """The prior conversation as OpenAI messages (from the ``oa`` field), or [] if unreadable."""
        out: list[dict] = []
        try:
            raw = self._resolved().read_text(encoding="utf-8")
        except OSError:
            return out
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            msg = rec.get("oa")
            if isinstance(msg, dict) and msg.get("role"):
                out.append(msg)
        return out

    def record_user(self, text: str) -> None:
        """VERBATIM. See the module docstring: the phase mark, the machinery mark and
        ``engine._prompt_key`` all read THIS string."""
        self.append({"type": "user", "sessionId": self.session_id,
                     "message": {"role": "user", "content": [{"type": "text", "text": text}]},
                     "oa": {"role": "user", "content": text}})

    def record_assistant(self, text: str, calls: list[dict], oa: dict) -> None:
        blocks: list[dict] = []
        if text:
            blocks.append({"type": "text", "text": text})
        for c in calls:
            blocks.append({"type": "tool_use", "id": c["id"], "name": c["name"],
                           "input": c["args"]})
        self.append({"type": "assistant", "sessionId": self.session_id,
                     "message": {"role": "assistant", "content": blocks}, "oa": oa})

    def record_tool_result(self, call_id: str, ok: bool, text: str) -> None:
        self.append({"type": "user", "sessionId": self.session_id,
                     "message": {"role": "user", "content": [
                         {"type": "tool_result", "tool_use_id": call_id,
                          "content": text, "is_error": not ok}]},
                     "oa": {"role": "tool", "tool_call_id": call_id, "content": text}})


# ── context trimming ────────────────────────────────────────────────────────────────────────────

def trim_messages(messages: list[dict], budget: int) -> tuple[list[dict], int]:
    """Fit ``messages`` under ``budget`` estimated tokens. Returns (messages, trimmed_count).

    Order of sacrifice, oldest first: tool RESULTS (replaced by a stub, so the model still sees that
    the call happened), then whole oldest exchanges, and only then the head of the first user
    message. The LAST user message is never touched — it is the ask, and a turn that trims the ask
    answers a question nobody put."""
    msgs = [dict(m) for m in messages]
    if _est_tokens(msgs) <= budget:
        return msgs, 0
    trimmed = 0
    for m in msgs:                                     # 1) oldest tool results → stub
        if _est_tokens(msgs) <= budget:
            break
        if m.get("role") == "tool" and m.get("content") != _TRIM_STUB:
            m["content"] = _TRIM_STUB
            trimmed += 1
    last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=0)
    while _est_tokens(msgs) > budget:                  # 2) drop oldest non-final messages
        drop = next((i for i, m in enumerate(msgs)
                     if i != last_user and i != 0 and m.get("role") != "system"), None)
        if drop is None:
            break
        msgs.pop(drop)
        last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=0)
        trimmed += 1
    if _est_tokens(msgs) > budget and len(msgs) > 1:   # 3) last resort: head-truncate the opener
        head = msgs[0]
        content = str(head.get("content") or "")
        keep = max(1000, budget * 2)
        if len(content) > keep:
            head["content"] = content[:keep] + "\n\n[…trimmed to fit the turn's context budget]"
            trimmed += 1
    return msgs, trimmed


# ── the harness ─────────────────────────────────────────────────────────────────────────────────

class OpenAIAgentHarness:
    """``HarnessPort`` adapter: our own agent loop over an OpenAI-compatible endpoint."""

    name = "openai-agent"

    def __init__(self, *, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 model: Optional[str] = None, extra_body: Optional[dict] = None,
                 timeout: float = 300.0, transport: Optional[httpx.BaseTransport] = None,
                 mcp_http_client: Optional[httpx.Client] = None) -> None:
        self._base = (base_url or os.environ.get("VEXA_LLM_BASE_URL")
                      or os.environ.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
        self._key = (api_key or os.environ.get("VEXA_LLM_API_KEY")
                     or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                     or os.environ.get("ANTHROPIC_API_KEY") or "")
        self._model = model or os.environ.get("VEXA_LLM_MODEL") or ""
        self._extra = _parse_extra_body(extra_body if extra_body is not None
                                        else os.environ.get("VEXA_LLM_EXTRA_BODY"))
        self._client = httpx.Client(timeout=timeout, transport=transport)
        self._mcp_http = mcp_http_client
        self._chat_root: Optional[Path] = None

    # -- port surface -----------------------------------------------------------------------
    def prepare(self, work: Path, chat_root: Optional[Path] = None) -> None:
        """Remember where continuity lives. claude-code does this with a `~/.claude/projects`
        symlink because the CLI decides where to write; we write the file ourselves, so the whole
        of `prepare` is holding on to the root the engine already computed (`_system` when the
        dispatch declares one — chats are private and must not land on a shared mount)."""
        self._chat_root = Path(chat_root or work)
        try:
            (self._chat_root / ".claude" / "projects").mkdir(parents=True, exist_ok=True)
        except OSError:
            pass                                  # a read-only mount: the turn still runs

    def transcript_bytes(self, work: Path, session_id: str) -> int:
        total = 0
        for path in (work / ".claude" / "projects").glob(f"*/{session_id}.jsonl"):
            try:
                total += path.stat().st_size
            except OSError:
                continue
        return total

    def preflight(self) -> Optional[str]:
        if not self._base:
            return ("openai-agent: no VEXA_LLM_BASE_URL — every workspace turn will fail until an "
                    "OpenAI-compatible endpoint is set")
        if not (self._model or os.environ.get("VEXA_AGENT_MODEL")):
            return "openai-agent: no VEXA_LLM_MODEL — the endpoint will be asked for an empty model"
        return None

    def midturn_enabled(self) -> bool:
        return False                              # one request at a time; nothing to inject into

    def inject_user_message(self, text: str) -> bool:
        return False

    def run_turn(self, work: Path, prompt: str, *, allowed_tools: Iterable[str] = (),
                 session: Optional[str] = None, model: Optional[str] = None,
                 mcp_config: Optional[str] = None) -> Iterator[dict]:
        sid = session or uuid.uuid4().hex
        try:
            yield from self._loop(Path(work), prompt, set(allowed_tools), session, sid, model,
                                  mcp_config)
        except (LLMConfigError, LLMAuthError) as exc:
            yield {"type": "done", "reply": str(exc), "sessionId": sid, "ok": False}
        except LLMError as exc:
            yield {"type": "done", "reply": f"Model inference failed: {exc}", "sessionId": sid,
                   "ok": False}

    # -- the loop ---------------------------------------------------------------------------
    def _loop(self, work: Path, prompt: str, allow: set[str], resume: Optional[str], sid: str,
              model: Optional[str], mcp_config: Optional[str]) -> Iterator[dict]:
        target = (model or "").strip() or self._model or os.environ.get("VEXA_AGENT_MODEL") or ""
        if not self._base:
            raise LLMConfigError(
                "no agent endpoint: set VEXA_LLM_BASE_URL (e.g. http://192.168.1.6:8001/v1) — the "
                "openai-agent runner has no default host")
        if not target:
            raise LLMConfigError("no model: set VEXA_LLM_MODEL (or VEXA_AGENT_MODEL)")

        chat_root = self._chat_root or work
        store = _Transcript(chat_root, work, sid)
        if resume and not store.exists():
            # An alien/stale id must yield done.ok=False — the engine's stale-resume retry heals it
            # by calling again with session=None. Anything else forks the conversation silently.
            yield {"type": "done", "reply": "unknown session", "sessionId": sid, "ok": False}
            return
        messages: list[dict] = store.messages() if resume else []
        messages.append({"role": "user", "content": prompt})
        store.record_user(prompt)

        servers, mcp_index = _load_mcp(mcp_config, http_client=self._mcp_http)
        try:
            specs = [{"type": "function", "function": {"name": n, **BUILTIN_SPECS[n]}}
                     for n in BUILTIN_SPECS if _allowed(n, allow)]
            specs += [s for s in _mcp_specs(mcp_index) if _allowed(s["function"]["name"], allow)]

            budget_calls = _int_env("VEXA_AGENT_MAX_TOOL_CALLS", _DEFAULT_MAX_TOOL_CALLS)
            budget_secs = _float_env("VEXA_AGENT_MAX_TURN_SEC", _DEFAULT_MAX_TURN_SEC)
            ctx_budget = _int_env("VEXA_AGENT_CONTEXT_TOKENS", _DEFAULT_CONTEXT_TOKENS)
            started, calls_made, reply = time.monotonic(), 0, ""
            sandbox = _Sandbox(mount_roots(work))

            while True:
                sent, trimmed = trim_messages(messages, ctx_budget)
                if trimmed:
                    yield {"type": "context-trimmed", "dropped": trimmed,
                           "tokens": _est_tokens(sent), "budget": ctx_budget}
                    messages = sent
                text = ""
                calls: list[dict] = []
                oa: dict = {}
                for ev in self._complete(sent, specs, target):
                    if "__final__" in ev:
                        oa = ev["__final__"]
                        text = str(oa.get("content") or "")
                        calls = _tool_calls_of(oa)
                        break
                    yield ev
                    if ev.get("type") == "message-delta":
                        text += ev.get("text", "")
                if not calls:
                    store.record_assistant(text, [], oa or {"role": "assistant", "content": text})
                    reply = text
                    break
                store.record_assistant(text, calls, oa)
                messages.append(oa)
                over_budget = False
                for i, call in enumerate(calls):
                    if calls_made >= budget_calls or (time.monotonic() - started) > budget_secs:
                        over_budget = True
                        reason = "tool-call budget" if calls_made >= budget_calls else "time budget"
                        yield {"type": "turn-truncated", "reason": reason,
                               "calls": calls_made, "seconds": round(time.monotonic() - started, 1)}
                        # EVERY call the model made is answered, refusals included. An assistant
                        # message whose tool_calls have no matching tool message is a MALFORMED
                        # request to the next round trip, and on a resumed session that malformation
                        # outlives the turn that made it.
                        for skipped in calls[i:]:
                            refusal = f"not run: the turn hit its {reason}"
                            yield {"type": "tool-result", "callId": skipped["id"], "ok": False,
                                   "summary": refusal}
                            store.record_tool_result(skipped["id"], False, refusal)
                            messages.append({"role": "tool", "tool_call_id": skipped["id"],
                                             "content": refusal})
                        break
                    calls_made += 1
                    yield {"type": "tool-call", "tool": call["name"], "args": call["args"],
                           "callId": call["id"]}
                    ok, out = self._exec_tool(call, mcp_index, sandbox)
                    out = out[:_TOOL_RESULT_MAX_CHARS]
                    yield {"type": "tool-result", "callId": call["id"], "ok": ok,
                           "summary": _short(out)}
                    for extra in _panel_events(call, ok, out):
                        yield extra
                    store.record_tool_result(call["id"], ok, out)
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": out})
                if over_budget:
                    reply = text
                    break
            yield {"type": "done", "reply": reply, "sessionId": sid, "ok": True}
        finally:
            for srv in servers:
                srv.close()

    def _exec_tool(self, call: dict, mcp_index: dict, sandbox: _Sandbox) -> tuple[bool, str]:
        name, args = call["name"], call["args"]
        if name in mcp_index:
            srv, tool = mcp_index[name]
            return srv.call(tool, args if isinstance(args, dict) else {})
        if name in BUILTIN_SPECS:
            return run_builtin(name, args if isinstance(args, dict) else {}, sandbox)
        return False, (f"no tool named {name} is attached to this turn — the attached tools are "
                       f"{sorted(set(BUILTIN_SPECS) | set(mcp_index))}")

    def _complete(self, messages: list[dict], specs: list[dict], model: str) -> Iterator[dict]:
        """One `chat/completions` round trip. Yields ``message-delta`` events while the text
        streams, then a single ``{"__final__": <assistant message>}``."""
        body = {**self._extra, "model": model, "messages": messages}   # reserved keys always win
        if specs:
            body["tools"] = specs
            body["tool_choice"] = "auto"
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        stream = (os.environ.get("VEXA_AGENT_STREAM", "1") or "1").strip() not in ("0", "false", "no")
        if not stream:
            yield from self._complete_blocking(body, headers)
            return
        body = {**body, "stream": True}
        acc_text, acc_calls = "", {}
        try:
            with self._client.stream("POST", f"{self._base}/chat/completions", json=body,
                                     headers=headers) as r:
                if r.status_code >= 400:
                    r.read()
                    self._raise_http(r)
                for line in r.iter_lines():
                    line = (line or "").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    delta = ((chunk.get("choices") or [{}])[0] or {}).get("delta") or {}
                    if delta.get("content"):
                        acc_text += delta["content"]
                        yield {"type": "message-delta", "text": delta["content"]}
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        slot = acc_calls.setdefault(idx, {"id": "", "type": "function",
                                                          "function": {"name": "", "arguments": ""}})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]
        except httpx.HTTPError as exc:
            raise LLMError(f"agent transport failure against {self._base}: {exc}") from exc
        msg: dict = {"role": "assistant", "content": acc_text}
        if acc_calls:
            msg["tool_calls"] = [acc_calls[k] for k in sorted(acc_calls)]
        yield {"__final__": msg}

    def _complete_blocking(self, body: dict, headers: dict) -> Iterator[dict]:
        try:
            r = self._client.post(f"{self._base}/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise LLMError(f"agent transport failure against {self._base}: {exc}") from exc
        if r.status_code >= 400:
            self._raise_http(r)
        try:
            msg = ((r.json().get("choices") or [{}])[0] or {}).get("message") or {}
        except (ValueError, AttributeError, TypeError) as exc:
            raise LLMError(f"malformed completion payload from {self._base}: {exc}") from exc
        text = str(msg.get("content") or "")
        if text:
            yield {"type": "message-delta", "text": text}
        yield {"__final__": {"role": "assistant", "content": text,
                             **({"tool_calls": msg["tool_calls"]} if msg.get("tool_calls") else {})}}

    def _raise_http(self, r: httpx.Response) -> None:
        detail = (r.text or "")[:300]
        if r.status_code in (401, 403):
            raise LLMAuthError(
                f"{r.status_code} from {self._base}: {detail} — set VEXA_LLM_API_KEY for this "
                f"endpoint, or point VEXA_LLM_BASE_URL at one that needs no credential")
        raise LLMError(f"{r.status_code} from {self._base}: {detail}")


def _tool_calls_of(msg: dict) -> list[dict]:
    """The assistant message's tool calls as ``{id, name, args}``.

    A model that returns unparseable arguments is COMMON on smaller models and must not take the
    turn down: the call is kept with `{}` arguments plus the raw text, so the tool fails loudly and
    the model gets a chance to correct itself — which is exactly what a loop is for."""
    out: list[dict] = []
    for i, tc in enumerate(msg.get("tool_calls") or []):
        fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
        raw = fn.get("arguments")
        if isinstance(raw, dict):
            args = raw
        else:
            try:
                args = json.loads(raw or "{}")
            except ValueError:
                args = {"__unparsed_arguments__": str(raw)[:500]}
        if not isinstance(args, dict):
            args = {"__unparsed_arguments__": str(args)[:500]}
        out.append({"id": str(tc.get("id") or f"call_{i}"), "name": str(fn.get("name") or ""),
                    "args": args})
    return out


def _panel_events(call: dict, ok: bool, out: str) -> list[dict]:
    """The panel moves a successful call earns — the SAME three conventions ``claude_code`` applies,
    through its own helpers: the writer's tab, decision 35's chips, decision 30.4's transcript."""
    if not ok:
        return []                                  # success only: a failed call must move nothing
    name = call["name"]
    events: list[dict] = []
    if name in _WRITER_TOOLS:
        target = _written_artifact(name, call["args"])
        if target:
            events.append({"type": "artifact", "workspace": target[0], "path": target[1],
                           "focus": True})
    elif name in _TERMS_TOOLS:
        ev = _published_terms(out)
        if ev:
            events.append(ev)
    elif name in _BOT_TOOLS:
        ev = _bot_artifact(out)
        if ev:
            events.append(ev)
    return events
