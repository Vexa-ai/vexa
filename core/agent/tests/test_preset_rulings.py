"""THE FOUNDER'S HOT PRESET RULINGS, PORTED OFF THE DOGFOOD VOLUME (Vexa-ai/vexa#1608).

During the 2026-09-06 walk the founder's rulings were applied HOT, on the running dogfood instance,
to its `_global/asks/*.md`. That is the one place they could NOT survive: the preset library is
admin-owned on the volume and `preset_library.top_up` is deliberately additive — it never overwrites
what is already there (`test_preset_library.py`) — so a hot edit lives on exactly one instance and a
fresh deployment gets none of it. The repo copies under `behavior/asks/` are what every new instance
is born with, and they are what these tests read.

Each test below pins the load-bearing sentence of one ruling, the way
`test_scaffold.py::test_the_setup_preset_opens_by_CONFIRMING_the_company_it_read_off_the_address`
pins the confirmation: not the paragraph, which will be rewritten, but the thing the ruling turned
on, so a rewrite that drops it fails here instead of on a customer's first instance.

The port is a MERGE, not a copy: the volume was behind the repo on four separate later changes
(#1583/#1607's first message, #1593's `{{instruction}}`, #1596's `extend-transcript`, decision 19's
grounded prep question), so the last test in this file pins those too — the direction of a merge is
exactly the thing a second port would get wrong.
"""
from __future__ import annotations

import pathlib

import pytest

from control_plane import scaffolds

REPO = pathlib.Path(__file__).resolve().parents[3]
BEHAVIOR_ASKS = REPO / "behavior" / "asks"

EXPAND_ACTS = ("create", "extend")


def _ask(name: str) -> str:
    return (BEHAVIOR_ASKS / f"{name}.md").read_text(encoding="utf-8")


# ── setup-global · the objective, and the graph around it ────────────────────────────────────────

def test_the_setup_preset_pursues_its_OWN_objective_and_does_not_wait_to_be_driven():
    """Founder ruling, 2026-09-06. The scaffold is not a form the administrator fills in one field
    at a time: the agent's objective is the five files written and `mark_global_ready` called, and
    it drives toward that itself. "Being handed one confirmation is not the end of your job."
    """
    body = _ask("setup-global")
    assert "**Your objective is the global scaffold, and you pursue it on your own.**" in body
    assert "`mark_global_ready` called" in body
    assert "You do not wait to be told the next step" in body
    assert "you stop only where a decision or a private fact is needed" in body
    # it is the FIRST thing under the heading — the small runner sends the whole turn as one user
    # message, so position is the only lever there is (`test_scaffold.py` makes the same point).
    how = body.index("## How to run it")
    assert how < body.index("**Your objective is the global scaffold") < body.index("**READ SILENTLY.")


def test_the_five_files_stay_thin_and_the_substance_goes_to_a_connected_graph_of_pages():
    """The layer is THIN (that doctrine predates this ruling and is untouched); what the ruling adds
    is where the substance goes instead — a page per thing, linked both ways, each fact sourced."""
    body = _ask("setup-global")
    assert "RICH, CONNECTED graph" in body
    assert "`entity_upsert`" in body
    assert "[[wikilinks]]" in body
    assert "each fact carrying its\nsource" in body
    assert "The five files stay thin; the substance goes to the pages they link to." in body
    # and the doctrine it was added to is still there
    assert "`_global` is THIN" in body


def test_the_README_is_a_MAP_a_reader_can_walk_from_the_name_to_everything_known():
    body = _ask("setup-global")
    assert "`README.md` is the MAP" in body
    for item in ("[[divisions and studios]]", "[[offices]]", "[[leaders]]",
                 "[[key clients and products]]"):
        assert item in body
    assert "so a reader can walk from the name to everything known" in body


