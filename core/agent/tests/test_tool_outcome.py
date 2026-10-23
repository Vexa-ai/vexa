"""How a tool call ended rides on its `tool-result` (held-label-rooms-1018).

Founder screenshot, app.dev 2026-10-18: the chat's turn line read "✓ Wrote · 7 steps" over a reply
that said "Nothing was written. The refactored README is held". The privacy hold (D6) had refused
the write (409 `private_sources_to_shared`), and nothing on the stream said so: the step's tick came
from the next step arriving. Every harness now says `outcome` (`held`, `refused`, `failed`) and a
held write carries the approval card's fields (`held`), live and in the reloaded history.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from control_plane.workspace_reader import parse_history
from llm.claude_code import parse_stream_json
from llm.codex import _tool_completed
from llm.openai_agent import OpenAIAgentHarness
from shared.tool_outcome import result_outcome

REFUSAL = {"refused": "private_sources_to_shared",
           "fault": {"code": "held_private_egress", "surface": "page", "held": True, "release": "confirm a8nv7c"},
           "surface": "page", "workspace": "oenb-pilot", "workspace_name": "OeNB × Vexa pilot",
           "path": "README.md", "readers": ["ana", "bob"], "sources": ["gmail"], "confirm_code": "a8nv7c",
           "carried": ["the Q3 number"], "instruction": "Nothing was written. …"}
HELD = {"code": "a8nv7c", "workspace": "oenb-pilot", "workspace_name": "OeNB × Vexa pilot", "path": "README.md",
        "readers": ["ana", "bob"], "sources": ["gmail"]}
#: what the MCP edge hands the agent for a 409: the body inside an error string, `isError: true`
MCP_TEXT = "Error calling tool 'workspace_write': HTTP 409: " + json.dumps({"detail": REFUSAL})


# ── the classifier ──────────────────────────────────────────────────────────────────────────────

def test_a_held_write_is_held_with_the_cards_fields_and_nothing_it_carried():
    out = result_outcome(False, MCP_TEXT)
    assert out == {"outcome": "held", "held": HELD}
    assert "carried" not in json.dumps(out) and "Q3" not in json.dumps(out)


@pytest.mark.parametrize("content", [
    MCP_TEXT,
    [{"type": "text", "text": MCP_TEXT}],                    # claude-code content blocks
    {"content": [{"type": "text", "text": MCP_TEXT}]},       # an MCP result envelope (codex)
    json.dumps(REFUSAL),                                    # a bare body
])
def test_every_shape_a_harness_hands_over_is_read(content):
    assert result_outcome(False, content)["outcome"] == "held"


def test_other_endings():
    assert result_outcome(True, "wrote README.md") == {}
    assert result_outcome(False, "boom") == {"outcome": "failed"}
    assert result_outcome(False, 'HTTP 403: {"detail": {"refused": "out_of_scope"}}') == {"outcome": "refused"}
    # a successful read whose CONTENT quotes a refusal is still a read that worked
    assert result_outcome(True, 'the doc says {"refused": "out_of_scope"}') == {}


# ── each harness puts it on the event ──────────────────────────────────────────────────────────

def test_claude_code_marks_the_held_result():
    lines = [json.dumps({"type": "assistant", "message": {"content": [
                 {"type": "tool_use", "name": "mcp__vexa__workspace_write", "input": {"path": "README.md"}, "id": "c1"}]}}),
             json.dumps({"type": "user", "message": {"content": [
                 {"type": "tool_result", "tool_use_id": "c1", "is_error": True,
                  "content": [{"type": "text", "text": MCP_TEXT}]}]}})]
    res = next(e for e in parse_stream_json(iter(lines)) if e["type"] == "tool-result")
    assert res["ok"] is False and res["outcome"] == "held" and res["held"] == HELD


def test_codex_marks_the_held_result():
    ev = _tool_completed({"type": "mcpToolCall", "id": "c1", "status": "failed", "error": MCP_TEXT})
    assert ev["outcome"] == "held" and ev["held"]["code"] == "a8nv7c"
    ok = _tool_completed({"type": "mcpToolCall", "id": "c2", "status": "completed", "result": {"content": []}})
    assert "outcome" not in ok


def test_openai_agent_marks_the_held_result(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_AGENT_STREAM", "0")
    monkeypatch.setenv("VEXA_AGENT_AUTO_CONTINUE_CHAT", "0")
    script = [{"role": "assistant", "content": "", "tool_calls": [{"id": "w1", "type": "function",
               "function": {"name": "Write", "arguments": json.dumps({"file_path": str(tmp_path / "README.md"),
                                                                       "content": "x"})}}]},
              {"role": "assistant", "content": "Nothing was written. The README is held."}]
    state = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        msg = script[min(state["i"], 1)]
        state["i"] += 1
        return httpx.Response(200, json={"choices": [{"message": msg, "finish_reason":
                                                      "tool_calls" if msg.get("tool_calls") else "stop"}]})
    h = OpenAIAgentHarness(transport=httpx.MockTransport(handler), base_url="http://ccc.local/v1", model="m")
    h.prepare(tmp_path)
    monkeypatch.setattr(h, "_exec_tool", lambda call, *a, **k: (False, MCP_TEXT))
    evs = list(h.run_turn(Path(tmp_path), "refactor the page", allowed_tools=["Write"]))
    res = next(e for e in evs if e["type"] == "tool-result")
    assert res["ok"] is False and res["outcome"] == "held" and res["held"] == HELD


# ── and the reloaded history keeps it ──────────────────────────────────────────────────────────

def test_history_ops_carry_the_outcome():
    raw = "\n".join(json.dumps(x) for x in [
        {"type": "user", "message": {"role": "user", "content": "fully refactor the page"}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/workspaces/oenb-pilot/README.md"}, "id": "r1"},
            {"type": "tool_use", "name": "mcp__vexa__workspace_write", "input": {"file_path": "/workspaces/oenb-pilot/README.md"}, "id": "w1"},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "/workspaces/oenb-pilot/plan.md"}, "id": "e1"}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "r1", "is_error": False, "content": "# README"},
            {"type": "tool_result", "tool_use_id": "w1", "is_error": True, "content": MCP_TEXT},
            {"type": "tool_result", "tool_use_id": "e1", "is_error": True, "content": "no such file"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Nothing was written."}]}},
    ])
    turns = parse_history(raw, [])
    ops = turns[-1]["ops"]
    assert [o.get("outcome") for o in ops] == [None, "held", "failed"]
    assert all("_id" not in o for o in ops)          # the pairing id is not served
