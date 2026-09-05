"""L2: the openai-agent harness — the loop, offline. A scripted stub server over
``httpx.MockTransport`` plays the model; a stdlib stdio MCP server (written to a temp file) plays
the toolbelt. No network, no CLI, no rig.

What each test is defending, because a harness's failures are all silent ones:

* the tool loop actually loops — call, execute, feed back, answer — and emits the FROZEN UnitEvents
  the terminal reducer consumes;
* the transcript is written in Claude Code's on-disk shape with the prompt VERBATIM, because
  ``control_plane.workspace_reader.history`` parses that file and ``engine._prompt_key`` hashes
  those exact bytes (the phase mark and the machinery mark ride on it);
* a resumed session carries the conversation, and an ALIEN session id yields ``done.ok=False`` so
  the engine's stale-resume retry can heal it;
* ``VEXA_LLM_EXTRA_BODY`` reaches EVERY request — the Qwen thinking switch is the worked case, and
  a deployment that believes it disabled thinking and did not fails far from the cause;
* the file tools refuse a path outside the mounts rather than clamping it;
* the per-turn budget stops the loop AND answers every outstanding tool call;
* MCP tools are attached from the same `mcp.json` the worker writes, over both transports.
"""
from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path

import httpx
import pytest

from llm.openai_agent import (OpenAIAgentHarness, _Sandbox, mount_roots, run_builtin,
                              trim_messages)


# ── the scripted model ──────────────────────────────────────────────────────────────────────────

def _msg(content="", calls=None):
    m = {"role": "assistant", "content": content}
    if calls:
        m["tool_calls"] = [{"id": c[0], "type": "function",
                            "function": {"name": c[1], "arguments": json.dumps(c[2])}}
                           for c in calls]
    return m


def _server(script, seen=None):
    """A handler that plays ``script`` (one assistant message per request), recording bodies."""
    state = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if seen is not None:
            seen.append(body)
        msg = script[min(state["i"], len(script) - 1)]
        state["i"] += 1
        return httpx.Response(200, json={"choices": [{"message": msg, "finish_reason":
                                                      "tool_calls" if msg.get("tool_calls") else "stop"}]})
    return handler


@pytest.fixture(autouse=True)
def _blocking(monkeypatch):
    # The loop streams by default; these tests script whole messages, so they run the blocking path.
    # Streaming has its own test below.
    monkeypatch.setenv("VEXA_AGENT_STREAM", "0")
    monkeypatch.delenv("VEXA_MOUNTS", raising=False)


def _harness(handler, **kw):
    kw.setdefault("base_url", "http://ccc.local/v1")
    kw.setdefault("model", "qwen3.8-27b")
    return OpenAIAgentHarness(transport=httpx.MockTransport(handler), **kw)


def _events(harness, work, prompt, **kw):
    return list(harness.run_turn(Path(work), prompt, **kw))


# ── the loop ────────────────────────────────────────────────────────────────────────────────────

def test_tool_loop_reads_a_file_then_answers(tmp_path):
    (tmp_path / "note.md").write_text("the DNA TSC met on Monday")
    seen = []
    h = _harness(_server([
        _msg("", [("call_1", "Read", {"file_path": str(tmp_path / "note.md")})]),
        _msg("They met on Monday."),
    ], seen))
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "what happened?", allowed_tools=["Read", "Write"])

    kinds = [e["type"] for e in evs]
    assert kinds == ["tool-call", "tool-result", "message-delta", "done"]
    assert evs[0]["tool"] == "Read" and evs[0]["callId"] == "call_1"
    assert evs[1]["ok"] is True and "Monday" in evs[1]["summary"]
    assert evs[-1]["reply"] == "They met on Monday." and evs[-1]["ok"] is True
    # the second request carries the assistant's tool_calls AND the tool result, in order
    roles = [m["role"] for m in seen[1]["messages"]]
    assert roles == ["user", "assistant", "tool"]
    assert seen[1]["messages"][2]["tool_call_id"] == "call_1"
    # only the allowed built-ins were offered
    offered = {t["function"]["name"] for t in seen[0]["tools"]}
    assert offered == {"Read", "Write"}
    assert seen[0]["tool_choice"] == "auto"


