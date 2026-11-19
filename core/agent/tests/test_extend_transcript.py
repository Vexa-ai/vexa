"""EXTEND ON A TRANSCRIPT SELECTION, server half (Vexa-ai/vexa#1596).

Founder, 2026-09-06, in a live meeting with the transcript canvas open: *"we also want extend on
transcript when i can select some text and push the button"*.

The client half is the same control the pages panel wears; what this file pins is what the SERVER
does with the press, and all of it is the same rule the other intents keep: the wire carries a kind
and its arguments, the words live in `_global/asks/`, and nothing composed by a client reaches the
agent as instructions.

Four things have to hold, and each has already failed somewhere in this codebase in another form:

  1. the kind runs a preset that EXISTS, or the turn degrades to the client's plainer sentence;
  2. it is a JOB — a 30-120 s act that holds the composer is the defect Vexa-ai/vexa#1584 removed,
     and this act reads a room and writes several pages;
  3. its TARGET names the meeting and the passage, with `]` taken out — the mark's own terminator
     inside a person's words would close it early and spill the rest into the prompt;
  4. the person reads **Extend**, because Extend is the button they pressed. `extend_transcript` is
     a routing detail, and a label that leaks it is the same failure as the one that painted a whole
     preset back at the founder as his own words (Vexa-ai/vexa#1588).
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from control_plane import chat_intents, scaffolds  # noqa: E402
from shared.marks import act_label, read_job_mark  # noqa: E402
from worker import jobs  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]
BEHAVIOR_ASKS = REPO / "behavior" / "asks"

MEETING = "41"
PASSAGE = "their pilot ships in March, self-hosted"
INTENT = {
    "kind": "extend_transcript", "meeting": MEETING, "selection": PASSAGE,
    "segment": "s2", "speaker": "Ravi", "at": "2026-09-06T11:52:09.000Z",
}


# ── 1 · the preset ───────────────────────────────────────────────────────────────────────────────

def test_the_kind_runs_its_own_preset_and_that_preset_ships():
    assert chat_intents.preset_for(INTENT) == "extend-transcript"
    assert (BEHAVIOR_ASKS / "extend-transcript.md").is_file()


def test_the_preset_is_an_extend_act_and_says_the_transcript_is_never_rewritten(tmp_path):
    """The founder asked for Extend, so the act the agent reads is `[extend]` — and the one thing it
    must never do is edit the record it was pressed on (#1595 is the same rule, one layer down)."""
    _fm, body = scaffolds.read_preset(tmp_path, "extend-transcript", image_root=BEHAVIOR_ASKS)
    assert body.lstrip().startswith("[extend]")
    assert "NEVER REWRITE THE TRANSCRIPT" in body


def test_every_token_the_preset_asks_for_is_one_the_intent_supplies(tmp_path):
    """A `{{token}}` `tokens_for` does not know is left STANDING by `substitute` — visible to an
    admin as their own typo, and to the person as braces in their chat. So the two lists are pinned
    together here rather than trusted to stay in step."""
    _fm, body = scaffolds.read_preset(tmp_path, "extend-transcript", image_root=BEHAVIOR_ASKS)
    out = scaffolds.substitute(body, chat_intents.tokens_for(INTENT))
    assert "{{" not in out
    assert PASSAGE in out and MEETING in out and "Ravi" in out and "s2" in out


def test_where_it_was_said_renders_as_nothing_when_it_could_not_be_established(tmp_path):
    """The client omits a speaker it could not locate exactly (F63). The preset must then say
    nothing about one — never the literal `None`, which the founder would read as a name."""
    _fm, body = scaffolds.read_preset(tmp_path, "extend-transcript", image_root=BEHAVIOR_ASKS)
    out = scaffolds.substitute(body, chat_intents.tokens_for(
        {"kind": "extend_transcript", "meeting": MEETING, "selection": PASSAGE}))
    assert "None" not in out and "{{" not in out


def test_the_persons_own_line_reaches_the_agent_attributed(tmp_path):
    """#1593's line is the same field on the same control, so it must survive the same way here:
    placed by the preset's own token, and appended attributed when a deployment's preset predates
    it — `with_instruction` decides which, off the RAW ask. A line typed and dropped is the silent
    failure, because nobody downstream can tell it was ever there."""
    line = "check whether that date is public anywhere"
    said = {**INTENT, "instruction": line}
    _fm, ask = scaffolds.read_preset(tmp_path, "extend-transcript", image_root=BEHAVIOR_ASKS)
    out = chat_intents.with_instruction(
        scaffolds.substitute(ask, chat_intents.tokens_for(said)), ask, said)
    assert line in out
    # this preset carries the token, so the line is placed, not appended twice
    assert out.count(line) == 1
    assert chat_intents.INSTRUCTION_TOKEN in ask

    # and a preset that does NOT ask for it still gets the line, attributed
    older = "[extend] an older preset that predates the field"
    assert chat_intents.INSTRUCTION_LEAD in chat_intents.with_instruction(older, older, said)


# ── 2 · it is a job, and it is not silent ────────────────────────────────────────────────────────

def test_the_act_runs_as_a_job_so_the_chat_stays_answerable():
    """It reads the room, researches, and writes pages — the exact 30-120 s shape #1584 took out of
    the turn loop. Read off the KIND, never off a wire flag."""
    assert "extend_transcript" in chat_intents.JOB_KINDS
    assert chat_intents.is_job(INTENT) is True
    assert chat_intents.job_prefix(INTENT).startswith("[vexa-job:extend_transcript:")


def test_the_person_sees_the_act_it_is_not_machinery():
    """Highlight is silent because nobody asked it a question. This one they pressed on words they
    chose, and it answers with a line about what it wrote."""
    assert chat_intents.is_silent(INTENT) is False
    assert "extend_transcript" not in chat_intents.SILENT_KINDS


# ── 3 · the target names the meeting and the passage ─────────────────────────────────────────────

def test_the_target_is_the_room_and_the_words_not_a_path():
    target = chat_intents.job_target(INTENT)
    assert target.startswith(f"meeting {MEETING}")
    assert PASSAGE in target


def test_two_passages_of_one_meeting_are_two_targets_and_the_same_one_twice_is_one():
    """`JobRunner.spawn` refuses a second job on the SAME target. Keyed on the meeting alone, a
    second Extend anywhere in a long meeting would be refused for two minutes; keyed on the passage,
    the double-press it exists to catch still is."""
    other = {**INTENT, "selection": "and the budget sits with procurement"}
    assert chat_intents.job_target(INTENT) != chat_intents.job_target(other)
    assert chat_intents.job_target(INTENT) == chat_intents.job_target({**INTENT, "segment": "s9"})


def test_a_bracket_in_the_passage_cannot_close_the_mark():
    """`_JOB_RE` reads `[^\\]]{0,512}`: a `]` inside the target ENDS the mark, and everything after
    it — a person's own words — would arrive as prose in the prompt with the machinery stripped off.
    The one place a person's words enter a mark is the one place they are cleaned."""
    said = {**INTENT, "selection": "the array [0] is empty] and then we stopped"}
    mark = chat_intents.job_prefix(said)
    kind, target, rest = read_job_mark(mark + "the composed preset")
    assert kind == "extend_transcript"
    assert "]" not in target
    assert rest == "the composed preset"


def test_a_long_passage_is_capped_rather_than_carried_whole():
    """A target is read in one chat line and lives inside a 512-character mark; a highlighted
    paragraph is neither. It is cut, and the cut says so."""
    said = {**INTENT, "selection": "w" * 600}
    target = chat_intents.job_target(said)
    assert len(target) < 200
    assert target.endswith("…”")


def test_a_passage_is_one_line_in_the_target():
    said = {**INTENT, "selection": "we agreed\n\nthen they   left"}
    assert "\n" not in chat_intents.job_target(said)


# ── 4 · the person reads "Extend" ────────────────────────────────────────────────────────────────

SENTINEL = "<!--vexa:user-input-below-->"


def test_the_label_is_extend_and_names_the_room_never_the_kind():
    composed = "grounding\n" + SENTINEL + chat_intents.job_prefix(INTENT) + "[extend] the whole preset"
    label = act_label(composed)
    assert label.startswith("Extend: ")
    assert "extend_transcript" not in label
    assert f"meeting {MEETING}" in label and "the whole preset" not in label


def test_the_job_lines_read_as_the_act_the_person_pressed():
    target = chat_intents.job_target(INTENT)
    assert jobs.started_line("extend_transcript", target).startswith("Extending meeting 41")
    assert jobs.done_line("extend_transcript", target).endswith("— extended.")
    assert "Working on" not in jobs.started_line("extend_transcript", target)
