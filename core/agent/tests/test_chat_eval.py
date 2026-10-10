"""The chat cases and their scorer, offline (`eval/chat_eval.py`; the live run is by hand)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
import chat_eval  # noqa: E402

from control_plane.routers.connections import INSTRUCTIONS  # noqa: E402

CASE = chat_eval.load_case("connect-gmail")


def _reply(calls=(), text=None):
    return {"content": text, "tool_calls": [{"function": {"name": n, "arguments": "{}"}} for n in calls]}


def test_the_case_uses_only_tools_agent_api_serves_and_reasons_it_answers():
    specs = {s["function"]["name"] for s in chat_eval.tool_specs(CASE["tools"])}
    assert specs == set(CASE["tools"])
    for variant in CASE["variants"].values():
        assert variant["detail"]["reason"] in INSTRUCTIONS


def test_the_conversation_ends_on_the_persons_request_with_the_variants_tool_answer():
    msgs = chat_eval.messages(CASE, "store_unavailable", "system")
    assert msgs[-1] == {"role": "user", "content": "let's connect gmail"}
    tool = next(m for m in msgs if m["role"] == "tool")
    assert json.loads(tool["content"])["detail"]["reason"] == "store_unavailable"


@pytest.mark.parametrize("variant", ["reconnect_required", "store_unavailable"])
def test_calling_connection_request_passes_and_retrying_fails(variant):
    assert chat_eval.score(CASE, variant, _reply(["connection_request"]))[0]
    assert not chat_eval.score(CASE, variant, _reply(["gmail_draft_create"]))[0]
    assert not chat_eval.score(CASE, variant, _reply(["connection_request", "gmail_draft_create"]))[0]


def test_an_outage_explanation_passes_only_for_the_outage():
    text = ("Gmail is already connected. The failure is an outage of the credential store, so "
            "reconnecting won't fix it; please try again later.")
    assert chat_eval.score(CASE, "store_unavailable", _reply(text=text))[0]
    assert not chat_eval.score(CASE, "reconnect_required", _reply(text=text))[0]
    assert not chat_eval.score(CASE, "store_unavailable", _reply(text="Sure, done!"))[0]
