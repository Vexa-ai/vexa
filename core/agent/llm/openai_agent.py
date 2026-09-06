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

WHAT IT DELIBERATELY DOES NOT HAVE. No `Bash`, no skills discovery, no mid-turn injection. The file
tools are ours (`Read`/`Write`/`Edit`/`Glob`/`Grep`), minimal, and sandboxed to the mount roots — a
path outside them is refused rather than clamped, because a write that silently lands somewhere else
is worse than a failed call. Names in the allow-set that this harness does not implement are simply
not attached, and the turn says so through the tools it has.

WHAT IT GAINED, AND WHY IT IS AN ADAPTER. `WebSearch` and `WebFetch` (``llm/web_tools.py``), because
the onboarding playbook has said *research first, ask last* since it shipped and under this harness
there was nothing behind the sentence — the first admin walking a blank instance on our own model
reported it as "my research tools here can't reach the open web". Search speaks to an endpoint THE
OPERATOR RUNS (``VEXA_SEARCH_URL`` + ``VEXA_SEARCH_DIALECT``) and is NOT attached when none is
configured: no search engine ships with this product, which is a licence decision (the obvious
self-hosted one is AGPL-3.0) as much as a deployment one. `WebFetch` needs no backend, is therefore
always attached, and refuses any URL that resolves into the deployment's own network.

SIZING (CCC-Inference-Deployment): the KV cache holds ~29 requests at 24k context, so the loop
carries a HARD per-turn budget — max tool calls, max wall seconds — and trims context (oldest tool
results first) to stay under ``VEXA_AGENT_CONTEXT_TOKENS``. A shared box is a shared box.

