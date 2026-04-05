"""Tests for agent_api.stream_parser — Claude CLI stream-json parsing."""

import pytest

from agent_api.stream_parser import parse_event


class TestTextEvents:
    def test_assistant_text_block(self):
        data = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "hello"}],
            },
        }
        events = parse_event(data)
        assert len(events) == 1
        assert events[0] == {"type": "text_delta", "text": "hello"}

    def test_multiple_text_blocks_separated(self):
        data = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "first"},
                    {"type": "text", "text": "second"},
                ],
            },
        }
        events = parse_event(data)
        assert len(events) == 2
        assert events[0]["text"] == "first"
        assert events[1]["text"] == "\n\nsecond"  # prefix for subsequent blocks

    def test_content_block_delta(self):
        data = {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "streaming..."},
        }
        events = parse_event(data)
        assert len(events) == 1
        assert events[0] == {"type": "text_delta", "text": "streaming..."}


class TestToolEvents:
    def test_tool_use_block(self):
        data = {
            "type": "assistant",
            "message": {
                "content": [{
                    "type": "tool_use",
                    "name": "bash",
                    "input": {"command": "ls -la"},
                }],
            },
        }
        events = parse_event(data)
        assert len(events) == 1
        assert events[0]["type"] == "tool_use"
        assert events[0]["tool"] == "bash"

    def test_tool_use_summary_bash(self):
        data = {
            "type": "assistant",
            "message": {
                "content": [{
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "npm test"},
                }],
            },
        }
        events = parse_event(data)
        assert "Running: npm test" in events[0]["summary"]

    def test_tool_use_summary_read(self):
        data = {
            "type": "assistant",
            "message": {
                "content": [{
                    "type": "tool_use",
                    "name": "Read",
                    "input": {"file_path": "/src/main.py"},
                }],
            },
        }
        events = parse_event(data)
        assert events[0]["summary"] == "Reading: /src/main.py"

    def test_tool_use_summary_write(self):
        data = {
            "type": "assistant",
            "message": {
                "content": [{
                    "type": "tool_use",
                    "name": "Write",
                    "input": {"file_path": "/out.txt"},
                }],
            },
        }
        events = parse_event(data)
        assert events[0]["summary"] == "Writing: /out.txt"

    def test_tool_use_summary_unknown(self):
        data = {
            "type": "assistant",
            "message": {
                "content": [{
                    "type": "tool_use",
                    "name": "CustomTool",
                    "input": {},
                }],
            },
        }
        events = parse_event(data)
        assert events[0]["summary"] == "CustomTool"


class TestResultEvents:
    def test_success_result(self):
        data = {
            "type": "result",
            "session_id": "ses-abc123",
            "cost_usd": 0.05,
            "duration_ms": 1200,
        }
        events = parse_event(data)
        assert len(events) == 1
        assert events[0]["type"] == "done"
        assert events[0]["session_id"] == "ses-abc123"
        assert events[0]["cost_usd"] == 0.05

    def test_error_result(self):
        data = {
            "type": "result",
            "is_error": True,
            "errors": ["Process killed"],
            "session_id": "ses-x",
        }
        events = parse_event(data)
        assert len(events) == 2
        assert events[0]["type"] == "error"
        assert events[0]["message"] == "Process killed"
        assert events[1]["type"] == "done"

    def test_error_subtype(self):
        data = {
            "type": "result",
            "subtype": "error_during_execution",
            "errors": [],
            "session_id": "ses-y",
        }
        events = parse_event(data)
        assert events[0]["type"] == "error"
        assert events[0]["message"] == "Agent CLI error"  # fallback message


class TestEdgeCases:
    def test_empty_content(self):
        data = {"type": "assistant", "message": {"content": []}}
        events = parse_event(data)
        assert events == []

    def test_unknown_type_returns_empty(self):
        data = {"type": "ping"}
        events = parse_event(data)
        assert events == []

    def test_missing_type_returns_empty(self):
        data = {"foo": "bar"}
        events = parse_event(data)
        assert events == []

    def test_mixed_content_blocks(self):
        data = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Let me check..."},
                    {"type": "tool_use", "name": "Grep", "input": {"pattern": "TODO"}},
                    {"type": "text", "text": "Found 3 matches."},
                ],
            },
        }
        events = parse_event(data)
        assert len(events) == 3
        assert events[0]["type"] == "text_delta"
        assert events[1]["type"] == "tool_use"
        assert events[2]["type"] == "text_delta"

    def test_content_block_delta_non_text(self):
        data = {
            "type": "content_block_delta",
            "delta": {"type": "input_json_delta", "partial_json": '{"x":'},
        }
        events = parse_event(data)
        assert events == []
