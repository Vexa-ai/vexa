"""THE POLICIES WIZARD — five questions, one recommendation, one decision record (Vexa-ai/vexa#1627).

Founder, 2026-09-06: *"this is essentially a part of the onboarding process that helps decide on the
policy to start with, which is a tradeoff between adoption and security, but with specific risks
that we can assess and define … and have predefined rationale about that initial policy … to help
them decide."*

`_global/POLICIES.md` already carried the rules, their defaults and three lenses on each. What was
missing was the conversation that turns those into ONE deployment's answer: an assessment of the
administrator's own risks, a recommendation with the reasoning attached, and a record of what was
decided. That conversation is a FILE — `behavior/asks/policies-wizard.md` — for the same reason the
rules are: it is admin-owned, seeded additively (`preset_library.top_up`), and read hot.

Five claims, in the order they would fail:

  W1  The wizard is a preset this library can actually serve, and the intent behind the page's
      **Set up policies** act resolves to it. An act whose ask is not there degrades to a sentence.
  W2  It asks FIVE questions, one at a time, and each names both the rules it answers and the RISK
      it assesses. A question with no risk beside it is a preference, not an assessment.
  W3  The mapping: each answer set produces exactly one block, and every key in every block is a
      rule the seeded `POLICIES.md` declares. A block that writes a key nothing reads is a control
      that silently does nothing — the failure `POLICIES.md`'s own table exists to prevent.
  W4  The decision is APPENDED under `## Decision` and an older one is NEVER rewritten. The record
      answers *why we started here*; edited to agree with today it answers nothing.
  W5  The setup ask calls the wizard BY NAME instead of restating it — one conversation, one place
      it is written.

The reasoning itself is deliberately NOT pinned here: the wizard is instructed to lift it out of
`POLICIES.md`'s own section for each rule, so a test that restated the lenses would be the third
copy of them and the one nobody updates.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from control_plane import chat_intents, scaffolds
from shared import marks

REPO = pathlib.Path(__file__).resolve().parents[3]
BEHAVIOR_ASKS = REPO / "behavior" / "asks"
POLICIES_MD = REPO / "behavior" / "global" / "POLICIES.md"
WIZARD = "policies-wizard"


def _ask(name: str) -> str:
    return (BEHAVIOR_ASKS / f"{name}.md").read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """The text with its line wrapping removed — a sentence pinned across a wrap is still that
    sentence, and re-flowing a paragraph must not fail a test about what it says."""
    return " ".join((text or "").split())


# ── the four worked shapes, read out of the wizard's own body ────────────────────────────────────

def _worked_shapes(body: str) -> dict[str, tuple[str, tuple[str, ...]]]:
    """`{heading: (the five answers as one line, the block's rows in order)}`.

    Parsed rather than restated: the file is the mapping, and this reads what it says. A shape whose
    fenced block is missing simply does not appear, and the tests below assert the set."""
    out: dict[str, tuple[str, tuple[str, ...]]] = {}
    parts = re.split(r"^#### (.+)$", body, flags=re.M)
    for i in range(1, len(parts) - 1, 2):
        heading, section = parts[i].strip(), parts[i + 1]
        answers = re.search(r"^\*(.+?)\*$", section, flags=re.M)
        block = re.search(r"```yaml\n(.*?)```", section, flags=re.S)
        if answers and block:
            rows = tuple(ln.strip() for ln in block.group(1).strip().splitlines() if ln.strip())
            out[heading] = (answers.group(1).strip(), rows)
    return out


#: THE SPEC, WRITTEN OUT. Each answer set and the block the wizard must produce for it — by hand
#: here, parsed from the file there, compared below. A test that derived both from the same place
#: would be the mapping agreeing with itself.
SHAPES: dict[str, tuple[str, tuple[str, ...]]] = {
    "Only our own people": (
        "only our own people · our people's inboxes · the transcript is enough · "
        "anyone who invites it · open web yes",
        ("profile: default",
         "external_participants: off",
         "bot_joins_mixed_meetings: off",
         "report_to_participants: on"),
    ),
    "Partners in the room, and the words stay here": (
        "partners and clients too · on this instance · the transcript is enough · "
        "the organizer confirms each join · open web no",
        ("profile: bank",
         "attendee_domains: <the domains that count as inside>",
         "report_to_participants: off",
         "organizer_confirms_join: on",
         "open_web: off"),
    ),
    "Partners in the room, and the mail reaches them": (
        "partners and clients too · partners' inboxes too · we need the recording · "
        "anyone who invites it · open web yes",
        ("profile: default",
         "attendee_domains: <the domains that count as inside>",
         "report_to_participants: on",
         "external_participants: on",
         "recording_retention_days: forever"),
    ),
    "Sometimes the public": (
        "sometimes the public · our people's inboxes · the transcript is enough · "
        "the organizer confirms each join · open web yes",
        ("profile: default",
         "attendee_domains: <the domains that count as inside>",
         "report_to_participants: on",
         "external_participants: off",
         "organizer_confirms_join: on"),
    ),
}


# ── W1 · it is a preset, and the act resolves to it ──────────────────────────────────────────────

def test_the_wizard_is_a_preset_this_library_can_serve(tmp_path):
    """`_global/asks/` is admin-owned and empty here, so this exercises the IMAGE fallback — the
    path that actually holds on a read-only `_global` (`preset_library`'s own second half)."""
    fm, body = scaffolds.read_preset(tmp_path, WIZARD, image_root=BEHAVIOR_ASKS)
    assert fm["label"] == "policies"
    assert body.lstrip().startswith(f"[{WIZARD}]")
    # the file it walks is in front of them while they answer
    assert "_global/POLICIES.md" in (fm.get("tabs") or "")


def test_the_set_up_policies_act_runs_the_wizard_and_nothing_else():
    intent = {"kind": "policies_wizard", "workspace": "_global", "path": "POLICIES.md"}
    assert chat_intents.presets_for(intent) == [WIZARD]
    assert chat_intents.preset_for(intent) == WIZARD


def test_the_act_neither_holds_the_chat_nor_hides_itself():
    """A wizard is a CONVERSATION: there is no background job to watch (`JOB_KINDS`), and the person
    pressed a labelled control, so the turn must read as that label rather than vanish
    (`SILENT_KINDS`). Both are closed sets and neither is a flag the wire may set."""
    intent = {"kind": "policies_wizard", "workspace": "_global", "path": "POLICIES.md"}
    assert not chat_intents.is_job(intent)
    assert not chat_intents.is_silent(intent)
    assert chat_intents.job_prefix(intent) == ""
    assert chat_intents.act_prefix(intent).startswith(marks.ACT_MARK)


def test_the_turn_reads_back_as_the_button_that_was_pressed():
    """#1605's rule, on the newest act: a turn nobody typed never renders as their words, and the
    label a reload rebuilds comes out of the mark rather than out of the preset's prose."""
    intent = {"kind": "policies_wizard", "workspace": "_global", "path": "POLICIES.md"}
    prompt = chat_intents.act_prefix(intent) + "…the whole ask…"
    assert marks.act_label(prompt) == "Set up policies: _global/POLICIES.md"


# ── W2 · five questions, one at a time, each naming its risk ─────────────────────────────────────

#: The five, in the order the issue names them: the heading, the rules it answers, and the word the
#: risk line has to contain. The RISK is the half that makes this an assessment rather than a menu.
QUESTIONS = (
    ("1 · Who is in your meetings?",
     ("external_participants", "bot_joins_mixed_meetings", "attendee_domains"), "consent law"),
    ("2 · Where must the words stay?",
     ("report_to_participants", "external_participants", "data_statement"), "exfiltration"),
    ("3 · Do you need to re-listen, or is the transcript enough?",
     ("recording_retention_days", "transcript_retention_days"), "subpoena"),
    ("4 · Who decides when the bot joins?",
     ("bot_joins_on_invite", "organizer_confirms_join"), "hostile invite"),
    ("5 · May the agents reach the open web from inside your perimeter?",
     ("open_web",), "SSRF"),
)


@pytest.mark.parametrize("heading,rules,risk", QUESTIONS)
def test_each_question_names_the_rules_it_answers_and_the_risk_it_assesses(heading, rules, risk):
    body = _ask(WIZARD)
    assert f"### {heading}" in body, f"the wizard does not ask {heading!r}"
    section = _flat(body.split(f"### {heading}", 1)[1].split("\n### ", 1)[0])
    for rule in rules:
        assert f"`{rule}`" in section, f"{heading}: does not say it answers `{rule}`"
    assert "**The risk:**" in section, f"{heading}: names no risk, so it is a preference not an assessment"
    assert risk in section, f"{heading}: the risk line does not mention {risk!r}"


def test_the_questions_are_asked_in_order_and_one_at_a_time():
    body = _ask(WIZARD)
    at = [body.index(f"### {h}") for h, _, _ in QUESTIONS]
    assert at == sorted(at), "the five questions are not in the order the issue names them"
    assert "ONE AT A TIME" in body
    assert "Never two in one turn." in body


def test_it_reads_the_file_before_it_asks_and_never_composes_a_reason():
    """The founder's own diagnosis of the improvisation this replaces: *"she does not have the
    context we decided on here"*. Every reason the wizard gives is lifted out of `POLICIES.md`'s
    section for that rule — so the argument a person weighs is the one the deployment wrote down,
    not the one a model produced in the moment."""
    body = _flat(_ask(WIZARD))
    assert "Read `/workspaces/_global/POLICIES.md` in full, silently." in body
    assert "Never restate a rationale from memory and never compose one." in body
    assert "the three lenses, lifted from that rule's own section in `POLICIES.md`" in body
    # and a rule the engine does not enforce is said to be exactly that
    assert "intended, not yet enforced" in body


def test_the_recommendation_is_ONE_message_carrying_the_block_and_the_derived_sentence():
    body = _flat(_ask(WIZARD))
    assert "## The recommendation — ONE message" in _ask(WIZARD)
    assert "**The block**, as a fenced snippet of the front matter you would write" in body
    assert "**The derived attendee sentence**" in body
    assert "It is DERIVED from the rules, never written" in body
    assert "is this the policy to start with?" in body


# ── W3 · the mapping ─────────────────────────────────────────────────────────────────────────────

def test_the_wizard_works_exactly_the_four_shapes():
    got = _worked_shapes(_ask(WIZARD))
    assert set(got) == set(SHAPES), "the worked shapes are not the four the mapping produces"


@pytest.mark.parametrize("heading", list(SHAPES))
def test_each_answer_set_produces_exactly_its_block(heading):
    """THE MAPPING TEST. Five answers in, one block out — and the block is the whole answer: a row
    the answers did not produce is a rule nobody chose, and a missing row is a rule the
    recommendation forgot to say out loud."""
    answers, rows = _worked_shapes(_ask(WIZARD))[heading]
    assert answers == SHAPES[heading][0], f"{heading}: the answers this block claims are not its own"
    assert rows == SHAPES[heading][1], f"{heading}: the block is not what those answers produce"


def test_every_key_any_block_writes_is_a_rule_the_policy_page_declares():
    """A block that writes a key `POLICIES.md` does not carry is a control that silently does
    nothing, and one that misses a key is a rule the admin cannot answer. The page is the schema."""
    declared = {ln.split(":", 1)[0].strip() for ln in
                POLICIES_MD.read_text(encoding="utf-8").split("---")[1].strip().splitlines()
                if ":" in ln}
    for heading, (_answers, rows) in _worked_shapes(_ask(WIZARD)).items():
        for row in rows:
            key = row.split(":", 1)[0].strip()
            assert key in declared, f"{heading}: `{key}` is not a row of POLICIES.md's front matter"


def test_the_mapping_table_names_the_answer_behind_every_row_it_writes():
    body = _ask(WIZARD)
    table = body.split("## The mapping — answers to a block", 1)[1].split("\n### ", 1)[0]
    for rule in ("profile: bank", "profile: default", "external_participants: off",
                 "attendee_domains:", "report_to_participants: off", "report_to_participants: on",
                 "recording_retention_days: forever", "organizer_confirms_join: on", "open_web: off"):
        assert rule in table, f"the mapping table does not say when it writes `{rule}`"
    assert "An explicit row wins over the profile" in _flat(table)
    assert "Do not invent a key." in _flat(table)


# ── W4 · the decision record ─────────────────────────────────────────────────────────────────────

def test_the_decision_is_appended_and_an_older_one_is_never_rewritten():
    """The half that is easy to get wrong and impossible to notice: a wizard that rewrote the last
    decision would destroy the only record of why a deployment started where it started, and the
    loss would be invisible — the file would look right."""
    raw = _ask(WIZARD)
    body = _flat(raw)
    assert "## The decision record — only on yes" in raw
    assert "**NEVER REWRITE AN OLDER DECISION.**" in body
    assert "appends a NEW `## Decision` section below the last one" in body
    assert "**Append a `## Decision` section at the END of the file**" in body
    # it writes the block too, and only the block
    assert "those keys and no others" in body
    assert "**Leave the body alone**" in body
    # what the record has to carry
    for field in ("Recorded by", "**Profile:**", "**Overrides:**", "**Declared, not yet enforced:**"):
        assert field in raw, f"the decision record does not carry {field!r}"
    # all five answers, in the record, in their own words
    assert raw.count("| Who is in your meetings? |") == 1
    assert "in their words" in body


def test_nothing_is_written_before_yes():
    body = _flat(_ask(WIZARD))
    assert "On yes, and never before" in body
    assert "A policy nobody decided is better recorded as undecided than written down as agreed." in body


def test_it_refuses_rather_than_pretends_when_global_cannot_be_written():
    body = _flat(_ask(WIZARD))
    assert "Do not pretend the block was written." in body
    assert "a row nothing reads is a control that silently does nothing" in body


# ── W5 · the setup ask calls it by name ──────────────────────────────────────────────────────────

def test_the_setup_ask_calls_the_wizard_by_name_instead_of_restating_it():
    """Point 5 of the issue. One conversation, written in one place: the setup ask used to carry its
    own walk of the rules, which is a second wizard that drifts from the first."""
    body = _flat(_ask("setup-global"))
    assert "read `/workspaces/_global/asks/policies-wizard.md` and follow it" in body
    assert "Do not restate its questions here, do not add a sixth, and do not walk the rules yourself" in body
    # the offer still never blocks the five files (#1583's rule, re-pinned)
    assert "The offer never blocks the files" in body
    assert "the pages must not wait for it" in body
    # …and the page's own act is named, so it is reachable after setup as well
    assert "**Set up policies**" in body


def test_the_setup_ask_no_longer_walks_the_rules_itself():
    body = _flat(_ask("setup-global"))
    assert "How to walk it: name the rule and its current answer in one line" not in body
    assert "Never put all thirteen rules in one message" not in body


# ── the rule the fourth question answers ─────────────────────────────────────────────────────────

def test_the_new_join_rule_is_on_the_page_with_its_own_section_and_marked_unenforced():
    """Question 4 offers `organizer_confirms_join`, so the page has to be able to argue for it — the
    wizard lifts its reasoning from there and can invent none."""
    page = POLICIES_MD.read_text(encoding="utf-8")
    assert '<a id="organizer_confirms_join"></a>' in page
    assert "organizer_confirms_join: off" in page.split("---")[1]
    section = _flat(page.split('<a id="organizer_confirms_join"></a>', 1)[1].split("<a id=", 1)[0])
    for lens in ("**Adoption.**", "**Security.**", "**Adversarial.**", "**The price of turning it on.**"):
        assert lens in section, f"the rule ships without its {lens} lens, so the wizard has none to quote"
    read_today = page.split("## Where each rule is read today", 1)[1]
    assert "| `organizer_confirms_join` | **declared, not yet enforced**" in read_today


def test_the_page_says_where_a_decision_goes():
    page = _flat(POLICIES_MD.read_text(encoding="utf-8"))
    assert "asks/policies-wizard.md" in page
    assert "no decision is ever rewritten" in page


def test_the_wizard_hard_codes_no_ones_name():
    """The denylist `test_preset_rulings.py` applies to the library also applies here, said once
    more at the file this issue adds — a wizard is read out to the one person who decides how every
    agent in a company behaves, and somebody else's name in it is the worst possible furniture."""
    text = _ask(WIZARD)
    for name in ("Marvin", "ASWF", "DNA TSC"):
        assert name not in text
