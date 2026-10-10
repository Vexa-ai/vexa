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


def test_an_expired_authorization_passes_by_opening_the_panel():
    assert chat_eval.score(CASE, "reconnect_required", _reply(["connection_request"]))[0]


def test_an_outage_passes_only_on_the_explanation():
    """In the outage, what is scored is what the agent tells the person: a call alone, or a final
    answer without the explanation, fails."""
    text = "Gmail is connected; this is an outage and reconnecting won't fix it."
    assert not chat_eval.score(CASE, "store_unavailable", _reply(["connection_request"]))[0]
    assert not chat_eval.score(CASE, "store_unavailable", _reply(["connection_request"], text))[0]
    assert chat_eval.score(CASE, "store_unavailable", _reply(text=text))[0]


def test_in_the_outage_connection_request_answers_that_it_opened_nothing():
    replies = iter([_reply(["connection_request"]), _reply(text="Done, the panel is open.")])
    reply = chat_eval.run(CASE, "store_unavailable", chat_eval.messages(CASE, "store_unavailable", "s"),
                          lambda msgs: next(replies))
    assert reply["lookups"] == ["connection_request"]
    assert not chat_eval.score(CASE, "store_unavailable", reply)[0]     # claiming it opened fails


@pytest.mark.parametrize("variant", ["reconnect_required", "store_unavailable"])
def test_retrying_the_failed_tool_always_fails(variant):
    assert not chat_eval.score(CASE, variant, _reply(["gmail_draft_create"]))[0]
    assert not chat_eval.score(CASE, variant, _reply(["connection_request", "gmail_draft_create"]))[0]


def test_an_outage_explanation_passes_only_for_the_outage():
    text = ("Gmail is already connected. The failure is an outage of the credential store, so "
            "reconnecting won't fix it; please try again later.")
    assert chat_eval.score(CASE, "store_unavailable", _reply(text=text))[0]
    assert not chat_eval.score(CASE, "reconnect_required", _reply(text=text))[0]
    assert not chat_eval.score(CASE, "store_unavailable", _reply(text="Sure, done!"))[0]


def test_a_status_lookup_is_answered_and_the_next_step_is_scored():
    replies = iter([_reply(["connections_status"]),
                    _reply(text="Gmail is connected; this is an outage and reconnecting won't fix it.")])
    seen = []

    def complete(msgs):
        seen.append(msgs)
        return next(replies)
    reply = chat_eval.run(CASE, "store_unavailable", chat_eval.messages(CASE, "store_unavailable", "s"), complete)
    assert reply["lookups"] == ["connections_status"] and chat_eval.score(CASE, "store_unavailable", reply)[0]
    assert json.loads(seen[1][-1]["content"])["connections"][0]["status"] == "ready"


def test_a_lookup_then_the_refused_panel_then_no_explanation_fails_the_outage():
    replies = iter([_reply(["connections_status"]), _reply(["connection_request"]), _reply(text="Opened it.")])
    reply = chat_eval.run(CASE, "store_unavailable", chat_eval.messages(CASE, "store_unavailable", "s"),
                          lambda msgs: next(replies))
    assert reply["lookups"] == ["connections_status", "connection_request"]
    assert not chat_eval.score(CASE, "store_unavailable", reply)[0]


def test_the_failing_tools_answer_is_what_agent_api_sends():
    for reason, variant in CASE["variants"].items():
        assert variant["detail"]["reason"] == reason
        assert variant["detail"]["instruction"] == INSTRUCTIONS[reason]
    refusal = CASE["variants"]["store_unavailable"]["lookups"]["connection_request"]["detail"]
    assert refusal["instruction"] == INSTRUCTIONS["store_unavailable"]
