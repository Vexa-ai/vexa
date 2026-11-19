"""A provider's 402 ends the turn ONCE, typed, with no account link in the chat.

On the demo stack (OpenRouter, a GLM model) a chat turn was refused with OpenRouter's 402 — "You
requested up to 32000 tokens, but can only afford 4857. To increase, visit <the key's management
page>…" — and the terminal printed the provider's raw text twice: once as the agent's words
(`API Error: 402 …`) and once more as `Model inference failed: API Error: 402 …`, with the link.

The stream below is the claude CLI's own (2.1.293, the worker image's pin), captured offline against
an endpoint that answers exactly that 402, trimmed to the lines the parser reads. Pinned here:
  * no message-delta carries the provider's text — the CLI's synthetic message is a fault, not prose;
  * one `done`, `ok: false`, typed `unpaid` with status 402;
  * neither the reply nor the fault carries a link: the link goes to the operator's log only;
  * the remedy names the output cap, the dial for "requested more than the balance covers".
"""
from __future__ import annotations

import json
import logging

import pytest

from llm import faults
from llm.claude_code import parse_stream_json

SAID = ("This request requires more credits, or fewer max_tokens. You requested up to 32000 "
        "tokens, but can only afford 4857. To increase, visit https://openrouter.ai/settings/keys "
        "and create a key with a higher total limit")
LINK = "https://openrouter.ai/settings/keys"

CLI_2_1_293_STREAM = [
    {"type": "system", "subtype": "init", "model": "z-ai/glm-4.6"},
    {"type": "system", "subtype": "status"},
    {"type": "assistant", "error": "unknown", "is_api_error_message": True,
     "message": {"model": "<synthetic>", "role": "assistant", "type": "message",
                 "content": [{"type": "text", "text": "API Error: 402 " + SAID}]}},
    {"type": "result", "subtype": "success", "is_error": True, "api_error_status": 402,
     "terminal_reason": "api_error", "session_id": "s-1", "result": "API Error: 402 " + SAID},
]


@pytest.fixture(autouse=True)
def _openrouter(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://openrouter.ai/api")


def _events(stream):
    return list(parse_stream_json(json.dumps(o) for o in stream))


def test_the_cli_402_ends_the_turn_once_typed_and_without_its_link(caplog):
    caplog.set_level(logging.WARNING, logger="llm.faults")
    evs = _events(CLI_2_1_293_STREAM)
    assert [e["type"] for e in evs] == ["done"]           # no raw `API Error` bubble before it
    done = evs[0]
    assert done["ok"] is False
    f = done["fault"]
    assert (f["source"], f["kind"], f["status"], f["provider"]) == (
        "model-provider", "unpaid", 402, "openrouter.ai")
    assert "can only afford 4857" in f["detail"]
    for text in (done["reply"], f["detail"], f["remedy"]):
        assert "http" not in text and "settings/keys" not in text and "API Error" not in text
    assert "VEXA_AGENT_MAX_OUTPUT_TOKENS" in f["remedy"]
    # …and the operator still has the link, in the log.
    assert any(LINK in r.getMessage() for r in caplog.records)


def test_a_cli_build_that_does_not_mark_its_message_still_cannot_speak_for_the_agent():
    unmarked = [dict(CLI_2_1_293_STREAM[2])]
    unmarked[0].pop("error"), unmarked[0].pop("is_api_error_message")
    unmarked[0]["message"] = {**unmarked[0]["message"], "model": "z-ai/glm-4.6"}
    evs = _events([CLI_2_1_293_STREAM[0], *unmarked, CLI_2_1_293_STREAM[3]])
    assert [e["type"] for e in evs] == ["done"]
    assert evs[0]["fault"]["kind"] == "unpaid"


def test_the_status_in_the_text_wins_over_the_cli_label():
    f = faults.classify(text="API Error: 402 " + SAID, sdk_error="billing_error", provider="openrouter.ai")
    assert (f.kind, f.status) == ("unpaid", 402)


def test_a_detail_never_carries_a_link_but_keeps_the_rest_of_the_words():
    d = faults.safe_detail(json.dumps({"error": {"message": SAID, "code": 402}}))
    assert d == ("This request requires more credits, or fewer max_tokens. You requested up to "
                 "32000 tokens, but can only afford 4857.")
    assert faults.provider_links(SAID) == [LINK]
    # a bare host-and-path is a link too
    assert faults.safe_detail("Out of credits. Top up at openrouter.ai/settings/credits now.") == "Out of credits."