def test_unknown_tool_is_a_failed_result_not_a_crash(tmp_path):
    # No allow-set (unrestricted, the claude-code `--allowedTools` semantics) so the call reaches
    # the "this harness does not implement it" branch rather than the F85 allow-set refusal — the
    # two are different answers to different questions and both must stay non-fatal.
    h = _harness(_server([_msg("", [("c1", "Bash", {"command": "rm -rf /"})]), _msg("ok")]))
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "go")
    result = next(e for e in evs if e["type"] == "tool-result")
    assert result["ok"] is False and "no tool named Bash" in result["summary"]
    assert evs[-1]["ok"] is True          # the loop recovers; the model gets to choose again


def test_extra_body_is_merged_into_every_request(tmp_path):
    seen = []
    h = _harness(_server([_msg("", [("c1", "Glob", {"pattern": "*.md"})]), _msg("done")], seen),
                 extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    h.prepare(tmp_path)
    _events(h, tmp_path, "list", allowed_tools=["Glob"])
    assert len(seen) == 2
    for body in seen:
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert body["model"] == "qwen3.8-27b"     # reserved keys always win


def test_extra_body_cannot_override_reserved_keys(tmp_path):
    seen = []
    h = _harness(_server([_msg("hi")], seen), extra_body={"model": "smuggled", "messages": []})
    h.prepare(tmp_path)
    _events(h, tmp_path, "hi")
    assert seen[0]["model"] == "qwen3.8-27b" and seen[0]["messages"][0]["content"] == "hi"


# ── sessions + the transcript contract ──────────────────────────────────────────────────────────

def _transcript(root: Path) -> list[dict]:
    files = list((root / ".claude" / "projects").glob("*/*.jsonl"))
    assert len(files) == 1, files
    return [json.loads(l) for l in files[0].read_text().splitlines() if l.strip()]


def test_transcript_is_claude_shaped_and_stores_the_prompt_verbatim(tmp_path):
    work, chat = tmp_path / "ws", tmp_path / "sys"
    work.mkdir(); chat.mkdir()
    h = _harness(_server([_msg("", [("c1", "Glob", {"pattern": "*"})]), _msg("nothing here")]))
    h.prepare(work, chat_root=chat)
    prompt = "[vexa-machinery] [vexa-phase:writeback] Write-back phase — give each name a page."
    evs = _events(h, work, prompt, allowed_tools=["Glob"])
    sid = evs[-1]["sessionId"]

    recs = _transcript(chat)
    assert not (work / ".claude" / "projects").exists()      # chats stay off a shared mount
    assert recs[0]["type"] == "user"
    # VERBATIM: the phase mark and engine._prompt_key both read this string
    assert recs[0]["message"]["content"][0]["text"] == prompt
    assert recs[1]["type"] == "assistant"
    assert [b["type"] for b in recs[1]["message"]["content"]] == ["tool_use"]
    assert recs[1]["message"]["content"][0]["name"] == "Glob"
    assert recs[2]["type"] == "user"
    assert recs[2]["message"]["content"][0]["type"] == "tool_result"      # history skips these
    assert recs[3]["message"]["content"][0]["text"] == "nothing here"
    assert h.transcript_bytes(chat, sid) > 0


def test_history_reader_parses_our_transcript(tmp_path):
    """The seam that matters most: the REAL reader, on a file this harness wrote."""
    from control_plane.workspace_reader import WorkspaceReader

    root = tmp_path / "store"
    ws = root / "126"
    ws.mkdir(parents=True)
    h = _harness(_server([_msg("", [("c1", "Glob", {"pattern": "*"})]), _msg("I found nothing.")]))
    h.prepare(ws)
    evs = _events(h, ws, "what do we have?", allowed_tools=["Glob"])
    sid = evs[-1]["sessionId"]
    sess = ws / ".claude" / "sessions"
    sess.mkdir(parents=True, exist_ok=True)
    (sess / "main.session").write_text(sid)

    turns = WorkspaceReader(root).history("126", "main")
    assert [t["role"] for t in turns] == ["user", "agent"]
    assert turns[0]["text"] == "what do we have?"
    assert turns[1]["text"] == "I found nothing."
    assert turns[1]["ops"], "the Glob call should render as an op"


def test_resume_carries_the_conversation(tmp_path):
    h = _harness(_server([_msg("first")]))
    h.prepare(tmp_path)
    sid = _events(h, tmp_path, "one")[-1]["sessionId"]

    seen = []
    h2 = _harness(_server([_msg("second")], seen))
    h2.prepare(tmp_path)
    evs = _events(h2, tmp_path, "two", session=sid)
    assert evs[-1]["sessionId"] == sid and evs[-1]["ok"] is True
    assert [m["content"] for m in seen[0]["messages"]] == ["one", "first", "two"]


def test_alien_session_yields_not_ok_so_the_engine_can_retry(tmp_path):
    h = _harness(_server([_msg("never reached")]))
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "hello", session="deadbeef")
    assert len(evs) == 1 and evs[0]["type"] == "done" and evs[0]["ok"] is False


