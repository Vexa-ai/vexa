"""A turn's sources as data (terminal design guidelines §4.17): every page a turn successfully
fetched is a `sources` event, so the terminal renders a citation list from structure instead of
lifting a "Sources" Markdown list out of the prose. Both runners derive it the same way."""
from __future__ import annotations

import json

from llm.claude_code import parse_stream_json
from llm.openai_agent import _panel_events
from llm.tool_events import _fetched_source


def _use(tool, args, cid="c1"):
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": tool, "input": args, "id": cid}]}})


def _result(body, cid="c1", err=False):
    return json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": cid, "is_error": err, "content": [{"type": "text", "text": body}]}]}})


def test_a_successful_fetch_is_a_source():
    evs = list(parse_stream_json(iter([_use("WebFetch", {"url": "https://example.com/a"}), _result("page text")])))
    assert {"type": "sources", "items": [{"url": "https://example.com/a"}]} in evs


def test_a_failed_fetch_is_not_a_source():
    evs = list(parse_stream_json(iter([_use("WebFetch", {"url": "https://example.com/a"}), _result("403", err=True)])))
    assert not [e for e in evs if e["type"] == "sources"]


def test_a_search_is_not_a_source():
    evs = list(parse_stream_json(iter([_use("WebSearch", {"query": "example"}), _result("[]")])))
    assert not [e for e in evs if e["type"] == "sources"]


def test_only_http_urls_are_sources():
    assert _fetched_source({"url": "javascript:alert(1)"}, "x") is None
    assert _fetched_source({"url": "file:///etc/passwd"}, "x") is None
    assert _fetched_source({}, "x") is None


def test_the_title_and_final_url_come_from_a_json_result():
    body = json.dumps({"url": "https://example.com/final", "title": "Example page", "text": "…"})
    assert _fetched_source({"url": "https://example.com/a"}, body) == {
        "type": "sources", "items": [{"url": "https://example.com/final", "title": "Example page"}]}


def test_the_openai_runner_derives_the_same_event():
    body = json.dumps({"url": "https://example.com/a", "title": "Example page", "text": "…"})
    evs = _panel_events({"name": "WebFetch", "args": {"url": "https://example.com/a"}}, True, body)
    assert evs == [{"type": "sources", "items": [{"url": "https://example.com/a", "title": "Example page"}]}]
    assert _panel_events({"name": "WebFetch", "args": {"url": "https://example.com/a"}}, False, body) == []
