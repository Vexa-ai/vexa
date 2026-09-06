"""THE FLOW PAGES ARE THE CODE, OR THEY ARE FOLKLORE.

`behavior/global/flows/<flow>.md` is generated from the registry (`make flow-pages`) and seeded into
every instance's `_global`. A page a person can read that says what a flow does is worth exactly as
much as the guarantee that it still does it — so this file compares the committed pages against what
the generator produces from the code that is in the tree right now. A step whose docstring, domains,
mail template, policy rules or body changed and whose page did not is red here, at the commit, and
not a discovery six weeks later.

If this fails: run `make flow-pages` in `core/flows` and read the diff. The diff IS the review — it
says exactly what a person opening that page will now be told.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import flows_pages
from flows_steps import policies

REPO = Path(flows_pages.__file__).resolve().parents[3]
PAGES = REPO.joinpath(*flows_pages.PAGES_DIR)


@pytest.fixture(scope="module")
def generated() -> dict:
    return flows_pages.all_pages()


def test_every_flow_this_image_carries_has_a_page(generated):
    if not PAGES.is_dir():
        pytest.fail(f"{PAGES} does not exist — run `make flow-pages`")
    on_disk = {f.name for f in PAGES.iterdir() if f.is_file() and f.suffix == ".md"}
    assert set(generated) == on_disk, (
        f"the pages and the flows have drifted: only in the code {sorted(set(generated) - on_disk)}, "
        f"only on disk {sorted(on_disk - set(generated))} — run `make flow-pages`")


def test_each_page_is_what_the_code_says(generated):
    if not PAGES.is_dir():
        pytest.skip(f"no pages at {PAGES}")
    stale = [name for name, body in sorted(generated.items())
             if (PAGES / name).read_text(encoding="utf-8") != body]
    assert not stale, (f"these pages no longer match the code: {stale}. Run `make flow-pages` in "
                       f"core/flows and read the diff — it is what a person opening the page will "
                       f"be told.")


def test_the_generator_is_deterministic():
    """Two runs, one answer. A page set that depends on the environment makes the test above a coin
    toss — which is why `build_registry` names the agent door itself."""
    assert flows_pages.all_pages() == flows_pages.all_pages()


def test_the_agent_only_flows_are_on_the_page_set(generated):
    """`meeting_prep` and friends register only where the agent domain is named. The page set must
    not depend on whether the machine that generated it happened to name one."""
    for name in ("meeting_prep.md", "email_chat.md", "desk_setup.md", "desk_claim.md"):
        assert name in generated


def test_the_post_meeting_page_names_the_rules_that_flow_honours(generated):
    body = generated["post_meeting.md"]
    for rule in ("report_to_participants", "external_participants", "attendee_domains"):
        assert f"POLICIES.md#{rule}" in body, (
            f"{rule} is read by a step in post_meeting and the page does not say so — the reader "
            f"cannot tell which switch changes what they are looking at")


def test_a_rule_is_found_through_a_closure_not_only_in_the_step_body():
    """`email_attendees` honours three rules and names none of them: `_attendees` and
    `_followup_on` do, and those are closures in `production.build`'s scope. Scanning the step body
    alone would report a flow that honours nothing, confidently."""
    reg = flows_pages.build_registry()
    body = flows_pages.reachable_source(reg.steps["email_attendees"])
    assert "external_participants" in body
    assert "def _attendees" in body


def test_the_view_source_block_carries_the_real_python(generated):
    body = generated["post_meeting.md"]
    assert '<ViewSource step="email_attendees">' in body
    assert "```python" in body
    assert "def email_attendees(ctx: StepCtx):" in body


def test_the_index_lists_every_page(generated):
    index = generated["README.md"]
    for name in generated:
        if name == "README.md":
            continue
        assert f"]({name})" in index


def test_every_rule_link_points_at_a_real_anchor(generated):
    """A page that links to a rule the policy file does not carry sends a reader to nothing."""
    seeded = REPO / "behavior" / "global" / "POLICIES.md"
    if not seeded.is_file():
        pytest.skip("no POLICIES.md in the seed")
    anchors = seeded.read_text(encoding="utf-8")
    for name, body in generated.items():
        for rule in policies.DEFAULTS:
            if f"POLICIES.md#{rule}" in body:
                assert f'id="{rule}"' in anchors, f"{name} links to #{rule}, which POLICIES.md lacks"