# ── budgets, trimming, sandbox ──────────────────────────────────────────────────────────────────

def test_tool_call_budget_stops_the_turn_and_answers_every_call(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_AGENT_MAX_TOOL_CALLS", "1")
    h = _harness(_server([_msg("", [("a", "Glob", {"pattern": "*"}),
                                    ("b", "Glob", {"pattern": "*"})]), _msg("late")]))
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "go", allowed_tools=["Glob"])
    trunc = [e for e in evs if e["type"] == "turn-truncated"]
    assert trunc and trunc[0]["reason"] == "tool-call budget"
    # both calls answered — an unanswered tool_call is a malformed next request
    answered = {e["callId"] for e in evs if e["type"] == "tool-result"}
    assert answered == {"a", "b"}
    # F89: the truncation reaches the `done` event, which is the only one anything downstream reads
    assert evs[-1]["type"] == "done" and evs[-1]["ok"] is False
    assert "tool-call budget" in evs[-1]["reason"]


def _calls(*ids):
    return [{"id": i, "type": "function", "function": {"name": "Read", "arguments": "{}"}}
            for i in ids]


def test_trim_eats_the_oldest_tool_results_first():
    msgs = [{"role": "user", "content": "ask"},
            {"role": "assistant", "content": "x", "tool_calls": _calls("1", "2")},
            {"role": "tool", "tool_call_id": "1", "content": "H" * 20000},
            {"role": "tool", "tool_call_id": "2", "content": "T" * 20000},
            {"role": "user", "content": "the real question"}]
    out, trimmed = trim_messages(msgs, 2000)
    assert trimmed >= 1
    assert out[-1]["content"] == "the real question"          # the ask is never trimmed
    assert "trimmed" in out[2]["content"]


def test_context_budget_emits_an_event_and_shrinks_the_request(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_AGENT_CONTEXT_TOKENS", "300")
    seen = []
    h = _harness(_server([_msg("", [("c1", "Read", {"file_path": str(tmp_path / "big.md")})]),
                          _msg("ok")], seen))
    (tmp_path / "big.md").write_text("x" * 40000)
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "read it", allowed_tools=["Read"])
    assert any(e["type"] == "context-trimmed" for e in evs)
    assert len(json.dumps(seen[1]["messages"])) < 40000


def test_file_tools_refuse_a_path_outside_the_mounts(tmp_path):
    sandbox = _Sandbox([tmp_path.resolve()])
    ok, out = run_builtin("Write", {"file_path": "/etc/passwd", "content": "x"}, sandbox)
    assert ok is False and "outside the WRITABLE mounted workspaces" in out
    ok, out = run_builtin("Read", {"file_path": "/etc/passwd"}, sandbox)
    assert ok is False and "outside the mounted workspaces" in out
    ok, out = run_builtin("Write", {"file_path": str(tmp_path / "a/b.md"), "content": "hi"}, sandbox)
    assert ok is True and (tmp_path / "a/b.md").read_text() == "hi"
    ok, out = run_builtin("Edit", {"file_path": str(tmp_path / "a/b.md"),
                                   "old_string": "hi", "new_string": "yo"}, sandbox)
    assert ok is True and (tmp_path / "a/b.md").read_text() == "yo"
    ok, out = run_builtin("Grep", {"pattern": "yo", "path": str(tmp_path)}, sandbox)
    assert ok is True and "b.md" in out


def test_mount_roots_reads_vexa_mounts(tmp_path, monkeypatch):
    other = tmp_path / "global"
    other.mkdir()
    monkeypatch.setenv("VEXA_MOUNTS", json.dumps([{"path": str(other), "role": "global"}]))
    roots = mount_roots(tmp_path / "ws")
    assert other.resolve() in roots


# ── MCP ─────────────────────────────────────────────────────────────────────────────────────────

