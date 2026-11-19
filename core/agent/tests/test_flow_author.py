"""THE ADMIN WRITES FLOWS FROM THE GOVERNANCE CHAT (Vexa-ai/vexa#1639).

Founder, 2026-09-06, in the governance chat of `_global`: *"you get organization authorization to
write the flows"* → the agent: *"the gate is open, but I still don't have the instruction … tell me
which one you want … confirm you do want me to actually `flows_submit`"* → *"so you can't write the
keys?"* → **"we want to be able to write flows for the global chat as we like."**

Nothing was broken. `flows_submit` already filed a flow as data and had the worker running it ten
seconds later, and the administrator already held the authority to call it. What was missing was the
CONVERSATION: no ask turned a sentence into a trigger and a step list, so the agent turned the
administrator's words into a questionnaire and then asked permission for a permission it had been
given in the previous message.

`behavior/asks/flow-author.md` is that conversation. This file pins its shape; the half that pins it
against the real step vocabulary is `core/flows/tests/test_flow_author_ask.py`, because the
vocabulary lives in that package and the ask lives in `behavior/`, which both can read.

Seven claims, in the order they would fail:

  F1  It is a preset this library can actually serve, and the intent behind the act resolves to it.
      An act whose ask is not there degrades to a sentence.
  F2  ONE confirmation, and it is asked on the flow shown AS ITS PAGE, before anything is submitted.
      Not a questionnaire — the failure the founder met.
  F3  The three sentences the agent actually said are named as sentences it never says.
  F4  The authorization is taken ONCE and recorded append-only, and the ask says out loud that it is
      not the confirmation. Two different questions, one asked once and one asked every time.
  F5  A step that does not exist becomes a proposal page — `kind: proposal`, the Python in this
      repo's step shape, the flow that would use it, the tests it needs, never executed.
  F6  The Send act files through the report path this deployment already has, with the code fenced,
      and carries no names.
  F7  The page is the proof: the ask links the generated page and says when it appears.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from control_plane import chat_intents, flow_pages_watch, scaffolds

REPO = pathlib.Path(__file__).resolve().parents[3]
BEHAVIOR_ASKS = REPO / "behavior" / "asks"
POLICIES_MD = REPO / "behavior" / "global" / "POLICIES.md"
ASK = "flow-author"


@pytest.fixture(scope="module")
def body() -> str:
    return (BEHAVIOR_ASKS / f"{ASK}.md").read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """The text with its line wrapping removed — a sentence pinned across a wrap is still that
    sentence, and re-flowing a paragraph must not fail a test about what it says."""
    return " ".join((text or "").split())


# ── F1 · the preset, and the act that runs it ───────────────────────────────────────────────────

def test_the_intent_resolves_to_this_ask():
    assert chat_intents.INTENT_PRESETS["flow_author"] == ASK
    assert chat_intents.preset_for({"kind": "flow_author"}) == ASK


def test_it_is_neither_a_background_job_nor_silent():
    """It opens a QUESTION — the one confirmation — and a question that runs on a background thread
    is a question nobody is there to answer. And the person pressed a labelled control, so they must
    read that label back."""
    intent = {"kind": "flow_author", "workspace": "_global", "path": "flows/README.md"}
    assert not chat_intents.is_job(intent)
    assert not chat_intents.is_silent(intent)
    assert chat_intents.act_prefix(intent)


def test_the_library_serves_it(tmp_path):
    """Read through the same two roots a click reads through: the admin's copy in `_global/asks/`
    first, the image's second. Here only the image has it, which is a fresh instance."""
    fm, text = scaffolds.read_preset(tmp_path, ASK, image_root=BEHAVIOR_ASKS)
    assert fm.get("label") and fm.get("mounts")
    assert "_global" in str(fm.get("mounts"))
    assert text.lstrip().startswith(f"[{ASK}]")


def test_it_opens_on_the_flows_page(body):
    """`tabs:`/`focus:` put the administrator on the page about flows while they are writing one —
    the index that lists what this deployment already does, which is the thing a new flow has to
    fit beside."""
    head = body.split("---")[1]
    assert "_global/flows/README.md" in head