def test_every_company_tier_page_is_written_INTO_global_and_only_the_admins_own_page_is_not():
    """The failure this sentence exists to stop: `entity_upsert` defaults to the caller's own desk,
    so a company page written without `slug` lands on ONE person's desk — invisible to everyone
    else, in a layer whose entire purpose is that every agent in the company carries it."""
    body = _ask("setup-global")
    assert "**Every company-tier page is written INTO `_global`**" in body
    assert "/workspaces/_global/kg/entities/<kind>/<slug>.md" in body
    assert 'pass `slug="_global"` for every company-tier page' in body
    assert "Only the administrator's own\nperson page (`self: true`) goes to their desk." in body
    assert ("A company page that lands on one person's desk is invisible to everyone else and\n"
            "wrong.") in body


def test_the_setup_preset_works_from_public_data_BEFORE_it_asks_the_administrator_anything():
    """Founder ruling, 2026-09-06. A question whose answer is on the company's own website is a
    question the agent failed to look up — and stopping because a file is done is not stopping for
    a human, it is stopping for nothing."""
    body = _ask("setup-global")
    assert ("**Work from public data first, and keep going until only a human can answer.**"
            in body)
    assert ("A\nquestion whose answer is on the company's own website is a question you failed to "
            "look up.") in body
    assert "Stop for the human when you need a decision or a fact that is not public; never because a file\nis done." in body
    assert "Being handed one confirmation is not the end of your job" in body
    # it sits after the first message and before the walk of the five — research, then ask
    assert (body.index("**Your first message is the confirmation above**")
            < body.index("**Work from public data first")
            < body.index("Then walk the five"))


def test_who_can_see_what_is_WRITTEN_as_the_default_first_and_the_choice_offered_after():
    """Founder, 2026-09-06, watching it happen: *"it offered to choose and setup policies, which is
    kind of cool … that's exactly what we need at this stage"* — so the offer stays. What the ruling
    fixes is the ORDER: the platform's stance is written into `STRUCTURE.md` as the default first,
    the choice is offered after in a few lines, and the five files never wait for the answer."""
    body = _ask("setup-global")
    assert ("Write the platform's own stance into `STRUCTURE.md` under who can see what FIRST, as "
            "the default,") in body
    assert "then offer the administrator the choice in one short message" in body
    assert "(say what the options are in two lines each, not a lecture)" in body
    assert "record\nwhichever they pick in their words" in body
    assert "The offer never blocks the files" in body
    assert "the pages must not wait for it" in body
    # the sentence that made the files wait on an answer is GONE
    assert "Ask the administrator whether that is what they intend" not in body
    # decision 21's own words, which this ruling refines rather than replaces, are untouched
    assert "a desk is company knowledge held by one person" in body
    assert "what stays genuinely private is `_system`" in body
    # and disagreement is still a `MISSING.md` line, not something to smooth over
    assert "is a `MISSING.md` line, not something to smooth over" in body


# ── create / extend · expand means EVERY direction ───────────────────────────────────────────────

@pytest.mark.parametrize("act", EXPAND_ACTS)
def test_the_expand_acts_grow_the_graph_in_EVERY_direction(act):
    """Founder ruling, 2026-09-06. The page the button was pressed on is a NODE: the act researches
    it, then gives each thing it finds AROUND it its own page and links both ways. Pinned on both
    acts because they were edited together and drift apart silently — Create and Extend are the same
    move on a page that does not exist yet and one that does."""
    body = _ask(act)
    assert "## Expand means EVERY direction" in body
    assert "the page is a NODE and you grow the graph\naround it" in body
    assert "Research the subject from public data (WebSearch, WebFetch)" in body
    assert "give it its own page with `entity_upsert` in the SAME workspace as\nthis page" in body
    # the same `slug` trap the company-tier pages have, one layer down
    assert "(pass that workspace as `slug`; `_global` for company-tier pages)" in body
    assert "link it from this\npage with a [[wikilink]] and link back" in body
    assert "Every fact carries its source." in body
    # ONE neighbour is not the graph
    assert "Stop when the neighbours\nare written, not after the first one" in body
    assert "say in one line what the page now connects to" in body