_STDIO_SERVER = textwrap.dedent('''
    import json, sys
    TOOL = {"name": "entity_upsert", "description": "record a page",
            "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}},
                            "required": ["name"]}}
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        msg = json.loads(raw)
        mid = msg.get("id")
        if mid is None:
            continue
        m = msg.get("method")
        if m == "initialize":
            res = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}}
        elif m == "tools/list":
            res = {"tools": [TOOL]}
        else:
            args = (msg.get("params") or {}).get("arguments") or {}
            res = {"content": [{"type": "text", "text": json.dumps({"created": args.get("name")})}]}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": res}) + "\\n")
        sys.stdout.flush()
''')


def test_mcp_stdio_tools_are_attached_and_callable(tmp_path):
    server = tmp_path / "stub.py"
    server.write_text(_STDIO_SERVER)
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"entities": {
        "type": "stdio", "command": sys.executable, "args": [str(server)]}}}))
    seen = []
    h = _harness(_server([_msg("", [("c1", "mcp__entities__entity_upsert", {"name": "Sam"})]),
                          _msg("filed")], seen))
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "record it",
                  allowed_tools=["Read", "mcp__entities__entity_upsert"], mcp_config=str(cfg))
    offered = {t["function"]["name"] for t in seen[0]["tools"]}
    assert "mcp__entities__entity_upsert" in offered and "Write" not in offered
    result = next(e for e in evs if e["type"] == "tool-result")
    assert result["ok"] is True and "Sam" in result["summary"]


def test_mcp_http_carries_the_delegation_token(tmp_path):
    """The worker's own `mcp.json` shape: the rig over http, the token in a HEADER."""
    calls = []

    def mcp_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append((body.get("method"), request.headers.get("authorization")))
        if body.get("method") == "initialize":
            res = {"protocolVersion": "2025-03-26", "capabilities": {}}
        elif body.get("method") == "tools/list":
            res = {"tools": [{"name": "whats_waiting", "description": "the queue",
                              "inputSchema": {"type": "object", "properties": {}}}]}
        else:
            res = {"content": [{"type": "text", "text": "nothing waiting"}]}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body.get("id"), "result": res})

    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"vexa": {
        "type": "http", "url": "https://rig.example/mcp",
        "headers": {"Authorization": "Bearer delegated-token"}}}}))
    h = _harness(_server([_msg("", [("c1", "mcp__vexa__whats_waiting", {})]), _msg("all clear")]),
                 mcp_http_client=httpx.Client(transport=httpx.MockTransport(mcp_handler)))
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "anything?", allowed_tools=["mcp__vexa"], mcp_config=str(cfg))
    assert ("tools/call", "Bearer delegated-token") in calls
    assert next(e for e in evs if e["type"] == "tool-result")["ok"] is True


def test_an_unreachable_mcp_server_costs_its_tools_not_the_turn(tmp_path):
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"gone": {"type": "stdio",
                                                       "command": "/nonexistent/binary"}}}))
    h = _harness(_server([_msg("answered anyway")]))
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "go", mcp_config=str(cfg))
    assert evs[-1]["ok"] is True and evs[-1]["reply"] == "answered anyway"


# ── streaming + errors ──────────────────────────────────────────────────────────────────────────

def test_streaming_emits_incremental_deltas_and_assembles_tool_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_AGENT_STREAM", "1")
    chunks = [
        {"choices": [{"delta": {"content": "Look"}}]},
        {"choices": [{"delta": {"content": "ing."}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1", "function": {"name": "Glob", "arguments": '{"pat'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": 'tern": "*.md"}'}}]}}]},
    ]
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            payload = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, text=payload,
                                  headers={"content-type": "text/event-stream"})
        payload = ("data: " + json.dumps({"choices": [{"delta": {"content": "Two files."}}]})
                   + "\n\ndata: [DONE]\n\n")
        return httpx.Response(200, text=payload, headers={"content-type": "text/event-stream"})

    h = _harness(handler)
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "list", allowed_tools=["Glob"])
    deltas = [e["text"] for e in evs if e["type"] == "message-delta"]
    assert deltas[:2] == ["Look", "ing."]
    call = next(e for e in evs if e["type"] == "tool-call")
    assert call["tool"] == "Glob" and call["args"] == {"pattern": "*.md"}
    assert evs[-1]["reply"] == "Two files."


def test_auth_failure_is_a_done_not_an_exception(tmp_path):
    h = _harness(lambda request: httpx.Response(401, text="no key"))
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "hi")
    assert evs[-1]["type"] == "done" and evs[-1]["ok"] is False
    assert "VEXA_LLM_API_KEY" in evs[-1]["reply"]