# ── F2 · one confirmation, on the flow shown as its page ────────────────────────────────────────

def test_the_flow_is_shown_as_the_page_it_will_become(body):
    flat = _flat(body)
    assert "show the flow the way its page will show it" in flat
    for field in ("trigger", "the steps in order", "what it mails", "the rules it honours"):
        assert field in flat, field
    assert "Not a JSON body, not a form: the page." in flat


def test_exactly_one_confirmation_and_it_precedes_the_submit(body):
    assert "**Then ask one question and stop: activate it?**" in body
    assert "## On yes, and never before" in body
    assert body.index("ask one question and stop") < body.index("`flows_submit(name=")


def test_a_missing_fact_is_one_question_and_never_a_questionnaire(body):
    flat = _flat(body)
    assert "a question, not a questionnaire, and never more than one at a time" in flat


# ── F3 · the three sentences it never says ──────────────────────────────────────────────────────

@pytest.mark.parametrize("said", [
    "I don't have the instruction",
    "tell me which one you want",
    "confirm you do want me to actually submit",
])
def test_the_words_the_agent_actually_said_are_named_as_words_it_never_says(body, said):
    """Verbatim from the 2026-09-06 exchange. Naming them is the point: a rule that said "be
    helpful" would not have stopped any of the three."""
    section = body[body.index("## The one sentence you never say"):]
    section = section[:section.index("\n## ", 1)]
    assert said in _flat(section)


def test_it_says_what_it_will_do_instead(body):
    section = body[body.index("## The one sentence you never say"):]
    assert "**Say what you will do with what they said.**" in section


# ── F4 · the authorization, once ────────────────────────────────────────────────────────────────

def test_the_grant_is_standing_and_not_re_asked(body):
    flat = _flat(body)
    assert "It is not re-asked per flow" in flat
    assert "Do not raise it again, in this conversation or any later one." in flat


def test_the_grant_is_not_the_confirmation(body):
    """The failure of 2026-09-06 was these two being answered as one question. The grant says *you
    may write flows*; the confirmation says *write THIS one*."""
    flat = _flat(body)
    assert "**The one confirmation per activation stays.**" in flat
    assert "They are different questions and the second is asked every time." in flat


def test_the_record_is_appended_and_never_rewritten(body):
    section = body[body.index("## The authorization record"):]
    flat = _flat(section)
    assert "/workspaces/_global/POLICIES.md" in flat
    assert "after everything already there" in flat
    assert "**Never rewrite an older one**" in flat
    assert "## Authorization — <YYYY-MM-DD>" in section


def test_the_policy_page_says_where_a_standing_authorization_lives():
    """The ask writes the record; `POLICIES.md` is where a reader who never runs the ask finds out
    that such a record exists and what it does and does not cover."""
    page = POLICIES_MD.read_text(encoding="utf-8")
    flat = _flat(page)
    assert "## What the administrator is authorized to do, and where that is written" in page
    assert "`## Authorization` sections" in flat
    assert "**A standing authorization is not a confirmation, and it does not replace one.**" in flat
    assert "asks/flow-author.md" in flat


# ── F5 · a step that does not exist ─────────────────────────────────────────────────────────────

def _proposal_template(body: str) -> str:
    m = re.search(r"^````markdown\n(.*?)^````$", body, flags=re.M | re.S)
    assert m, "the proposal page template is not in the ask"
    return m.group(1)


def test_a_missing_step_is_neither_bent_into_an_existing_one_nor_refused(body):
    flat = _flat(body)
    assert "do not bend an existing step into it and do not refuse" in flat
    assert "*This needs code — no step does it. I have written it as a proposal.*" in flat


def test_the_proposal_page_declares_what_it_is_and_where_it_lives(body):
    assert "/workspaces/_global/flows/proposals/<slug>.md" in body
    tpl = _proposal_template(body)
    assert "kind: proposal" in tpl
    assert "status: needs code — never executed" in tpl
    assert _flat(body).count("never executed and never submitted") == 1