@pytest.mark.parametrize("act", EXPAND_ACTS)
def test_the_expand_section_did_not_displace_the_act_it_was_added_to(act, tmp_path):
    """A port that appends is still a port that can break what it appends to: the frontmatter must
    still parse, the body must still open with its own act label, and the refusal — create nothing /
    change nothing when there is nothing to say — must survive."""
    fm, body = scaffolds.read_preset(tmp_path, act, image_root=BEHAVIOR_ASKS)
    assert fm["label"] == act
    assert body.lstrip().startswith(f"[{act}]")
    assert "{{path}}" in body and "{{workspace}}" in body and "{{selection}}" in body
    assert body.index("## Expand means EVERY direction") > body.index("{{selection}}")
    if act == "create":
        assert "If you found nothing to put there, create nothing and say so in one line." in body
    else:
        assert "say that in one line and change nothing" in body


# ── no preset hard-codes a name ──────────────────────────────────────────────────────────────────

def test_no_preset_hard_codes_a_persons_or_a_companys_name():
    """Founder ruling: no hard-coded names. `first-visit.md` illustrated "name the specific things"
    with a REAL person and a REAL organisation, and a preset is read out to a stranger as if it were
    about them — the one context where somebody else's name is worst.

    A denylist, not a heuristic, and it earns its keep: these exact strings are all over this repo's
    fixtures, evals and dogfood rig, which is precisely where the next copy-paste into a preset would
    come from. The deliberate exclusions are `Vexa` (the product introduces itself by name, from
    `_global/README.md`) and the two domain→name derivation EXAMPLES in `setup-global.md`, which are
    the founder's own current wording on the volume.
    """
    forbidden = ("Marvin", "ASWF", "DNA TSC")
    for f in sorted(BEHAVIOR_ASKS.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        for name in forbidden:
            assert name not in text, (
                f"{f.name} hard-codes {name!r}. Presets carry no person's or company's name — "
                f"take it from the facts block above the ask, and write a placeholder here.")


def test_the_first_visit_preset_names_things_from_the_facts_block_not_from_its_own_example():
    """The replacement has to keep the teaching: "You've been added to a workspace" is a
    notification and the specific thing is why they stayed. Only the example is neutralised."""
    body = _ask("first-visit")
    assert '"You\'ve been\nadded to a workspace" is a notification' in body
    assert "is why\nthey stayed." in body
    assert "the ones in the facts block, never an example from this text" in body
    assert "**No preset hard-codes a person or a company**" in body
    for placeholder in ("&lt;the colleague who shared it&gt;", "&lt;workspace\nname&gt;",
                        "&lt;meeting title&gt;"):
        assert placeholder in body


# ── the merge direction ──────────────────────────────────────────────────────────────────────────

def test_the_repos_own_later_work_survived_the_port_from_the_volume():
    """THE TEST THAT MAKES THIS A MERGE. The dogfood volume was BEHIND the repo on four changes at
    once, so a copy in the other direction — the obvious way to do this job — would have silently
    reverted every one of them. Each assertion here is a thing the volume copy does not have."""
    setup, create, extend, prep = (_ask("setup-global"), _ask("create"), _ask("extend"),
                                   _ask("prep"))
    # #1583 / #1607 — the confirmation is a sentence, not a stop ("it just stopped")
    assert "KEEP GOING in the same turn" in setup
    assert "unless you correct me." in setup
    assert "carry straight on into the work." in setup
    assert "One message, one\nquestion" not in setup
    # #1593 — the words they typed on the button win over the agent's own reading of the page
    for body in (create, extend):
        assert "{{instruction}}" in body
        assert "Those are THEIR words, not a paraphrase and not a suggestion" in body
    # decision 19 — the prep question is grounded in what the reading turned up
    assert "never a generic closer" in prep
    assert "A\ngeneric question is worse than no question" in prep
    # #1596 / #1598 — the transcript and meeting extends ship in the library
    assert (BEHAVIOR_ASKS / "extend-transcript.md").is_file()
    assert (BEHAVIOR_ASKS / "extend-meeting.md").is_file()