def test_missing_endpoint_is_a_config_error_on_the_done_event(tmp_path, monkeypatch):
    monkeypatch.delenv("VEXA_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    h = OpenAIAgentHarness(base_url="", model="m")
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "hi")
    assert evs[-1]["ok"] is False and "VEXA_LLM_BASE_URL" in evs[-1]["reply"]


def test_unparsable_tool_arguments_fail_the_call_not_the_turn(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if len(body["messages"]) == 1:
            return httpx.Response(200, json={"choices": [{"message": {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "Read", "arguments": "{not json"}}]}}]})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant",
                                                                  "content": "recovered"}}]})
    h = _harness(handler)
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "go", allowed_tools=["Read"])
    assert next(e for e in evs if e["type"] == "tool-result")["ok"] is False
    assert evs[-1]["reply"] == "recovered"


# ── the panel conventions (imported from claude_code, so they must behave identically) ──────────

def test_a_successful_workspace_write_opens_the_tab(tmp_path):
    """decision 18 / the writer-tool tab: the SAME `_WRITER_TOOLS` + `_written_artifact` the claude
    adapter uses, so an openai-agent turn paints the panel identically."""
    server = tmp_path / "stub.py"
    server.write_text(_STDIO_SERVER.replace("entity_upsert", "workspace_write"))
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"vexa": {
        "type": "stdio", "command": sys.executable, "args": [str(server)]}}}))
    h = _harness(_server([_msg("", [("c1", "mcp__vexa__workspace_write",
                                    {"path": "kg/note.md", "slug": "126", "name": "n"})]),
                          _msg("written")]))
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "write it", allowed_tools=["mcp__vexa"], mcp_config=str(cfg))
    art = [e for e in evs if e["type"] == "artifact"]
    assert art and art[0]["workspace"] == "126" and art[0]["path"] == "kg/note.md"
    assert art[0]["focus"] is True


def test_a_failed_write_opens_nothing(tmp_path):
    h = _harness(_server([_msg("", [("c1", "mcp__vexa__workspace_write",
                                    {"path": "kg/note.md", "slug": "126"})]), _msg("sorry")]))
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "write it", allowed_tools=["mcp__vexa"])  # no MCP attached
    assert next(e for e in evs if e["type"] == "tool-result")["ok"] is False
    assert not [e for e in evs if e["type"] == "artifact"]


# ── the sandbox and the allow-set (F85 · F86 · F87) ─────────────────────────────────────────────

def test_the_allow_set_is_enforced_at_EXECUTION_not_only_at_advertisement(tmp_path):
    """F85 REPRODUCTION. `allowed_tools=["Read"]` filters the `tools` array the model is handed —
    and nothing else. A model that names `Write` anyway (small models do, constantly, and a resumed
    transcript carries names an earlier turn had) had its write performed."""
    target = tmp_path / "written-anyway.md"
    h = _harness(_server([
        _msg("", [("c1", "Write", {"file_path": str(target), "content": "escaped"})]),
        _msg("done"),
    ]))
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "write it", allowed_tools=["Read"])
    result = next(e for e in evs if e["type"] == "tool-result")
    assert result["ok"] is False
    assert "not allowed on this turn" in result["summary"]
    assert not target.exists(), "the refused tool must not have run"


def test_an_empty_allow_set_still_means_unrestricted(tmp_path):
    """The claude-code `--allowedTools` semantics `_allowed` documents: no flag = every tool."""
    target = tmp_path / "ok.md"
    h = _harness(_server([
        _msg("", [("c1", "Write", {"file_path": str(target), "content": "hi"})]),
        _msg("done"),
    ]))
    h.prepare(tmp_path)
    _events(h, tmp_path, "write it")
    assert target.read_text() == "hi"


def test_glob_cannot_enumerate_outside_the_mounts(tmp_path):
    """F86 REPRODUCTION. `Read` resolved its argument through the sandbox; `Glob` passed the pattern
    to `Path.glob` raw, so `../../../etc/*` listed a directory no mount contains. The listing IS the
    disclosure — refusing the read that follows is too late."""
    work = tmp_path / "ws"
    work.mkdir()
    (tmp_path / "secret.txt").write_text("outside")
    (work / "inside.md").write_text("inside")
    sandbox = _Sandbox([work.resolve()])
    ok, out = run_builtin("Glob", {"pattern": "../*.txt"}, sandbox)
    assert ok is False and ".." in out
    ok, out = run_builtin("Glob", {"pattern": "/etc/*"}, sandbox)
    assert ok is False
    ok, out = run_builtin("Glob", {"pattern": "*.md"}, sandbox)
    assert ok is True and "inside.md" in out


