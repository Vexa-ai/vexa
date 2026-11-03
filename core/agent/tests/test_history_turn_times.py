"""Each history turn says WHEN it was said (`at`, epoch seconds), so a shared chat can draw the
lines people typed to each other between the agent's turns, in time order (#1783)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from control_plane.workspace_reader import parse_history


def _raw(lines: list[dict]) -> str:
    return "".join(json.dumps(o) + "\n" for o in lines)


def _ts(h: int, m: int) -> str:
    return datetime(2026, 10, 14, h, m, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def test_turns_carry_the_time_they_were_said() -> None:
    raw = _raw([
        {"type": "user", "timestamp": _ts(15, 40), "message": {"role": "user", "content": "send a bot at 16:00"}},
        {"type": "assistant", "timestamp": _ts(15, 41), "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]}},
        {"type": "assistant", "timestamp": _ts(15, 42), "message": {"role": "assistant", "content": [{"type": "text", "text": " More."}]}},
    ])
    turns = parse_history(raw, [])
    assert [t["role"] for t in turns] == ["user", "agent"]
    assert turns[0]["at"] == datetime(2026, 10, 14, 15, 40, tzinfo=timezone.utc).timestamp()
    assert turns[1]["at"] == datetime(2026, 10, 14, 15, 41, tzinfo=timezone.utc).timestamp()


def test_a_line_without_a_time_has_no_at() -> None:
    raw = _raw([{"type": "user", "message": {"role": "user", "content": "hi"}},
                {"type": "user", "timestamp": "not a time", "message": {"role": "user", "content": "again"}}])
    assert all("at" not in t for t in parse_history(raw, []))