def test_the_proposal_carries_the_step_in_this_repos_own_shape(body):
    """A developer has to be able to read it as the file it would become — the decorator that
    declares which domains the body reaches and what their absence does, and the house
    `Reads: · Effect: · Result:` triple the generated pages already print."""
    tpl = _proposal_template(body)
    assert "```python" in tpl
    assert "@reg.step(needs=" in tpl and "absent=" in tpl
    assert "abort | skip | degrade" in tpl
    assert "def <step_name>(ctx: StepCtx):" in tpl
    assert "Reads:" in tpl and "Effect:" in tpl and "Result:" in tpl


def test_the_proposal_carries_the_flow_that_would_use_it_and_the_tests_it_needs(body):
    tpl = _proposal_template(body)
    assert "## The flow that would use it" in tpl
    assert "**trigger**" in tpl and "**steps**" in tpl
    assert "## The tests it needs" in tpl
    assert "when the domain it needs is not deployed" in _flat(tpl)


def test_the_proposal_page_carries_the_send_act(body):
    tpl = _proposal_template(body)
    assert "## Send to the developers" in tpl
    assert "This page has not been sent." in tpl


# ── F6 · the send ───────────────────────────────────────────────────────────────────────────────

def test_the_send_goes_through_the_report_path_this_deployment_already_has(body):
    """`report_issue` is the one path in this product that files an ISSUE — it maps the ticket onto
    GitHub's issue API where the operator configured that sink. Where it is not configured the
    report still has to land, and the carrier every deployment has is friction."""
    section = body[body.index("**The Send act.**"):]
    section = section[:section.index("\n## ", 1)]
    assert "`report_issue`" in section and "`report_friction`" in section
    assert section.index("`report_issue`") < section.index("`report_friction`")
    assert "not configured" in section
    assert "Say which one it went to" in _flat(section)


def test_the_code_is_fenced_and_verbatim_in_what_a_human_reads_first(body):
    section = body[body.index("**The Send act.**"):]
    flat = _flat(section)
    assert "code fence, verbatim" in flat
    assert "`what_happened`" in flat


def test_the_send_carries_no_names(body):
    section = body[body.index("**NO NAMES.**"):]
    flat = _flat(section)
    assert "not a customer's, not a domain, not an address, not a meeting title" in flat
    assert "`pilot`" in flat and "Jane Smith" in flat and "jsmith@example.com" in flat
    assert "A ticket cannot be withdrawn." in flat


def test_a_proposal_is_never_sent_twice(body):
    assert "Never send the same proposal twice." in body


# ── F7 · the page is the proof ──────────────────────────────────────────────────────────────────

def test_the_chat_links_the_page_the_activation_produces(body):
    section = body[body.index("## On yes, and never before"):]
    flat = _flat(section)
    assert "_global/flows/<name>@<version>.md" in flat
    assert "live within about ten seconds" in flat
    assert "It carries the version and who activated it." in flat
    assert "Say the path; do not paste the page." in flat


def test_the_page_name_the_ask_promises_is_the_one_the_writer_produces():
    """The ask tells the administrator where to look; `flow_pages_watch` is what puts the file
    there. One shape, pinned across the two files that would otherwise drift."""
    assert flow_pages_watch.RUNTIME_PAGE.match("post_meeting@4.md")
    assert not flow_pages_watch.RUNTIME_PAGE.match("post_meeting.md")


def test_editing_is_a_new_version_with_the_diff_and_one_confirmation(body):
    section = body[body.index("## Editing is a new version"):]
    section = section[:section.index("\n## ", 1)]
    flat = _flat(section)
    assert "A step list is never edited in place." in flat
    assert "**Show the diff, and only the diff**" in flat
    assert "(removed)" in section
    assert "*file version 4 and retire version 3?*" in flat
    assert "`flow_lifecycle(name, <the old version>, \"retire\")`" in flat
    assert "**Both pages stay.**" in flat
    assert "keep the version they were admitted on" in flat
    # And it never mints a version by hand — `flows_submit` files the next one itself, which is what
    # keeps two sessions from filing the same number.
    assert "never pass one" in flat