def test_glob_drops_hits_that_leave_the_mounts_by_symlink(tmp_path):
    """The same escape wearing a legal-looking pattern: a symlink inside the mount. `**` does not
    follow one, but an explicit component does — `link/*.md` walks straight out of the workspace,
    which is why every HIT is resolved and not merely the pattern checked."""
    work = tmp_path / "ws"
    work.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("classified")
    (work / "link").symlink_to(outside)
    sandbox = _Sandbox([work.resolve()])
    ok, out = run_builtin("Glob", {"pattern": "link/*.md"}, sandbox)
    assert ok is True and "secret.md" not in out, out


def test_write_roots_are_the_WRITABLE_mounts_only(tmp_path, monkeypatch):
    """F87 REPRODUCTION. `mount_roots` dropped each mount's `write` flag, so `_global` and every
    read-only desk in a post-meeting room became a writable root for `Write`/`Edit`. Only the docker
    `:ro` bind stood in the way, and the process backend has no such bind."""
    work = tmp_path / "ws"
    work.mkdir()
    ro = tmp_path / "_global"
    ro.mkdir()
    (ro / "README.md").write_text("org tier")
    monkeypatch.setenv("VEXA_MOUNTS", json.dumps([
        {"slug": "ws", "path": str(work), "role": "private", "write": True, "primary": True},
        {"slug": "_global", "path": str(ro), "role": "global", "write": False},
    ]))
    assert ro.resolve() in mount_roots(work)
    assert ro.resolve() not in mount_roots(work, writable_only=True)

    sandbox = _Sandbox(mount_roots(work), mount_roots(work, writable_only=True))
    ok, out = run_builtin("Read", {"file_path": str(ro / "README.md")}, sandbox)
    assert ok is True and "org tier" in out          # readable: it IS in the mount set
    ok, out = run_builtin("Write", {"file_path": str(ro / "hijack.md"), "content": "x"}, sandbox)
    assert ok is False and "WRITABLE" in out
    assert not (ro / "hijack.md").exists()
    ok, out = run_builtin("Edit", {"file_path": str(ro / "README.md"),
                                   "old_string": "org tier", "new_string": "mine"}, sandbox)
    assert ok is False and (ro / "README.md").read_text() == "org tier"
    ok, _ = run_builtin("Write", {"file_path": str(work / "fine.md"), "content": "x"}, sandbox)
    assert ok is True


# ── trimming never orphans a tool reply (F88) ───────────────────────────────────────────────────

def _orphans(msgs):
    """Every unsendable pairing an OpenAI-compatible server 400s on."""
    called = {tc["id"] for m in msgs for tc in (m.get("tool_calls") or [])}
    answered = {m.get("tool_call_id") for m in msgs if m.get("role") == "tool"}
    return (called - answered) | (answered - called)


def test_trim_drops_an_assistant_turn_together_with_its_tool_replies(tmp_path):
    """F88 REPRODUCTION. Step 2 dropped ONE message at a time, so the assistant turn that made the
    calls went and its `tool` answers stayed — a 400 from every OpenAI-compatible server, written
    into the transcript, reproduced on every later turn of a resumed session."""
    # The budget is met the moment the ASSISTANT turn goes — so the old loop stopped right there,
    # leaving its `tool` answer behind with nobody to answer.
    msgs = [{"role": "user", "content": "ask"},
            {"role": "assistant", "content": "X" * 8000, "tool_calls": _calls("c0")},
            {"role": "tool", "tool_call_id": "c0", "content": "small"},
            {"role": "user", "content": "the real question"}]
    out, trimmed = trim_messages(msgs, 200)
    assert trimmed >= 1
    assert _orphans(out) == set(), out
    assert out[-1]["content"] == "the real question"


def test_a_transcript_the_old_trimmer_already_broke_is_healed(tmp_path):
    """The resumed-session half: an orphan already ON DISK must not be sent either. It costs the
    whole turn, so it is repaired rather than counted as trimming."""
    msgs = [{"role": "user", "content": "ask"},
            {"role": "tool", "tool_call_id": "gone", "content": "an answer to nobody"},
            {"role": "assistant", "content": "", "tool_calls": _calls("never-answered")},
            {"role": "user", "content": "now what"}]
    out, trimmed = trim_messages(msgs, 10_000)
    assert trimmed == 0                       # nothing was sacrificed — it was unsendable
    assert _orphans(out) == set()
    assert [m["role"] for m in out] == ["user", "user"]


