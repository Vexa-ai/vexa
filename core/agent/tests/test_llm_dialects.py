"""L2: llm.dialects — the wire-shape judgement that Vexa-ai/vexa#1666 exists because we lacked.

Every case here used to produce an EMPTY completion and no error, or a message that named no
cause. The rule under test is: a 2xx body that is not this dialect's success shape is a NAMED
failure, and the name says which dialect answered and which setting moves.
"""
import json

import pytest

from llm.dialects import (ANTHROPIC, OPENAI, anthropic_text, detect_shape, openai_text,
                          parse_headers, redact_headers, render_headers)
from llm.errors import LLMError

BASE = "https://gw.example/v1"
CHAT = json.dumps({"object": "chat.completion",
                   "choices": [{"message": {"content": "pong"}, "finish_reason": "stop"}]})
MSG = json.dumps({"type": "message", "role": "assistant",
                  "content": [{"type": "text", "text": "pong"}], "stop_reason": "end_turn"})


# ── shape detection ───────────────────────────────────────────────────────────────────────────

def test_detect_shape():
    assert detect_shape(json.loads(CHAT)) == OPENAI
    assert detect_shape(json.loads(MSG)) == ANTHROPIC
    assert detect_shape({"error": {"message": "nope"}}) is None  # an error envelope is neither
    assert detect_shape("not a dict") is None


# ── the happy paths ───────────────────────────────────────────────────────────────────────────

def test_extracts_each_dialects_own_text():
    assert openai_text(CHAT, base=BASE) == "pong"
    assert anthropic_text(MSG, base=BASE) == "pong"


def test_openai_multimodal_part_list():
    body = json.dumps({"choices": [{"message": {"content": [{"type": "text", "text": "a"},
                                                            {"type": "text", "text": "b"}]}}]})
    assert openai_text(body, base=BASE) == "ab"


# ── #1666: HTTP 200 carrying the other dialect ────────────────────────────────────────────────

def test_anthropic_body_at_the_openai_path_is_named_not_empty():
    with pytest.raises(LLMError) as exc:
        openai_text(MSG, base=BASE)
    msg = str(exc.value)
    assert "DIALECT MISMATCH" in msg
    assert "/chat/completions" in msg and "Anthropic Messages" in msg


def test_openai_body_at_the_messages_path_is_named_not_empty():
    with pytest.raises(LLMError) as exc:
        anthropic_text(CHAT, base=BASE)
    msg = str(exc.value)
    assert "DIALECT MISMATCH" in msg
    assert "/v1/messages" in msg and "OpenAI chat-completions" in msg


# ── #1666: a 200 that is not an API response at all ────────────────────────────────────────────

def test_html_from_a_cdn_names_the_interception():
    page = "<!DOCTYPE html><html><body>403 Forbidden — cloudflare</body></html>"
    for extract in (openai_text, anthropic_text):
        with pytest.raises(LLMError) as exc:
            extract(page, base=BASE, content_type="text/html; charset=utf-8")
        assert "HTML page" in str(exc.value) and "CDN or gateway" in str(exc.value)


def test_html_without_a_content_type_still_caught():
    with pytest.raises(LLMError) as exc:
        openai_text("<html>blocked</html>", base=BASE)
    assert "HTML page" in str(exc.value)


def test_empty_200_body():
    with pytest.raises(LLMError) as exc:
        openai_text("", base=BASE)
    assert "EMPTY body" in str(exc.value)


def test_non_json_200_body():
    with pytest.raises(LLMError) as exc:
        anthropic_text("upstream connect error", base=BASE, content_type="text/plain")
    assert "non-JSON body" in str(exc.value)


# ── an empty completion is a failure, not a result ────────────────────────────────────────────

def test_empty_openai_completion_raises():
    body = json.dumps({"choices": [{"message": {"content": ""}, "finish_reason": "length"}]})
    with pytest.raises(LLMError) as exc:
        openai_text(body, base=BASE)
    assert "EMPTY completion" in str(exc.value) and "length" in str(exc.value)


def test_openai_refusal_is_surfaced():
    body = json.dumps({"choices": [{"message": {"refusal": "policy: no"}}]})
    with pytest.raises(LLMError) as exc:
        openai_text(body, base=BASE)
    assert "refused" in str(exc.value)


def test_anthropic_tool_only_reply_names_the_blocks():
    body = json.dumps({"type": "message", "content": [{"type": "tool_use", "name": "x"}],
                       "stop_reason": "tool_use"})
    with pytest.raises(LLMError) as exc:
        anthropic_text(body, base=BASE)
    assert "tool_use" in str(exc.value)


def test_json_without_the_dialects_key():
    with pytest.raises(LLMError) as exc:
        openai_text(json.dumps({"detail": "not found"}), base=BASE)
    assert "no 'choices'" in str(exc.value)


# ── #1667: extra headers ──────────────────────────────────────────────────────────────────────

def test_parse_headers_line_form():
    assert parse_headers("x-session: abc\nx-route: eu") == {"x-session": "abc", "x-route": "eu"}
    assert parse_headers("x-session: abc\r\nx-route: eu") == {"x-session": "abc", "x-route": "eu"}
    # a value may itself contain a colon (a URL, a timestamp)
    assert parse_headers("x-cb: https://h:8080/x") == {"x-cb": "https://h:8080/x"}


def test_parse_headers_json_form_is_accepted():
    assert parse_headers('{"x-provider-session": "abc"}') == {"x-provider-session": "abc"}


def test_parse_headers_tolerates_garbage():
    assert parse_headers("no colon here") == {}
    assert parse_headers("{not json}") == {}
    assert parse_headers(None) == {} and parse_headers("") == {} and parse_headers(17) == {}


def test_render_round_trips_and_matches_the_cli_format():
    text = render_headers({"x-session": "abc", "x-route": "eu"})
    assert text == "x-session: abc\nx-route: eu"  # what ANTHROPIC_CUSTOM_HEADERS parses
    assert parse_headers(text) == {"x-session": "abc", "x-route": "eu"}


def test_redact_keeps_names_hides_values():
    assert redact_headers({"x-session": "supersecretvalue"}) == {"x-session": "********alue"}
