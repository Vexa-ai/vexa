"""The agent gives a workspace its image unasked (UI decision 15f): the guidance every dispatch
carries names the two served verbs, the order (company logo, site logo, designed mark), the
never-hot-link rule, and the proposal answer in a shared workspace."""
from __future__ import annotations

import json
from pathlib import Path

from worker import engine

AGENT = Path(__file__).resolve().parents[1]
SERVED = {t["name"] for t in json.loads((AGENT / "mcp.tools.v1.json").read_text())["tools"]}
WORKER = set(json.loads((AGENT / "worker/mcp_tools.v1.json").read_text())["tools"])


def test_every_dispatch_carries_the_identity_guidance():
    assert engine.identity_preamble() in engine.page_verbs_preamble()


def test_it_names_served_verbs_only():
    text = engine.identity_preamble()
    for verb in ("workspace_icon_state", "workspace_icon_set"):
        assert f"`{verb}(" in text and verb in SERVED and verb in WORKER


def test_it_says_logo_first_then_a_designed_mark_and_never_a_hotlink():
    text = engine.identity_preamble().lower()
    assert text.index("company_logo") < text.index("site=") < text.index("svg=")
    assert "never put a remote image address" in text
    assert "not clip-art" in text and "not an emoji" in text and "24 px" in text
    assert "proposed" in text and "one line" in text


def test_a_missing_tool_is_never_told_to_the_person():
    """JTBD-1 blocker 3: the agent told a person "the tool isn't wired into this session" twice.
    A missing or refused internal tool falls back quietly: the default monogram, or a proposal."""
    assert "never tell the person that a tool" in engine.voice_preamble().lower()
    assert "monogram stays" in engine.identity_preamble().lower()


def test_the_icon_tool_takes_propose():
    """The create flow asks for `workspace_icon_set(..., propose: true)`; the manifest must pass it."""
    tool = next(t for t in json.loads((AGENT / "mcp.tools.v1.json").read_text())["tools"]
                if t["name"] == "workspace_icon_set")
    assert "propose" in tool["arguments"]