def test_trim_never_orphans_under_a_brutal_budget():
    msgs = [{"role": "system", "content": "rules"}, {"role": "user", "content": "ask"}]
    for n in range(4):
        msgs.append({"role": "assistant", "content": "", "tool_calls": _calls(f"a{n}", f"b{n}")})
        msgs.append({"role": "tool", "tool_call_id": f"a{n}", "content": "R" * 9000})
        msgs.append({"role": "tool", "tool_call_id": f"b{n}", "content": "S" * 9000})
    msgs.append({"role": "user", "content": "the real question"})
    out, _ = trim_messages(msgs, 50)
    assert _orphans(out) == set(), out


# ── the streaming path (F89 · F90) ──────────────────────────────────────────────────────────────

def _sse(*frames):
    """An SSE handler: each frame is a dict written as one `data:` line, then `[DONE]`."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = "".join(f"data: {json.dumps(f)}\n\n" for f in frames) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body,
                              headers={"Content-Type": "text/event-stream"})
    return handler


def _delta(text):
    return {"choices": [{"delta": {"content": text}}]}


def test_streaming_is_the_default_and_streams_deltas(tmp_path, monkeypatch):
    monkeypatch.delenv("VEXA_AGENT_STREAM", raising=False)
    h = _harness(_sse(_delta("Hel"), _delta("lo")))
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "hi")
    assert [e["text"] for e in evs if e["type"] == "message-delta"] == ["Hel", "lo"]
    assert evs[-1]["type"] == "done" and evs[-1]["ok"] is True and evs[-1]["reply"] == "Hello"


def test_a_mid_stream_error_frame_on_a_200_is_a_failure(tmp_path, monkeypatch):
    """F90 REPRODUCTION. vLLM/LiteLLM/OpenRouter answer 200 and put the failure inside the stream.
    The old loop looked only for `delta`, so the frame was skipped and the turn ended
    `done.ok=True` with an empty reply — the agent said nothing and nothing said why."""
    monkeypatch.delenv("VEXA_AGENT_STREAM", raising=False)
    h = _harness(_sse(_delta("part"), {"error": {"message": "context length exceeded"}}))
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "hi")
    assert evs[-1]["type"] == "done" and evs[-1]["ok"] is False
    assert "context length exceeded" in evs[-1]["reply"]


def test_a_stream_with_no_text_and_no_tool_calls_is_a_failure(tmp_path, monkeypatch):
    """F90 REPRODUCTION, second half: a truncated stream, or a model that spent its whole budget on
    reasoning tokens (the case VEXA_LLM_EXTRA_BODY exists to switch off), reached done.ok=True with
    an empty reply."""
    monkeypatch.delenv("VEXA_AGENT_STREAM", raising=False)
    h = _harness(_sse())
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "hi")
    assert evs[-1]["type"] == "done" and evs[-1]["ok"] is False
    assert "no content and no tool calls" in evs[-1]["reply"]


def test_a_streamed_tool_call_still_answers_the_turn(tmp_path, monkeypatch):
    """A stream carrying only tool_calls (no text) is NOT the empty case."""
    monkeypatch.delenv("VEXA_AGENT_STREAM", raising=False)
    (tmp_path / "n.md").write_text("body")
    state = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["i"] += 1
        if state["i"] == 1:
            frames = [{"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "c1", "function": {"name": "Read",
                                                      "arguments": json.dumps(
                                                          {"file_path": str(tmp_path / "n.md")})}}]}}]}]
        else:
            frames = [_delta("read it")]
        body = "".join(f"data: {json.dumps(f)}\n\n" for f in frames) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    h = _harness(handler)
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "read", allowed_tools=["Read"])
    assert any(e["type"] == "tool-call" for e in evs)
    assert evs[-1]["ok"] is True and evs[-1]["reply"] == "read it"


def test_a_trimmed_turn_says_so_on_done(tmp_path, monkeypatch):
    """F89: `context-trimmed` had no consumer anywhere. The turn is COMPLETE — it just answered from
    less — so it stays ok=True and reports what it gave up."""
    monkeypatch.setenv("VEXA_AGENT_CONTEXT_TOKENS", "300")
    h = _harness(_server([_msg("", [("c1", "Read", {"file_path": str(tmp_path / "big.md")})]),
                          _msg("ok")]))
    (tmp_path / "big.md").write_text("x" * 40000)
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "read it", allowed_tools=["Read"])
    done = evs[-1]
    assert done["type"] == "done" and done["ok"] is True
    assert "context-trimmed" in done["reason"]


# ── the web tools inside the loop (the adapter's own unit tests live in test_llm_web_tools.py) ───

def _web(handler):
    return httpx.MockTransport(handler)


def test_websearch_is_not_advertised_when_no_backend_is_configured(tmp_path, monkeypatch):
    """The harness's rule, applied to the one tool that needs an operator to exist: a `WebSearch`
    advertised with nothing behind it teaches the model that searching does not work, and that
    lesson outlives the turn. `WebFetch` needs no backend and is always there."""
    monkeypatch.delenv("VEXA_SEARCH_URL", raising=False)
    seen = []
    h = _harness(_server([_msg("nothing to do")], seen))
    h.prepare(tmp_path)
    _events(h, tmp_path, "hi")
    names = {t["function"]["name"] for t in seen[0]["tools"]}
    assert "WebSearch" not in names
    assert "WebFetch" in names and "Read" in names


def test_websearch_is_advertised_once_an_endpoint_is_named(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_SEARCH_URL", "http://searx.internal:8080")
    seen = []
    h = _harness(_server([_msg("nothing to do")], seen))
    h.prepare(tmp_path)
    _events(h, tmp_path, "hi")
    assert "WebSearch" in {t["function"]["name"] for t in seen[0]["tools"]}


def test_a_web_search_call_runs_through_the_loop_and_feeds_the_answer_back(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_SEARCH_URL", "http://searx.internal:8080")
    seen = []
    h = _harness(_server([_msg("", [("c1", "WebSearch", {"query": "aswf"})]), _msg("found it")], seen),
                 web_transport=_web(lambda r: httpx.Response(200, json={"results": [
                     {"title": "ASWF", "url": "https://www.aswf.io/", "content": "a neutral forum"}]})))
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "who is aswf", allowed_tools=["WebSearch"])
    result = next(e for e in evs if e["type"] == "tool-result")
    assert result["ok"] is True and "aswf.io" in result["summary"]
    # the result reached the NEXT request as a tool message — that is what "feeds back" means
    assert any(m.get("role") == "tool" and "aswf.io" in m["content"] for m in seen[1]["messages"])
    assert evs[-1]["ok"] is True and evs[-1]["reply"] == "found it"


def test_a_web_fetch_result_is_trimmed_to_the_tool_result_ceiling(tmp_path, monkeypatch):
    from llm.openai_agent import _TOOL_RESULT_MAX_CHARS
    monkeypatch.setattr("llm.web_tools._resolve", lambda h: ["93.184.216.34"])
    huge = "<html><title>T</title><body><p>" + ("word " * 200_000) + "</p></body></html>"
    h = _harness(_server([_msg("", [("c1", "WebFetch", {"url": "https://www.aswf.io/",
                                                        "max_chars": 10 ** 9})]), _msg("ok")]),
                 web_transport=_web(lambda r: httpx.Response(
                     200, headers={"content-type": "text/html"}, text=huge)))
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "read it", allowed_tools=["WebFetch"])
    assert next(e for e in evs if e["type"] == "tool-result")["ok"] is True
    stored = [m for m in _transcript(tmp_path) if m.get("type") == "user"]
    # the ceiling is enforced on the message fed back to the model, which is the one that costs context
    tool_msgs = [m for m in stored if isinstance(m.get("oa"), dict) and m["oa"].get("role") == "tool"]
    assert tool_msgs and len(tool_msgs[-1]["oa"]["content"]) <= _TOOL_RESULT_MAX_CHARS


def test_a_private_address_is_refused_inside_the_loop(tmp_path):
    h = _harness(_server([_msg("", [("c1", "WebFetch", {"url": "http://169.254.169.254/latest/"})]),
                          _msg("understood")]))
    h.prepare(tmp_path)
    evs = _events(h, tmp_path, "read the metadata service", allowed_tools=["WebFetch"])
    result = next(e for e in evs if e["type"] == "tool-result")
    assert result["ok"] is False and "169.254.169.254" in result["summary"]
    assert evs[-1]["ok"] is True          # a refusal is an ordinary result; the turn goes on
