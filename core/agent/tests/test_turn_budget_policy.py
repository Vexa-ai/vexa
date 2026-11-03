"""The chat turn's tool-call budget is a `_global/POLICIES.md` key (Vexa-ai/vexa#1783, turn-tool-budget).

`chat_turn_max_tool_calls` — default 40, today's number. The administrator sets it; a workspace (its
own `POLICIES.md`) or a person (their desk's `POLICIES.md`) may only NARROW it, never raise it above
`_global`. Dispatch stamps the effective value as `VEXA_AGENT_MAX_TOOL_CALLS_CHAT`.
"""
from __future__ import annotations

from pathlib import Path

from control_plane import turn_budget as TB


def _policies(dir_: Path, body: str) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "POLICIES.md").write_text(f"---\nkind: policies\n{body}\n---\n")
    return dir_


def test_default_is_todays_forty_and_unset_means_nothing_to_stamp(tmp_path):
    assert TB.DEFAULT == 40
    assert TB.effective(tmp_path / "nope") is None                 # no file: today's behaviour
    _policies(tmp_path / "g", "open_web: on")
    assert TB.effective(tmp_path / "g") is None                    # key absent: today's behaviour


def test_the_global_key_sets_the_budget(tmp_path):
    g = _policies(tmp_path / "g", "chat_turn_max_tool_calls: 25")
    assert TB.effective(g) == 25


def test_a_workspace_or_person_may_only_narrow(tmp_path):
    g = _policies(tmp_path / "g", "chat_turn_max_tool_calls: 30")
    ws = _policies(tmp_path / "ws", "chat_turn_max_tool_calls: 12")
    me = _policies(tmp_path / "me", "chat_turn_max_tool_calls: 500")   # tries to raise: clipped
    assert TB.effective(g, [ws, me]) == 12
    assert TB.effective(g, [me]) == 30


def test_narrowing_with_no_global_key_narrows_the_default(tmp_path):
    ws = _policies(tmp_path / "ws", "chat_turn_max_tool_calls: 10")
    assert TB.effective(tmp_path / "missing", [ws]) == 10
    me = _policies(tmp_path / "me", "chat_turn_max_tool_calls: 90")
    assert TB.effective(tmp_path / "missing", [me]) is None         # cannot raise above 40


def test_an_unreadable_value_is_the_conservative_default(tmp_path):
    g = _policies(tmp_path / "g", "chat_turn_max_tool_calls: lots")
    assert TB.effective(g) == TB.DEFAULT
    g2 = _policies(tmp_path / "g2", f"chat_turn_max_tool_calls: {TB.CEILING + 1}")
    assert TB.effective(g2) == TB.DEFAULT
    ws = _policies(tmp_path / "ws", "chat_turn_max_tool_calls: -3")
    assert TB.effective(_policies(tmp_path / "g3", "chat_turn_max_tool_calls: 20"), [ws]) == 20


def test_the_seeded_policies_carry_the_key_at_its_default():
    seed = Path(__file__).resolve().parents[3] / "behavior" / "global" / "POLICIES.md"
    assert "\nchat_turn_max_tool_calls: 40\n" in seed.read_text()


def test_dispatch_stamps_the_effective_chat_budget(tmp_path):
    from control_plane import dispatch
    g = _policies(tmp_path / "_global", "chat_turn_max_tool_calls: 25")
    desk = _policies(tmp_path / "7", "chat_turn_max_tool_calls: 15")
    env: dict = {}
    dispatch.stamp_turn_budget(env, global_dir=str(g),
                               mounts=[{"slug": "7", "path": str(desk), "primary": True,
                                        "role": "private", "write": True}], target="")
    assert env["VEXA_AGENT_MAX_TOOL_CALLS_CHAT"] == "15"
    env = {}
    dispatch.stamp_turn_budget(env, global_dir=str(tmp_path / "none"), mounts=[], target="")
    assert "VEXA_AGENT_MAX_TOOL_CALLS_CHAT" not in env
