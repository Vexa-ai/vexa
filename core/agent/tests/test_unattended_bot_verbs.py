"""No MCP tool that stops, reconfigures, speaks through or types from the person's live bot reaches a
turn with nobody in the loop without the gate.

A routine's turn, an inbound mail's (D25) and a meeting signal's carry the `bot` scope on a
delegation whose regime is not `human`. Two lines hold them off the live bot:

* meeting-api refuses the verbs (`meeting_api.regime.LIVE_BOT_VERBS`, its own tests);
* a worker the model proxy does not front holds the bearer in the attachment its harness reads,
  so it is offered its tools by name and none of the live bot's verbs it was not granted
  (`worker.engine.unattended_toolbelt`).

The guard below reads the served surface from source, so a tool added later (a chat-send tool, a
speak backend) fails here until it is in `UNATTENDED_BOT_VERBS`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from shared import delegation
from worker.engine import (UNATTENDED_BOT_VERBS, VEXA_MCP_SERVER, mcp_delegation_config,
                           unattended_toolbelt)

REPO = Path(__file__).resolve().parents[3]
MCP_SOURCES = sorted(REPO.glob("core/*/services/mcp/src/vexa_mcp/app.py"))
MUTATING = ("POST", "PUT", "PATCH", "DELETE")
#: A tool body's call into meeting-api's live bot: `/bots/{_room(…)}` and anything under it.
_CALL = re.compile(r'make_request\(\s*"(POST|PUT|PATCH|DELETE)",\s*f"\{base_url\}/bots/\{_room\(')
_TOOL = re.compile(r'@app\.(?:get|post|put|patch|delete)\([^)]*operation_id="(\w+)"')
_LIVE_PATH = re.compile(r"^/bots/\{platform\}/\{native_meeting_id\}(/.*)?$")


def _built_in_live_bot_tools() -> set:
    """Every built-in MCP tool whose body sends a mutating call to `/bots/{platform}/{native}…`."""
    assert len(MCP_SOURCES) == 1, MCP_SOURCES
    src = MCP_SOURCES[0].read_text()
    marks = [(m.start(), m.group(1)) for m in _TOOL.finditer(src)]
    out = set()
    for i, (start, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(src)
        if _CALL.search(src[start:end]):
            out.add(name)
    return out


def _manifest_live_bot_tools() -> set:
    out = set()
    for path in sorted((REPO / "core").rglob("*mcp.tools.v1.json")):
        if any(p.startswith(".") or p in {"node_modules", "dist"} for p in path.parts):
            continue
        for tool in json.loads(path.read_text()).get("tools") or []:
            route = tool.get("route") or {}
            if route.get("method") in MUTATING and _LIVE_PATH.match(str(route.get("path") or "")):
                out.add(tool["name"])
    return out


def test_every_tool_that_acts_on_the_live_bot_is_gated_for_unattended_turns():
    found = _built_in_live_bot_tools() | _manifest_live_bot_tools()
    # the scan is not vacuous: the three verbs the line serves today are found by it
    assert {"stop_bot", "update_bot_config", "speak_in_meeting"} <= found, found
    ungated = found - set(UNATTENDED_BOT_VERBS)
    assert not ungated, (
        f"{sorted(ungated)} act on the person's live bot and are not in "
        "worker.engine.UNATTENDED_BOT_VERBS: add them, and guard their meeting-api route with "
        "regime.live_bot_guard")


def test_only_stop_and_request_can_be_granted_back():
    assert {v for v in UNATTENDED_BOT_VERBS.values() if v} <= set(delegation.UNATTENDED_GRANTS)
    assert UNATTENDED_BOT_VERBS["update_bot_config"] == ""
    assert UNATTENDED_BOT_VERBS["speak_in_meeting"] == ""


# ── the worker's allow-set, with no model proxy in front ────────────────────────────────────────
PREFIX = f"mcp__{VEXA_MCP_SERVER}"
LIVE = {f"{PREFIX}__{t}" for t in UNATTENDED_BOT_VERBS}
CHAT = "agent-7-chat-s1"


def _tools(monkeypatch, tmp_path, *, proxy: bool = False, **mint) -> list:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    monkeypatch.setenv("VEXA_MCP_URL", "http://mcp.test")
    if proxy:
        monkeypatch.setenv("VEXA_MODEL_PROXY_URL", "http://model-proxy:8102")
    else:
        monkeypatch.delenv("VEXA_MODEL_PROXY_URL", raising=False)
    monkeypatch.setenv("VEXA_MCP_DELEGATION_TOKEN", delegation.mint_delegation("k", subject="7", **mint))
    _path, tools = mcp_delegation_config(work)
    return tools


UNATTENDED = {
    "mail": {"regime": "autonomous", "workspaces": [], "unit": f"{CHAT}-mail"},
    "signal": {"regime": "autonomous", "workspaces": [], "unit": f"{CHAT}-signal"},
    "routine": {"regime": "autonomous", "workspaces": ["7"], "unit": f"{CHAT}-auto"},
}


@pytest.mark.parametrize("who", sorted(UNATTENDED))
def test_an_unattended_worker_without_the_proxy_gets_no_live_bot_tool(monkeypatch, tmp_path, who):
    tools = _tools(monkeypatch, tmp_path, **UNATTENDED[who])
    assert PREFIX not in tools, "the bare server prefix would permit every tool it serves"
    assert not LIVE & set(tools), sorted(LIVE & set(tools))
    assert f"{PREFIX}__get_meeting_transcript" in tools, "its other tools are still named"


def test_a_routine_granted_bot_stop_gets_stop_and_nothing_else_of_the_live_bot(monkeypatch, tmp_path):
    tools = _tools(monkeypatch, tmp_path, regime="autonomous", workspaces=["7"], unit=f"{CHAT}-auto",
                   grants=["bot.stop"], flow="user:7/stop-at-1300")
    assert f"{PREFIX}__stop_bot" in tools
    assert not (LIVE - {f"{PREFIX}__stop_bot"}) & set(tools)


def test_a_watched_turn_keeps_its_whole_toolbelt(monkeypatch, tmp_path):
    tools = _tools(monkeypatch, tmp_path, regime="human", workspaces="*", unit=CHAT)
    assert tools[0] == PREFIX


def test_behind_the_proxy_the_allow_set_is_unchanged(monkeypatch, tmp_path):
    """The proxy holds the bearer; the model's shell holds only its turn token, and meeting-api
    refuses the verbs either way."""
    tools = _tools(monkeypatch, tmp_path, proxy=True, **UNATTENDED["mail"])
    assert tools[0] == PREFIX


def test_unattended_toolbelt_names_no_ungranted_live_verb():
    assert not LIVE & set(unattended_toolbelt())
    assert PREFIX not in unattended_toolbelt()
    assert f"{PREFIX}__request_meeting_bot" in unattended_toolbelt({"bot.request"})
