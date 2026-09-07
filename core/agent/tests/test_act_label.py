"""AN ACT IS ITS LABEL, NOT ITS PROMPT (Vexa-ai/vexa#1588).

The founder pressed Extend in the minutes panel and the chat painted the whole composed `[extend]`
preset back at him as a grey bubble in his own voice — its "Expand means EVERY direction" section
and all. An earlier Extend had shown `Extend: kg/entities/person/james-spadafora.md`.

The two halves of a turn diverge here and only here: the PROMPT is the composed preset, complete,
because that is what the agent has to read; the DISPLAY is the short label, because that is what the
person did. `human_half` cuts a composed prompt at the context sentinel — correct for a sentence
somebody typed, and on an act it leaves the entire preset, since on an act nobody typed anything.

These pin the display side: what `act_label` reads, that it never reads the preset, that the reader
serves the label for records the worker already wrote with the leak in them, and that a turn a
person actually typed is untouched.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control_plane import chat_intents  # noqa: E402
from shared.marks import MACHINERY_MARK, act_label, job_mark, read_job_mark  # noqa: E402

# What the server actually composes for an Extend — the preset body, its heading, and the section
# the founder read back at himself.
PRESET = (
    "[extend] Go further on kg/entities/person/james-spadafora.md.\n\n"
    "## Expand means EVERY direction\n"
    "Not just the next paragraph: the people, the companies, the decisions, the open threads.\n"
)
SENTINEL = "<!--vexa:user-input-below-->"


def _composed(kind: str, target: str) -> str:
    """A turn prompt exactly as the control plane builds one: grounding, sentinel, mark, preset."""
    return ("## Referencing knowledge (always)\nread the mount stack first\n\n"
            + SENTINEL + job_mark(kind, target) + PRESET)


def test_the_label_is_the_verb_and_the_page():
    assert act_label(_composed("extend", "kg/entities/person/james-spadafora.md")) \
        == "Extend: kg/entities/person/james-spadafora.md"
    assert act_label(_composed("create", "kg/plan.md")) == "Create: kg/plan.md"


def test_the_label_carries_none_of_the_preset():
    label = act_label(_composed("extend", "kg/entities/person/james-spadafora.md"))
    assert "Expand means EVERY direction" not in label
    assert "[extend]" not in label
    assert "\n" not in label


def test_a_workspace_qualified_page_keeps_its_workspace():
    """`job_target` names the page the same way twice — the refusal of a second job keys on it, so
    the same path in two workspaces must not collide. The label says which one."""
    target = chat_intents.job_target({"kind": "extend", "workspace": "175", "path": "kg/plan.md"})
    assert act_label(_composed("extend", target)) == "Extend: 175/kg/plan.md"


def test_a_turn_somebody_typed_is_not_an_act():
    assert act_label("what did we decide about the CLA?") is None
    assert act_label("## grounding\n\n" + SENTINEL + "send the bot to my 3pm") is None
    assert act_label("") is None
    assert act_label(MACHINERY_MARK + " a composed opening") is None


def test_the_prompt_is_untouched_by_the_display_decision():
    """The agent still gets every word. `read_job_mark` strips the mark and leaves the preset —
    the two functions read the same mark and answer different questions."""
    prompt = _composed("extend", "kg/plan.md")
    kind, target, brief = read_job_mark(prompt)
    assert (kind, target) == ("extend", "kg/plan.md")
    assert "Expand means EVERY direction" in brief
    assert PRESET.strip() in brief


def test_every_job_kind_has_a_label():
    """The closed set and the verbs must not drift apart: a kind with no verb would render its own
    machine name at somebody."""
    for kind in chat_intents.JOB_KINDS:
        label = act_label(_composed(kind, "kg/plan.md"))
        assert label and label.endswith(": kg/plan.md")
        assert label[0].isupper()


def test_the_worker_records_the_label_and_not_the_preset():
    """The worker's own expression, pinned: an act records its label, a sentence records itself."""
    from worker import engine

    act = _composed("extend", "kg/plan.md")
    typed = "## grounding\n\n" + engine.CONTEXT_SENTINEL + "what did we decide?"
    assert (engine.act_label(act) or engine.human_half(act)) == "Extend: kg/plan.md"
    assert (engine.act_label(typed) or engine.human_half(typed)) == "what did we decide?"
    assert engine.CONTEXT_SENTINEL == SENTINEL


def _thread(root: Path, subject: str, session: str = "main", sid: str = "sid-1") -> Path:
    ws = root / subject
    (ws / ".claude" / "sessions").mkdir(parents=True)
    (ws / ".claude" / "sessions" / f"{session}.session").write_text(sid + "\n")
    return ws


def _transcript(ws: Path, sid: str, lines: list) -> None:
    import json
    proj = ws / ".claude" / "projects" / "-some-cwd-slug"
    proj.mkdir(parents=True, exist_ok=True)
    proj.joinpath(f"{sid}.jsonl").write_text("".join(json.dumps(o) + "\n" for o in lines))


def test_the_history_reader_serves_the_label_over_a_record_already_written(tmp_path):
    """THE REPAIR, end to end. The founder's Extend recorded the composed preset as his own words —
    that record is in his own transcript and is not ours to rewrite. The mark is in the STORED
    PROMPT, so the reader answers correctly on every read; this also covers a worker one release
    behind a terminal that already sends intents."""
    from control_plane.workspace_reader import WorkspaceReader
    from worker import engine

    ws = _thread(tmp_path, "u_dmitry")
    composed = _composed("extend", "kg/entities/person/james-spadafora.md")
    _transcript(ws, "sid-1", [
        {"type": "user", "message": {"role": "user", "content": composed}},
        {"type": "assistant", "message": {"role": "assistant",
                                          "content": [{"type": "text", "text": "Added three threads."}]}},
    ])
    # exactly what the old worker wrote: everything after the sentinel, i.e. the whole preset
    engine.record_user_text(ws, "main", composed, engine.human_half(composed))

    turns = WorkspaceReader(str(tmp_path)).history("u_dmitry", "main")
    assert turns[0]["user_text"] == "Extend: kg/entities/person/james-spadafora.md"
    assert "Expand means EVERY direction" not in turns[0]["user_text"]
    # `text` stays the stored prompt — the terminal's shape filters read the RECORD, not the label
    assert turns[0]["text"] == composed
    assert turns[1]["role"] == "agent"          # the answer is still shown; only the ask was machinery