A JOB IS NOT A TURN (Vexa-ai/vexa#1613). The sizing above is about how much of the box ONE request
may hold at once — context and concurrency — and says nothing about how many times a piece of work
may come back for another one. An expand-in-every-direction job routinely needs more round trips
than a chat turn does: the founder's OeNB job ran 72 steps and then died on the 40-call per-turn
budget, with everything it had already written on disk. So a job gets its own, larger budget
(``VEXA_AGENT_JOB_MAX_TOOL_CALLS``, per window) and, on reaching it, does not fail: the pages it
wrote are already committed, it says how far it got, and it CONTINUES IN A FRESH WINDOW over the
same brief. It fails only when a window makes no progress at all, or when the job's own wall clock
(``VEXA_AGENT_JOB_MAX_TURN_SEC``) runs out — the outer bound, because a window that keeps making one
call would otherwise never end.

Both job dials are floored at the turn's, so an operator who raises ``VEXA_AGENT_MAX_TOOL_CALLS`` on
the containers as a stopgap never accidentally gives a job LESS than a turn.

AND A TURN IS NOT A TURN EITHER (Vexa-ai/vexa#1622). #1613 split the job off the chat turn and left
the other three sharing one number: a chat sentence, a post-meeting room run and a flow step are
different amounts of work and were all billed at 40 calls. So the budget is a TABLE keyed by
``llm.jobs.turn_kind()`` — see ``_KIND_MAX_TOOL_CALLS`` — read from ``VEXA_AGENT_MAX_TOOL_CALLS_<KIND>``
with the single ``VEXA_AGENT_MAX_TOOL_CALLS`` as the fallback for all four, so a deployment that
sets only the old name behaves exactly as it did.

WHAT A TURN THAT SPENDS ITS BUDGET NOW DOES, which is the defect this issue is actually about: it
SAYS SO. Four friction reports were auto-filed from the founder's own chats on 2026-09-06 while he
built the OeNB workspace — three in a row in one conversation — because the chat showed a finished
turn and he re-prompted into the same wall each time. The `done` event therefore carries the line
(*stopped at the tool-call budget after N of M steps*), the step count, and the Continue act the
person presses to queue "continue where you stopped" back onto the same target. A job checkpoints
and carries on by itself; a turn asks, because a turn is a person waiting.

Config: ``VEXA_LLM_BASE_URL`` · ``VEXA_LLM_API_KEY`` (optional — CCC has no auth) ·
``VEXA_LLM_MODEL`` / ``VEXA_AGENT_MODEL`` · ``VEXA_LLM_EXTRA_BODY`` (merged into EVERY request —
Qwen needs `{"chat_template_kwargs":{"enable_thinking":false}}` or it reasons its whole budget away
and returns nothing parseable) · ``VEXA_AGENT_MAX_TOOL_CALLS`` (+ the per-kind
``VEXA_AGENT_MAX_TOOL_CALLS_CHAT`` / ``_JOB`` / ``_ROOM`` / ``_FLOW``) · ``VEXA_AGENT_MAX_TURN_SEC`` ·
``VEXA_AGENT_CONTEXT_TOKENS`` · ``VEXA_AGENT_STREAM`` · ``VEXA_SEARCH_URL`` ·
``VEXA_SEARCH_DIALECT`` · ``VEXA_SEARCH_API_KEY``.
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
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Iterator, Optional

import httpx

from llm import jobs as llm_jobs
from llm.errors import LLMAuthError, LLMConfigError, LLMError
# The panel/chip/transcript vocabularies are the CLAUDE adapter's, imported rather than copied: the
# terminal must render an openai-agent turn identically, and two copies of a closed vocabulary drift.
from llm.claude_code import (_BOT_TOOLS, _FOCUS_TOOLS, _OPEN_TOOLS, _TERMS_TOOLS, _WRITER_TOOLS, _bot_artifact,
                             _open_event, _published_terms, _short, _workspace_focus,
                             _written_artifact)
from llm.ports import harness_subprocess_env
from llm import jobs, web_tools


def _parse_extra_body(raw: object) -> dict:
    """Parse ``VEXA_LLM_EXTRA_BODY``. A malformed value is a CONFIG error, never a silent no-op:
    a deployment that believes it disabled thinking and did not would fail as bad output, far
    from the cause.

    It lived in ``llm/openai_compat.py`` until PRD decision 34 removed the completion pipeline and
    that module with it. Nothing about parsing this variable was completion-specific — the harness
    is now its only reader, so it lives here rather than in a module kept alive for one function."""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        parsed = json.loads(str(raw))
    except ValueError as exc:
        raise LLMConfigError(f"VEXA_LLM_EXTRA_BODY is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMConfigError("VEXA_LLM_EXTRA_BODY must be a JSON object")
    return parsed


log = logging.getLogger(__name__)

# ── budgets ──────────────────────────────────────────────────────────────────────────────────────
# Defaults chosen against the CCC node's sizing table: ~29 concurrent requests at 24k context, and
# this product's turns are prefill-dominated (input is ~95% of the tokens moved). A turn that grows
# its own context without a ceiling is a turn that evicts everybody else's.
_DEFAULT_CONTEXT_TOKENS = 24_000
_DEFAULT_MAX_TOOL_CALLS = 40
_DEFAULT_MAX_TURN_SEC = 900.0
#: A BACKGROUND JOB's budgets (Vexa-ai/vexa#1613) — per WINDOW for the calls, whole-job for the
#: clock. 160 is four turns' worth: above the 72 steps the OeNB job reached before it was killed,
#: and low enough that one job cannot hold the box indefinitely between checkpoints.
_DEFAULT_JOB_MAX_TOOL_CALLS = 160
_DEFAULT_JOB_MAX_TURN_SEC = 3600.0
#: THE BUDGET IS PER KIND OF TURN (Vexa-ai/vexa#1622). One number for four shapes of work is how a
#: chat turn came to be billed the same as a whole post-meeting run, and the number was sized for
#: the shortest of them. Each row is a DEFAULT, overridable per kind and overridable for all four —
#: see ``_calls_budget``, which is where the precedence is written down.
#:
#:   chat  a sentence and its follow-through. The historical 40.
#:   job   Create/Extend, per window (#1613). Reaching it is a checkpoint, not a death.
#:   room  one pass over a finished meeting: read the transcript, write several pages, connect
#:         entities. Not a person waiting on a reply, and measurably longer than a sentence.
#:   flow  one step of a flow (#1605). Machinery, scoped by the step's own brief.
_KIND_MAX_TOOL_CALLS: dict[str, int] = {
    "chat": _DEFAULT_MAX_TOOL_CALLS,
    "job": _DEFAULT_JOB_MAX_TOOL_CALLS,
    "room": 80,
    "flow": _DEFAULT_MAX_TOOL_CALLS,
}
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


def _job_calls() -> int:
    """A background job's per-window tool-call budget — never below a turn's (Vexa-ai/vexa#1613).

    The floor matters operationally: raising ``VEXA_AGENT_MAX_TOOL_CALLS`` on the containers is the
    stopgap this issue's orchestrator applies at the next swap, and a job that then got LESS than a
    turn would be this bug with the numbers swapped."""
    return max(_int_env("VEXA_AGENT_JOB_MAX_TOOL_CALLS", _DEFAULT_JOB_MAX_TOOL_CALLS),
               _int_env("VEXA_AGENT_MAX_TOOL_CALLS", _DEFAULT_MAX_TOOL_CALLS))


def _job_seconds() -> float:
    """A background job's WHOLE-JOB wall clock — the outer bound windows do not reset."""
    return max(_float_env("VEXA_AGENT_JOB_MAX_TURN_SEC", _DEFAULT_JOB_MAX_TURN_SEC),
               _float_env("VEXA_AGENT_MAX_TURN_SEC", _DEFAULT_MAX_TURN_SEC))


def _calls_budget(kind: str) -> int:
    """This KIND of turn's tool-call budget (Vexa-ai/vexa#1622) — three reads, in this order.

      1. ``VEXA_AGENT_MAX_TOOL_CALLS_<KIND>`` — the dial for this row of the table.
      2. ``VEXA_AGENT_MAX_TOOL_CALLS`` — the single dial, the fallback for ALL four. This is the
         compatibility promise and it is not decorative: the dogfood stack has carried
         ``VEXA_AGENT_MAX_TOOL_CALLS=160`` since 14:41Z on 2026-09-06 as the stopgap for the very
         incident this table answers, and that stopgap must keep meaning what the operator meant.
      3. the row's own default (``_KIND_MAX_TOOL_CALLS``).

    A job keeps ``_job_calls()`` at step 2 rather than reading the single dial directly, so #1613's
    own name and its floor survive untouched. Nothing is clamped: a deployment that sets 0 wants 0,
    and the job runner already depends on that (a window that makes no call ends the job)."""
    k = (kind or "").strip().lower()
    if k not in _KIND_MAX_TOOL_CALLS:
        k = "chat"
    named = f"VEXA_AGENT_MAX_TOOL_CALLS_{k.upper()}"
    if (os.environ.get(named) or "").strip():
        return _int_env(named, _KIND_MAX_TOOL_CALLS[k])
    if k == "job":
        return _job_calls()
    return _int_env("VEXA_AGENT_MAX_TOOL_CALLS", _KIND_MAX_TOOL_CALLS[k])


def _job_progress_line(calls: int, window: int) -> str:
    """WHAT THE JOB ROW SAYS when a window ends and the next one opens. The person is watching one
    line at the foot of the chat; it has to say that work continues, not that something reset."""
    return f"{calls} steps so far — continuing (window {window})"


#: What a fresh window is told. Not a summary the model wrote (that is another round trip, and a
#: paraphrase of work is not the work): the brief, and the one fact the new window cannot see for
#: itself — that its own earlier output is already on disk. Re-reading it is cheap and true.
_JOB_CONTINUE = (
    "You have already made {calls} tool calls on this job and the window ended. Everything you "
    "wrote is saved on disk. Do not start over and do not repeat work: read what is there now, "
    "carry on from it, and finish. If it is already finished, say so in one line and stop."
)


#: WHAT THE PERSON PRESSES (Vexa-ai/vexa#1622), and the words that go back with it. A turn does NOT
#: continue itself the way a job opens a fresh window: a job was dispatched to finish something and
#: its pages are already committed, while a turn is somebody waiting on a reply who may well want a
#: different next move. So the loop offers, and the press queues a same-target act through #1610's
#: inbox — one click where the founder re-typed his instruction three times.
_CONTINUE_LABEL = "Continue"
_CONTINUE_INSTRUCTION = "continue where you stopped"


def _stopped_line(reason: str, calls: int, budget: int, seconds: float) -> str:
    """THE LINE IN THE BUBBLE for a turn that ended on a budget (Vexa-ai/vexa#1622).

    It rides on `done.reason`, which the terminal already renders under the partial reply (F89) —
    the defect was never that the field had no consumer, it was that the sentence in it said
    *"the turn stopped early: tool-call budget"*, which names a mechanism and not a state. This
    says how far it got and what the ceiling was, because those are the two facts that decide
    whether the answer is Continue or a bigger budget.

    The words *tool-call budget* / *time budget* are kept inside the sentence: they are what every
    existing reader — the log line in `worker.engine`, the friction scan, the terminal test —
    already keys on, and a rename would be a second change wearing this one's clothes."""
    if reason == "tool-call budget":
        return f"stopped at the tool-call budget after {calls} of {budget} steps"
    return f"stopped at the time budget after {calls} steps ({seconds:.0f}s)"


def _continue_window(messages: list[dict], prompt: str, calls: int, said: str) -> list[dict]:
    """The message list a continuation window starts from: the original brief, then the checkpoint.

    Everything between them is dropped deliberately — it is the transcript of work whose RESULT is
    on disk, and carrying it would spend the new window's context re-reading what the new window is
    about to re-read properly."""
    first = next((m for m in messages if m.get("role") == "user"), None)
    seed: list[dict] = [dict(first)] if first else [{"role": "user", "content": prompt}]
    tail = _JOB_CONTINUE.format(calls=calls)
    if (said or "").strip():
        tail += "\n\nThe last thing you said was:\n" + said.strip()[:1000]
    seed.append({"role": "user", "content": tail})
    return seed


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
    # The two WEB tools. Same discipline as the file tools — spec here, execution in `run_builtin`,
    # result trimmed by the loop, counted against the turn's tool-call budget. WebSearch is attached
    # only when a backend is configured (`_attached`).
    "WebSearch": {"description": "Search the open web. Returns JSON: "
                                 "{query, results:[{title,url,snippet}]}. Use it before asking the "
                                 "person a question you could answer yourself, then WebFetch the "
                                 "URLs worth reading in full.",
                  "parameters": {"type": "object", "properties": {
                      "query": {"type": "string"},
                      "max_results": {"type": "integer", "description": "default 8, max 25"}},
                      "required": ["query"]}},
    "WebFetch": {"description": "Fetch one http(s) page and return its readable text. Returns JSON: "
                                "{url, final_url, status, title, text}. Refuses any address on this "
                                "deployment's own network.",
                 "parameters": {"type": "object", "properties": {
                     "url": {"type": "string"},
                     "max_chars": {"type": "integer", "description": "default 12000"}},
                     "required": ["url"]}},
    # THE ONE TOOL THAT ENDS THE TURN INSTEAD OF EXTENDING IT (Vexa-ai/vexa#1584). Everything else
    # here does work; this hands work to a background job and comes straight back, so the person
    # gets an answer now. Attached only where a spawner exists (`_CONDITIONAL`) — see `llm/jobs.py`.
    "spawn_job": {"description": "Run a LONG act as a background job instead of inside this turn. "
                                 "Returns immediately; the job posts its own line into this chat "
                                 "when it lands, and the page it wrote refreshes itself. Use it for "
                                 "anything that will take more than a few tool calls — writing or "
                                 "extending a page, a research sweep — then answer the person now. "
                                 "The job does NOT see this conversation, so `brief` must carry the "
                                 "whole instruction. One job per `target`; a second is refused.",
                  "parameters": {"type": "object", "properties": {
                      "kind": {"type": "string", "description": "what kind of act: create, extend, "
                                                                "research, …"},
                      "target": {"type": "string", "description": "the one thing it acts on — a "
                                                                  "workspace path, or a name"},
                      "brief": {"type": "string", "description": "the whole instruction the job "
                                                                 "runs on, standalone"}},
                      "required": ["kind", "target", "brief"]}},
}

#: Built-ins whose attachment is CONDITIONAL. The harness's rule is that a tool it cannot serve is
#: simply not attached and the turn's tool list says so — advertising a `WebSearch` with no backend
#: behind it teaches the model that searching does not work, and that lesson outlives the turn.
_CONDITIONAL: dict[str, Callable[[], bool]] = {"WebSearch": web_tools.search_configured,
                                               "spawn_job": jobs.configured}


def _attached(name: str) -> bool:
    gate = _CONDITIONAL.get(name)
    return True if gate is None else bool(gate())

_GREP_MAX_HITS = 200
_READ_MAX_CHARS = 100_000
_GLOB_MAX = 400


def mount_roots(work: Path, *, writable_only: bool = False) -> list[Path]:
    """Every directory this harness's file tools may touch: the turn's cwd plus each declared mount.

    Read from ``VEXA_MOUNTS`` the same way ``worker.engine.active_mounts`` does — by ENV, not by
    import, because this module owns no product imports. A malformed value costs the extra mounts,
    never the turn.

    ``writable_only`` HONOURS THE MOUNT'S ``write`` FLAG (F87). The set carries read-only mounts —
    ``_global`` (the org tier, platform-write-only) and every desk in a post-meeting room — and this
    function used to drop the flag, so ``Write``/``Edit`` were rooted in workspaces the dispatch had
    declared read-only. Only the docker ``:ro`` bind stood between the model and somebody else's
    desk, and the process backend has no such bind at all. The turn's cwd stays in BOTH sets: it is
    the primary mount, which ``dispatch._worker_cwd`` picks precisely because it is writable."""
    roots = [work.resolve()]
    raw = os.environ.get("VEXA_MOUNTS") or ""
    if raw:
        try:
            for m in json.loads(raw):
                if not (isinstance(m, dict) and m.get("path")):
                    continue
                if writable_only and not m.get("write"):
                    continue
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

    def __init__(self, roots: list[Path], write_roots: Optional[list[Path]] = None) -> None:
        self._roots = roots
        # F87: the WRITABLE subset. Defaults to the read set so a caller that knows of only one
        # scope (a test, the eval stub) behaves exactly as before.
        self._write_roots = roots if write_roots is None else write_roots

    def resolve(self, raw: str) -> Path:
        """A path a READ tool may touch — anywhere in the mount set."""
        return self._within(raw, self._roots, "mounted workspaces")

    def resolve_write(self, raw: str) -> Path:
        """A path a WRITE tool may touch — the WRITABLE mounts only (F87). A read-only mount is a
        governance decision the dispatch already made; asking the model nicely is not enforcement."""
        return self._within(raw, self._write_roots, "WRITABLE mounted workspaces")

    def contains(self, raw: str) -> bool:
        """True when ``raw`` is inside the read set — for filtering hits rather than refusing a call."""
        try:
            self.resolve(raw)
        except (ValueError, OSError):
            return False
        return True

    def _within(self, raw: str, roots: list[Path], what: str) -> Path:
        if not raw:
            raise ValueError("no path given")
        if not roots:
            raise ValueError(f"this turn has no {what}")
        p = Path(raw)
        if not p.is_absolute():
            p = roots[0] / p
        # resolve() without strict so a not-yet-existing file still normalises (Write creates it)
        p = Path(os.path.normpath(str(p)))
        try:
            real = p.resolve()
        except OSError:
            real = p
        for root in roots:
            if real == root or root in real.parents:
                return real
        raise ValueError(f"path {raw} is outside the {what} "
                         f"({', '.join(str(r) for r in roots)})")


def run_builtin(tool: str, args: dict, sandbox: _Sandbox,
                web: Optional[httpx.Client] = None) -> tuple[bool, str]:
    """One built-in tool → ``(ok, text)``. Never raises: a bad call is a failed tool result.

    ``web`` is the harness's own http client for the two web tools (so a test can hand it a
    transport); omitted, each call opens and closes its own."""
    # The web tools take no path and touch no mount, so they are answered before the sandbox is
    # consulted at all. Their own refusals (no backend, a private address) are ordinary failed
    # results — the model reads them and picks another move.
    if tool == "WebSearch":
        return web_tools.web_search(str(args.get("query") or ""),
                                    args.get("max_results") or web_tools.DEFAULT_MAX_RESULTS,
                                    client=web)
    if tool == "WebFetch":
        return web_tools.web_fetch(str(args.get("url") or ""),
                                   args.get("max_chars") or web_tools.DEFAULT_FETCH_CHARS,
                                   client=web)
    # Answered before the sandbox too, and for a stronger reason than the web tools': this call
    # touches no path and does no work at all — it hands an instruction to the worker's job runner
    # and returns. A refusal (a job already running on that target) is an ordinary failed result.
    if tool == "spawn_job":
        return jobs.spawn(str(args.get("kind") or ""), str(args.get("target") or ""),
                          str(args.get("brief") or ""))
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
            path = sandbox.resolve_write(str(args.get("file_path") or ""))
            path.parent.mkdir(parents=True, exist_ok=True)
            content = args.get("content")
            path.write_text("" if content is None else str(content), encoding="utf-8")
            return True, f"wrote {path}"
        if tool == "Edit":
            path = sandbox.resolve_write(str(args.get("file_path") or ""))
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
            # F86: THE PATTERN IS A PATH TOO. `Read` resolved its argument through the sandbox and
            # `Glob` did not, so `{"pattern": "../../../etc/*"}` enumerated the container's whole
            # filesystem from inside a workspace turn — the sandbox refused the read that followed,
            # but the listing itself is the disclosure. Refuse the two escapes a pattern can spell,
            # then resolve every HIT as well: a symlink inside the mount is the same escape wearing
            # a legal-looking pattern.
            if pattern.startswith("/") or ".." in PurePosixPath(pattern).parts:
                return False, ("pattern must be relative to the search path and may not contain "
                               "'..' — name a `path` under a mounted workspace instead")
            hits: list[str] = []
            for p in base.glob(pattern):
                try:
                    real = sandbox.resolve(str(p))
                except (ValueError, OSError):
                    continue                      # a symlink out of the mounts is not a hit
                if real.is_file():
                    hits.append(str(real))
            hits = sorted(set(hits))[:_GLOB_MAX]
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
                if not sandbox.contains(str(f)):
                    continue                      # same escape as F86, reached through a symlink
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

def _call_ids(msg: dict) -> set[str]:
    """The ids of the tool calls THIS assistant message made."""
    return {str(tc.get("id")) for tc in (msg.get("tool_calls") or [])
            if isinstance(tc, dict) and tc.get("id")}


def _exchange(msgs: list[dict], i: int) -> set[int]:
    """The indices that must leave TOGETHER if index ``i`` leaves — an assistant's tool_calls and
    every ``tool`` message answering them (F88).

    The OpenAI dialect is strict in both directions: an assistant message whose ``tool_calls`` have
    no answering ``tool`` message is a 400, and so is a ``tool`` message whose caller is gone. The
    old trimmer dropped one message at a time and hit both."""
    msg = msgs[i]
    ids = _call_ids(msg)
    if ids:
        return {i} | {j for j, m in enumerate(msgs)
                      if m.get("role") == "tool" and str(m.get("tool_call_id")) in ids}
    if msg.get("role") == "tool":
        cid = str(msg.get("tool_call_id") or "")
        caller = next((j for j, m in enumerate(msgs) if cid and cid in _call_ids(m)), None)
        return {i} if caller is None else _exchange(msgs, caller)
    return {i}


def prune_orphans(messages: list[dict]) -> tuple[list[dict], int]:
    """Remove tool/assistant messages that cannot be sent because their counterpart is missing.

    This is a REPAIR, not a sacrifice: such a message is unsendable, so keeping it costs the whole
    turn. It matters most on a RESUMED session — a transcript written by the old trimmer carries the
    orphan for good, so every subsequent turn of that conversation 400s until somebody starts a new
    one, which is exactly how the defect reproduced."""
    msgs = list(messages)
    removed = 0
    while True:
        answered = {str(m.get("tool_call_id")) for m in msgs if m.get("role") == "tool"}
        called: set[str] = set()
        for m in msgs:
            called |= _call_ids(m)
        drop = {i for i, m in enumerate(msgs)
                if (m.get("role") == "tool" and str(m.get("tool_call_id")) not in called)
                or (_call_ids(m) and not (_call_ids(m) & answered))}
        if not drop:
            return msgs, removed
        msgs = [m for i, m in enumerate(msgs) if i not in drop]
        removed += len(drop)


def trim_messages(messages: list[dict], budget: int) -> tuple[list[dict], int]:
    """Fit ``messages`` under ``budget`` estimated tokens. Returns (messages, trimmed_count).

    Order of sacrifice, oldest first: tool RESULTS (replaced by a stub, so the model still sees that
    the call happened), then whole oldest EXCHANGES, and only then the head of the first user
    message. The LAST user message is never touched — it is the ask, and a turn that trims the ask
    answers a question nobody put.

    "Exchange", not "message" (F88): dropping an assistant turn without the ``tool`` messages that
    answered it — or a ``tool`` message without its caller — produces a request every
    OpenAI-compatible server rejects with a 400, and the malformation is WRITTEN TO THE TRANSCRIPT,
    so a resumed session reproduced it on every later turn. `prune_orphans` runs first and
    unconditionally, healing transcripts the old trimmer already broke; its removals are not counted
    as trimming because they buy no context, they only make the request sendable."""
    msgs, healed = prune_orphans([dict(m) for m in messages])
    if healed:
        log.warning("dropped %d orphaned tool message(s) from the session transcript — an "
                    "assistant turn and its tool results must leave together", healed)
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
    while _est_tokens(msgs) > budget:                  # 2) drop oldest non-final EXCHANGES
        drop: Optional[set[int]] = None
        for i, m in enumerate(msgs):
            if i == 0 or i == last_user or m.get("role") == "system":
                continue
            group = _exchange(msgs, i)
            if 0 in group or last_user in group or any(msgs[j].get("role") == "system"
                                                       for j in group):
                continue                               # the group is anchored — try the next one
            drop = group
            break
        if not drop:
            break
        msgs = [m for i, m in enumerate(msgs) if i not in drop]
        trimmed += len(drop)
        last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=0)
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
                 mcp_http_client: Optional[httpx.Client] = None,
                 web_transport: Optional[httpx.BaseTransport] = None) -> None:
        self._base = (base_url or os.environ.get("VEXA_LLM_BASE_URL")
                      or os.environ.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
        self._key = (api_key or os.environ.get("VEXA_LLM_API_KEY")
                     or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                     or os.environ.get("ANTHROPIC_API_KEY") or "")
        self._model = model or os.environ.get("VEXA_LLM_MODEL") or ""
        self._extra = _parse_extra_body(extra_body if extra_body is not None
                                        else os.environ.get("VEXA_LLM_EXTRA_BODY"))
        self._client = httpx.Client(timeout=timeout, transport=transport)
        # A SEPARATE client for the web tools, and deliberately so: the model endpoint's timeout is
        # a 300s inference wait, redirects there are meaningless, and a page the MODEL chose must
        # never ride the connection pool carrying the deployment's model credential.
        # `follow_redirects=False` because `web_fetch` walks the hops itself — every one of them is
        # re-checked against the SSRF guard, which is the whole point.
        self._web = httpx.Client(timeout=web_tools.FETCH_TIMEOUT, transport=web_transport,
                                 follow_redirects=False)
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
            # NAME THE KEY THE OPERATOR ACTUALLY SETS (F91). `VEXA_AGENT_MODEL` is what Settings →
            # Models writes and what the dispatch stamps into every worker; `VEXA_LLM_MODEL` is only
            # this harness's override, so sending the operator to it sent them to the dial that
            # would not be read.
            return ("openai-agent: no VEXA_AGENT_MODEL (nor the VEXA_LLM_MODEL override) — the "
                    "endpoint will be asked for an empty model")
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
            raise LLMConfigError("no model: set VEXA_AGENT_MODEL (Settings → Models writes this "
                                 "one), or the VEXA_LLM_MODEL override for this harness")

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
                     for n in BUILTIN_SPECS if _allowed(n, allow) and _attached(n)]
            specs += [s for s in _mcp_specs(mcp_index) if _allowed(s["function"]["name"], allow)]

            # A JOB IS NOT A TURN (Vexa-ai/vexa#1613) — see the module docstring. `in_job()` is a
            # thread-local set by the worker on the job's own thread, so the chat turn running
            # beside this one in the same process keeps the per-turn budget it always had.
            is_job = llm_jobs.in_job()
            # …AND WHICH KIND OF TURN IT IS (Vexa-ai/vexa#1622), read off the same thread-local for
            # the same reason: a chat turn, a room run and a flow step can all be in this process at
            # once, so the budget cannot come from the environment alone.
            kind = llm_jobs.turn_kind()
            budget_calls = _calls_budget(kind)
            budget_secs = _job_seconds() if is_job else _float_env("VEXA_AGENT_MAX_TURN_SEC",
                                                                   _DEFAULT_MAX_TURN_SEC)
            ctx_budget = _int_env("VEXA_AGENT_CONTEXT_TOKENS", _DEFAULT_CONTEXT_TOKENS)
            started, calls_made, reply = time.monotonic(), 0, ""
            # The CALL budget is per window; the CLOCK is not. `window_calls` is what decides
            # whether a window made progress, and `total_calls` is what the job row is told.
            window, window_calls, total_calls = 1, 0, 0
            sandbox = _Sandbox(mount_roots(work), mount_roots(work, writable_only=True))
            # F89: WHAT THE TURN GAVE UP, carried to the `done` event. `turn-truncated` and
            # `context-trimmed` are emitted for the log and the panel, but neither had a consumer —
            # the terminal reducer's switch has no case for them and the engine drops them — so a
            # turn that ran out of tool calls, ran out of wall clock, or answered from a context it
            # had sacrificed reported `done.ok=True` with a partial reply and looked complete.
            # `done` is one of the five FROZEN types, so this rides as an optional field on it
            # rather than as a sixth type.
            truncation = ""
            trimmed_total = 0
            # THE TOOL THE TURN WAS ON when the budget ran out (Vexa-ai/vexa#1622). The auto-filed
            # friction reported an EMPTY tool name on all four of the founder's reports, because the
            # only result left at the end of such a turn is a refusal for a call that never ran and
            # therefore never emitted a `tool-call` event to carry a name. Two fixes, and this is
            # the first: the loop remembers what it last actually ran.
            last_tool = ""

            while True:
                sent, trimmed = trim_messages(messages, ctx_budget)
                if trimmed:
                    trimmed_total += trimmed
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
                        truncation = reason
                        # A JOB ABOUT TO OPEN A FRESH WINDOW HAS GIVEN NOTHING UP (Vexa-ai/vexa#1613),
                        # so it does not say it has — `job-progress` below is what it says instead.
                        if not (is_job and reason == "tool-call budget" and window_calls):
                            # `budget` + `tool` ride here (Vexa-ai/vexa#1622) so that the friction
                            # scan can name the ceiling and the last tool without re-deriving
                            # either from a stream it only keeps four event types of.
                            yield {"type": "turn-truncated", "reason": reason,
                                   "calls": calls_made, "budget": budget_calls,
                                   "kind": kind, "tool": last_tool,
                                   "seconds": round(time.monotonic() - started, 1)}
                        # EVERY call the model made is answered, refusals included. An assistant
                        # message whose tool_calls have no matching tool message is a MALFORMED
                        # request to the next round trip, and on a resumed session that malformation
                        # outlives the turn that made it.
                        for skipped in calls[i:]:
                            refusal = f"not run: the turn hit its {reason}"
                            # THE NAME TRAVELS WITH THE REFUSAL (Vexa-ai/vexa#1622) — the second
                            # half of the empty-tool-name fix. A refused call emits no `tool-call`
                            # event (it never ran), so anything downstream that joins results to
                            # calls by id finds nothing and renders ` `` `. It is on the result
                            # itself now, for this case and for any future one.
                            yield {"type": "tool-result", "callId": skipped["id"], "ok": False,
                                   "tool": skipped["name"], "summary": refusal}
                            store.record_tool_result(skipped["id"], False, refusal)
                            messages.append({"role": "tool", "tool_call_id": skipped["id"],
                                             "content": refusal})
                        break
                    calls_made += 1
                    window_calls += 1
                    total_calls += 1
                    last_tool = call["name"]
                    yield {"type": "tool-call", "tool": call["name"], "args": call["args"],
                           "callId": call["id"]}
                    ok, out = self._exec_tool(call, mcp_index, sandbox, allow)
                    out = out[:_TOOL_RESULT_MAX_CHARS]
                    yield {"type": "tool-result", "callId": call["id"], "ok": ok,
                           "summary": _short(out)}
                    for extra in _panel_events(call, ok, out):
                        yield extra
                    store.record_tool_result(call["id"], ok, out)
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": out})
                if over_budget:
                    # A JOB CHECKPOINTS AND CARRIES ON (Vexa-ai/vexa#1613). Its pages are already
                    # committed as they land, so there is nothing to save here — what a fresh
                    # window needs is the brief and the fact that work has already happened. It
                    # only ever applies to the CALL budget: the clock is the job's outer bound and
                    # running past it is what "a job may take minutes, not hours" forbids.
                    if is_job and truncation == "tool-call budget" and window_calls:
                        window += 1
                        yield {"type": "job-progress", "window": window, "calls": total_calls,
                               "line": _job_progress_line(total_calls, window)}
                        messages = _continue_window(messages, prompt, total_calls, text)
                        store.record_user(messages[-1]["content"])
                        calls_made, window_calls, truncation, over_budget = 0, 0, "", False
                        continue
                    reply = text
                    break
            # THE TURN'S OWN VERDICT. `ok` is False only when the turn did not finish its own
            # reasoning — it hit a budget — because that reply is partial and acting on it as if it
            # were an answer is the failure. A context trim leaves a COMPLETE answer built on less,
            # so it stays ok=True and says so in `reason`; the consumer decides how loud that is.
            #
            # `steps` RIDES ON EVERY `done`, not only a truncated one (Vexa-ai/vexa#1622). The turn
            # status had no server-side step count at all — the terminal counted `tool-call` events
            # itself — so nothing outside one browser could say how much work a turn did, and the
            # one moment that number matters most is the moment the turn stops for having done too
            # much of it. It is the WHOLE turn's count: a job's windows are one piece of work.
            done: dict = {"type": "done", "reply": reply, "sessionId": sid,
                          "ok": not truncation, "steps": total_calls, "budget": budget_calls}
            if truncation:
                done["reason"] = _stopped_line(truncation, calls_made, budget_calls,
                                               time.monotonic() - started)
                # THE ACT THE BUBBLE OFFERS. Named here rather than in the client because the
                # harness is the only thing that knows the turn did not finish its own reasoning;
                # the client's job is to render a control and post the instruction back.
                done["act"] = {"label": _CONTINUE_LABEL, "instruction": _CONTINUE_INSTRUCTION}
            elif trimmed_total:
                done["reason"] = (f"context-trimmed: {trimmed_total} message(s) dropped to stay "
                                  f"inside the turn's {ctx_budget}-token budget")
            yield done
        finally:
            for srv in servers:
                srv.close()

    def _exec_tool(self, call: dict, mcp_index: dict, sandbox: _Sandbox,
                   allow: set[str]) -> tuple[bool, str]:
        name, args = call["name"], call["args"]
        # F85 (SECURITY): THE ALLOW-SET IS ENFORCED HERE, not only where tools are advertised.
        # `specs` filters what the model is TOLD about, and a model that names a tool it was never
        # offered — smaller models do this constantly, and a resumed transcript carries the names of
        # tools an earlier turn had — was executed anyway. `allowed_tools=["Read"]` ran `Write`.
        # A refusal is a normal tool result: the model sees it and corrects itself.
        if not _allowed(name, allow):
            return False, (f"{name} is not allowed on this turn — the allowed tools are "
                           f"{sorted(allow)}")
        if name in mcp_index:
            srv, tool = mcp_index[name]
            return srv.call(tool, args if isinstance(args, dict) else {})
        if name in BUILTIN_SPECS:
            return run_builtin(name, args if isinstance(args, dict) else {}, sandbox, self._web)
        attached = {n for n in BUILTIN_SPECS if _attached(n)} | set(mcp_index)
        return False, (f"no tool named {name} is attached to this turn — the attached tools are "
                       f"{sorted(attached)}")

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
                    # F90: AN ERROR FRAME ON A 200 RESPONSE. vLLM, LiteLLM and OpenRouter all
                    # answer 200 and then put the failure INSIDE the stream (a rate limit, a
                    # context overflow, an upstream 5xx). The old loop looked only for `delta`, so
                    # such a frame was skipped and the turn ended with an empty successful reply —
                    # the person saw the agent say nothing and nothing anywhere said why.
                    err = chunk.get("error")
                    if err:
                        detail = err.get("message") if isinstance(err, dict) else str(err)
                        raise LLMError(f"{self._base} streamed an error frame: "
                                       f"{_short(detail or err, 300)}")
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
        # F90: A STREAM THAT SAID NOTHING is a failure, not an empty answer. A truncated connection,
        # a model that emitted only reasoning tokens (the Qwen thinking case VEXA_LLM_EXTRA_BODY
        # exists to switch off), a `[DONE]` with no content — all reached `done.ok=True` with an
        # empty reply, which the chat renders as the agent having nothing to say.
        if not acc_text and not acc_calls:
            raise LLMError(f"{self._base} streamed no content and no tool calls — the endpoint "
                           "answered 200 with an empty completion (a truncated stream, or a model "
                           "spending its whole budget on reasoning tokens)")
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
    """The panel moves a successful call earns — the SAME four conventions ``claude_code`` applies,
    through its own helpers: the writer's tab, decision 35's chips, decision 30.4's transcript, and
    the one a person actually ASKED for (`open_page`, Vexa-ai/vexa#1586).

    Both runners read the same result through the same function on purpose. A panel convention
    written twice is a panel convention that is right in one runner — which is the reason these
    helpers live in `claude_code` and are imported here rather than re-derived."""
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
    elif name in _OPEN_TOOLS:
        ev = _open_event(out)
        if ev:
            events.append(ev)
    elif name in _FOCUS_TOOLS:
        ev = _workspace_focus(out)
        if ev:
            events.append(ev)
    return events
