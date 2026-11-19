"""A skill the agent loads is a STEP of its turn, never the person's words.

When the agent calls the harness's Skill tool, Claude Code writes the skill's whole body into the
transcript as a `user`-role line (`isMeta: true`, opening "Base directory for this skill: …"). The
history reader treated every user line with text as speech, so the chat rendered the skill's
instructions as a big bubble in the person's voice under "Working · 1 step". The rule this product
already holds for composed turns applies: a turn nobody typed never renders as somebody's words.
"""
from __future__ import annotations

import json

from control_plane.workspace_reader import parse_history

SKILL_BODY = ("Base directory for this skill: /tmp/.claude/skills/scheduling\n\n"
              "# Scheduling\n\nWhen the person asks to book a meeting, do the following …")


def _raw(lines: list[dict]) -> str:
    return "".join(json.dumps(o) + "\n" for o in lines)


def _skill_turn(*, meta: bool = True, with_tool_use: bool = True) -> list[dict]:
    lines: list[dict] = [{"type": "user", "message": {"role": "user", "content": "book a call with the team"}}]
    if with_tool_use:
        lines += [
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_1", "name": "Skill", "input": {"skill": "scheduling"}}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": "Launching skill: scheduling"}]}},
        ]
    injected: dict = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": SKILL_BODY}]}}
    if meta:
        injected["isMeta"] = True
        injected["sourceToolUseID"] = "tu_1"
    lines += [injected,
              {"type": "assistant", "message": {"role": "assistant", "content": [
                  {"type": "text", "text": "Booked for Tuesday."}]}}]
    return lines


def test_a_loaded_skill_is_a_step_not_a_user_bubble():
    turns = parse_history(_raw(_skill_turn()), [])
    assert [t["role"] for t in turns] == ["user", "agent"]
    assert turns[0]["text"] == "book a call with the team"
    assert "Base directory for this skill" not in json.dumps(turns)
    # ONE answer: the injection does not split the agent's turn in two
    assert turns[1]["text"] == "Booked for Tuesday."
    assert turns[1]["ops"] == [{"label": "skill", "skill": "scheduling"}]


def test_the_skill_body_alone_still_names_the_step():
    """No Skill tool_use in view (and no isMeta flag): the base-directory line names the skill."""
    turns = parse_history(_raw(_skill_turn(meta=False, with_tool_use=False)), [])
    assert [t["role"] for t in turns] == ["user", "agent"]
    assert turns[1]["ops"] == [{"label": "skill", "skill": "scheduling"}]
    assert "Base directory for this skill" not in json.dumps(turns)


def test_any_meta_injection_is_never_speech():
    raw = _raw([
        {"type": "user", "message": {"role": "user", "content": "hi"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Hello"}]}},
        {"type": "user", "isMeta": True, "message": {"role": "user", "content": "<harness reminder>"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": " there."}]}},
    ])
    turns = parse_history(raw, [])
    assert [t["role"] for t in turns] == ["user", "agent"]
    assert turns[1]["text"] == "Hello there."
    assert "harness reminder" not in json.dumps(turns)


def test_a_person_quoting_the_phrase_mid_sentence_is_still_speech():
    raw = _raw([{"type": "user", "message": {"role": "user",
                                             "content": "why does it say Base directory for this skill: x?"}}])
    assert [t["role"] for t in parse_history(raw, [])] == ["user"]
