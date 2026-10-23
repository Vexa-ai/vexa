"""UI DECISION 6b — the agent PROPOSES where a chat writes; only the person's click moves it.

Founder, 2026-10-15: "the agent should think which workspace it wants to write to and should ask to
change its writing target proactively if it thinks it needs to write there or doubts if writing to
default workspace is the right target". Live case: a chat writing to biz, asked to update the OeNB
pilot from a transcript, started writing biz's README."""
from __future__ import annotations

import json

from control_plane import workspace_attach as attach
from tests.test_chat_profiles import H, env  # noqa: F401 — the fixture

AGENT = {**H, "x-user-regime": "human", "x-user-delegation-unit": "agent-u1-chat-pchat-w1-g1"}


def _two(root):
    biz = attach.create_workspace(root, "u1", name="biz").slug
    oenb = attach.create_workspace(root, "u1", name="OeNB × Vexa pilot").slug
    return biz, oenb


def _target(c, session="pchat-w1"):
    return c.get("/api/workspace/states", params={"session": session}, headers=H).json()["chat"]["target"]


def test_the_proposal_changes_nothing(env):
    root, sessions, c = env
    biz, oenb = _two(root)
    assert c.post("/api/chat/target", json={"session": "pchat-w1", "workspace": biz}, headers=H).status_code == 200
    r = c.post("/api/workspace/propose-target", headers=H,
               json={"workspace": "OeNB × Vexa pilot", "reason": "the request is about the OeNB pilot", "session": "pchat-w1"})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["ui_action"] == "propose_target" and p["proposed"] == oenb and p["name"] == "OeNB × Vexa pilot"
    assert p["current"] == biz and p["current_name"] == "biz" and "WAIT" in p["say"]
    assert _target(c) == biz                                   # still biz — nothing moved


def test_the_agent_cannot_switch_the_target_itself(env):
    root, sessions, c = env
    biz, oenb = _two(root)
    c.post("/api/chat/target", json={"session": "pchat-w1", "workspace": biz}, headers=H)
    refused = c.post("/api/chat/target", json={"session": "pchat-w1", "workspace": oenb}, headers=AGENT)
    assert refused.status_code == 403 and "propose_write_target" in refused.json()["detail"]
    old_verb = c.post("/api/workspace/target", json={"slug": oenb}, headers=AGENT)
    assert old_verb.status_code == 409 and "propose_write_target" in old_verb.json()["detail"]
    assert _target(c) == biz


def test_the_persons_click_switches_it(env):
    root, sessions, c = env
    biz, oenb = _two(root)
    c.post("/api/chat/target", json={"session": "pchat-w1", "workspace": biz}, headers=H)
    assert c.post("/api/chat/target", json={"session": "pchat-w1", "workspace": oenb}, headers=H).status_code == 200
    assert _target(c) == oenb


def test_proposing_where_it_already_writes_says_go_ahead(env):
    root, sessions, c = env
    biz, _ = _two(root)
    c.post("/api/chat/target", json={"session": "pchat-w1", "workspace": biz}, headers=H)
    p = c.post("/api/workspace/propose-target", headers=H, json={"workspace": "biz", "session": "pchat-w1"}).json()
    assert p["proposed"] is None and p["already"] is True


def test_a_proposal_becomes_an_inline_event_and_the_old_verb_no_longer_focuses():
    from llm.tool_events import _FOCUS_TOOLS, _OPEN_TOOLS, _open_event
    assert "mcp__vexa__propose_write_target" in _OPEN_TOOLS
    assert "mcp__vexa__workspace_target" not in _FOCUS_TOOLS
    ev = _open_event(json.dumps({"ui_action": "propose_target", "proposed": "oenb-vexa-pilot",
                                 "name": "OeNB × Vexa pilot", "current": "biz-1", "current_name": "biz",
                                 "reason": "the request is about the OeNB pilot"}))
    assert ev == {"type": "propose-target", "workspace": "oenb-vexa-pilot", "name": "OeNB × Vexa pilot",
                  "current": "biz-1", "current_name": "biz", "reason": "the request is about the OeNB pilot"}


NAMES = [{"slug": "personal", "name": "Personal"}, {"slug": "biz-b1c87fc32ecb", "name": "biz"},
         {"slug": "oenb-vexa-pilot", "name": "OeNB × Vexa pilot"}, {"slug": "finos-0dc7fe", "name": "FINOS"}]


def test_update_the_oenb_workspace_in_a_biz_chat_is_told_to_propose(monkeypatch):
    """The founder's case, as the turn is TOLD it: named workspace ≠ target ⇒ propose, before any write."""
    from worker.engine import named_workspaces, write_target_preamble
    monkeypatch.setenv("VEXA_WORKSPACE_NAMES", json.dumps(NAMES))
    ask = "update the OeNB workspace from the transcript of today's call"
    assert [h["slug"] for h in named_workspaces(ask, NAMES, "biz-b1c87fc32ecb")] == ["oenb-vexa-pilot"]
    text = write_target_preamble(ask, "biz-b1c87fc32ecb")
    assert "This chat writes to **biz**" in text
    assert "**This request names OeNB × Vexa pilot**" in text
    assert 'propose_write_target(workspace="OeNB × Vexa pilot"' in text
    assert "write nothing to biz meanwhile" in text


def test_a_request_about_the_target_or_about_nothing_names_no_other_workspace(monkeypatch):
    from worker.engine import named_workspaces, write_target_preamble
    monkeypatch.setenv("VEXA_WORKSPACE_NAMES", json.dumps(NAMES))
    assert named_workspaces("update the OeNB pilot notes", NAMES, "oenb-vexa-pilot") == []
    assert named_workspaces("summarise the call and draft a follow-up", NAMES, "biz-b1c87fc32ecb") == []
    assert named_workspaces("a pilot for vexa", NAMES, "biz-b1c87fc32ecb") == []   # everyday words only
    assert "This request names" not in write_target_preamble("draft the follow-up", "biz-b1c87fc32ecb")
    # …the rule itself is said on every turn, named workspace or not
    assert "propose_write_target(workspace, reason)" in write_target_preamble("draft the follow-up", "")
