"""THE AUTHORING ASK, AGAINST THE VOCABULARY IT COMPOSES FROM (Vexa-ai/vexa#1639).

`behavior/asks/flow-author.md` is the conversation that turns an administrator's sentence into a
flow. It carries three worked sentences, each with the trigger and the ordered step list that
sentence maps to — and the whole value of the file is that those are REAL: a step name that is not
in this image's vocabulary is refused at submission with the vocabulary attached, and a trigger
nothing emits files a flow that never runs.

THE ASK LIVES IN `behavior/`, WHICH BOTH SIDES CAN READ, AND THE VOCABULARY LIVES HERE. So this half
of its test suite is here, where `flows_defs.production` composes the registry the ask has to agree
with; the half about the ask's own shape — the confirmation, the proposal page, the send, the
authorization record — is `core/agent/tests/test_flow_author.py`, where the preset library and the
intent map are. `tests/test_flow_pages.py` splits the same way and for the same reason.

Four claims:

  A1  The three worked sentences parse, and are the three this test names. The mapping is written in
      the file and restated by hand here — a test that derived both from the same place would be the
      ask agreeing with itself.
  A2  EVERY STEP NAME IS IN THE VOCABULARY. This is the claim the whole file exists for. A worked
      example naming a step the image does not carry teaches the agent to compose a flow that
      `flows_submit` refuses, and the administrator meets the refusal instead of the flow.
  A3  Every trigger — in the worked examples AND in the ask's own "they say → trigger" table — is an
      event some flow in this image reacts to. A flow filed on an event nothing publishes is admitted
      and then silent, which looks exactly like a flow that works.
  A4  The step order the examples give is one the steps' own docstrings allow: a step that says
      "cannot run before X" is never put before X.
  A5  The generated flows index POINTS AT the ask. An ask runs when an act posts its intent, and
      the founder's own path is that he says a sentence in the chat — so the pointer has to be in
      the tier every worker mounts, or the agent has to already know the file exists.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import flows_pages

REPO = Path(flows_pages.__file__).resolve().parents[3]
ASK = REPO / "behavior" / "asks" / "flow-author.md"


@pytest.fixture(scope="module")
def ask() -> str:
    if not ASK.is_file():
        pytest.fail(f"{ASK} does not exist — the authoring ask is the deliverable")
    return ASK.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def reg():
    return flows_pages.build_registry()


def _worked(body: str) -> dict:
    """`{heading: (the sentence, trigger, (steps…))}` — parsed out of the ask's own body.

    Same shape `test_policies_wizard.py::_worked_shapes` reads out of the wizard: the file IS the
    mapping and this reads what it says. A section whose fenced block is missing simply does not
    appear, and the test below asserts the set."""
    out: dict = {}
    parts = re.split(r"^#### (.+)$", body, flags=re.M)
    for i in range(1, len(parts) - 1, 2):
        heading, section = parts[i].strip(), parts[i + 1]
        sentence = re.search(r"^\*([^*].+?)\*$", section, flags=re.M | re.S)
        block = re.search(r"```yaml\n(.*?)```", section, flags=re.S)
        if not (sentence and block):
            continue
        on = re.search(r"^on:\s*(\S+)", block.group(1), flags=re.M)
        steps = tuple(m.group(1) for m in
                      re.finditer(r"^\s*-\s*(\S+)\s*$", block.group(1), flags=re.M))
        if on:
            out[heading] = (" ".join(sentence.group(1).split()), on.group(1), steps)
    return out


#: THE SPEC, WRITTEN OUT — the three sentences and what each must map to. By hand here, parsed from
#: the file there, compared below.
SENTENCES: dict = {
    "Write the report and get it to the room": (
        "when a meeting ends, write the report, mail it to everybody who was in the room, and put "
        "it on their desks",
        "meeting.completed",
        ("process_meeting", "email_minutes", "email_attendees", "drop_to_attendees"),
    ),
    "Take the invite, but do not write back": (
        "when somebody invites the mailbox to a call, accept it in their calendar and send the bot "
        "— but do not mail them a confirmation",
        "invite.received",
        ("ensure_user", "rsvp_accept", "emit_prep", "await_start", "dispatch_bot", "emit_started",
         "run_meeting", "emit_completed"),
    ),
    "Ask before the meeting, not after": (
        "when a meeting is coming up, ask the organiser whether they want to walk in ready",
        "meeting.upcoming",
        ("prepare_meeting",),
    ),
}


# ── A1 · the three sentences ────────────────────────────────────────────────────────────────────

def test_the_ask_works_three_sentences_and_they_are_these(ask):
    assert _worked(ask) == SENTENCES


def test_a_sentence_is_a_sentence_somebody_would_say(ask):
    """Not a specification with a verb bolted on. Each begins with `when`, because a flow's first
    fact is its trigger and that is what the administrator says first."""
    for _heading, (sentence, _on, _steps) in _worked(ask).items():
        assert sentence.lower().startswith("when "), sentence


# ── A2 · every step exists here ─────────────────────────────────────────────────────────────────

def test_every_step_in_every_worked_example_is_in_this_image(ask, reg):
    """THE CLAIM THIS FILE EXISTS FOR. `flows_submit` validates step names against the deployed
    vocabulary AT SUBMISSION and answers 400 with the whole list — so an example naming a step this
    image does not carry teaches the agent to compose a flow the administrator can never activate,
    and the refusal is what they meet instead of the flow."""
    unknown = {name: [s for s in steps if s not in reg.steps]
               for name, (_sentence, _on, steps) in _worked(ask).items()}
    unknown = {k: v for k, v in unknown.items() if v}
    assert unknown == {}, (f"the ask names steps this image does not carry: {unknown} — known: "
                           f"{sorted(reg.steps)}")


def test_no_worked_example_is_empty(ask):
    for name, (_sentence, _on, steps) in _worked(ask).items():
        assert steps, f"{name} maps to no steps"


# ── A3 · every trigger is a fact something reacts to ────────────────────────────────────────────

def test_every_worked_trigger_is_an_event_this_image_reacts_to(ask, reg):
    reactable = {f.on.name for f in reg.flows.values()}
    bad = {name: on for name, (_s, on, _steps) in _worked(ask).items() if on not in reactable}
    assert bad == {}, f"triggers nothing publishes: {bad} — reactable: {sorted(reactable)}"


def test_the_triggers_table_names_only_events_this_image_reacts_to(ask, reg):
    """The ask's own "they say → trigger" table is what the agent reads a sentence through. A row
    naming an event nothing emits sends the administrator's flow into silence: admitted, never
    matched, indistinguishable from one that works."""
    reactable = {f.on.name for f in reg.flows.values()}
    table = {m.group(1) for m in re.finditer(r"^\|[^|]+\|\s*`([a-z_]+\.[a-z_.]+)`\s*\|\s*$",
                                             ask, flags=re.M)}
    assert table, "the trigger table has no rows — the mapping the ask reads a sentence through"
    assert table <= reactable, f"rows nothing reacts to: {sorted(table - reactable)}"


# ── A4 · the order the steps themselves require ─────────────────────────────────────────────────

# ── A5 · the index points at the ask ────────────────────────────────────────────────────────────

def test_the_flows_index_tells_the_agent_where_the_ask_is(reg):
    """`_global/flows/README.md` is mounted read-only into every worker. It is the page about flows,
    so it is where an agent in the governance chat finds out that authoring is a conversation — and
    that answering *"I don't have the instruction"* is never the move."""
    index = flows_pages.all_pages(reg)["README.md"]
    assert "../asks/flow-author.md" in index
    assert "Never answer that you have no instruction" in " ".join(index.split())
    assert "<flow>@<version>.md" in index
    assert f"`{flows_pages.PROPOSALS_DIRNAME}/`" in index


def test_a_step_that_cannot_run_before_another_is_never_put_before_it(ask, reg):
    """`email_minutes` and `email_attendees` both say *"cannot run before the note"* in their own
    docstrings: their input is `process_meeting`'s artefact. An example that ordered them the other
    way would be a flow that fails on its second step, taught as the shape to copy."""
    import inspect
    for name, (_sentence, _on, steps) in _worked(ask).items():
        for i, step in enumerate(steps):
            doc = " ".join((inspect.getdoc(reg.steps[step]) or "").split())
            if "cannot run before the note" not in doc.lower():
                continue
            assert "process_meeting" in steps[:i], (
                f"{name}: {step} is placed before the step that produces what it reads")
