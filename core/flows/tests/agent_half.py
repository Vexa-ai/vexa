"""IS THE AGENT HALF OF THE PRODUCTION DEFINITIONS IN THIS TREE?

`flows_defs/production_agent.py` is OPTIONAL BY CONSTRUCTION, and `production._register_agent_flows`
says so in the product code: it checks `importlib.util.find_spec` before importing, *"so a cut that
deletes `production_agent.py` outright leaves this module registering the three flows it keeps
rather than failing to import"*. A cut that omits the agent half is a supported tree, and
`flows_defs/README.md` names the five flows that live there — `meeting_prep`, `email_chat`,
`desk_setup`, `desk_claim`, `workspace_invite`.

**The suite has to be honest on BOTH trees, and that is not the same as passing on both.** Before
this module, fourteen tests asserted on steps and flows that only the full tree registers; on the
cut tree they failed, and the whole suite went red for a reason that is not a defect. The two wrong
ways out are equally bad: deleting the assertions loses the contract on the tree that HAS the agent
half, and loosening them (`>=` instead of `==`, a `try/except`) turns a real regression into a
silent pass. So the assertions stay exactly as strict as they were, and the ones that cannot mean
anything without the module are SKIPPED BY PRESENCE, with the reason on the skip.

The presence signal is the one the product itself uses — `find_spec`, never an import attempt:
a missing optional module is a fact about the tree, a broken one is a defect, and `except
ImportError` cannot tell them apart.

Usage:

    import agent_half

    @agent_half.required
    def test_something_about_prepare_meeting(): ...

    EXPECTED = CORE_STEPS | (AGENT_HALF_STEPS if agent_half.PRESENT else set())
"""
from __future__ import annotations

import importlib.util

import pytest

#: True when `flows_defs/production_agent.py` is in this tree.
PRESENT: bool = importlib.util.find_spec("flows_defs.production_agent") is not None

WHY = ("flows_defs/production_agent.py is not in this tree — the agent half of the production "
       "definitions (meeting_prep · email_chat · desk_setup · desk_claim · workspace_invite, and "
       "the steps prepare_meeting · feedback_turn · await_scaffold · await_claim · "
       "mail_workspace_invite) is an optional module "
       "this cut omits, and `production._register_agent_flows` skips it by find_spec. This "
       "assertion is about those flows and can say nothing here.")

#: Decorator: skip this test on a tree with no agent half.
required = pytest.mark.skipif(not PRESENT, reason=WHY)

#: The production steps that are registered ONLY by the agent half. Written out for the same
#: reason `test_no_agents.AGENT_STEPS` is: the point of the list is to be READ in review.
#:
#: `mail_workspace_invite` is on this list and NOT on `test_no_agents.AGENT_STEPS`, and the two
#: lists are answering different questions: that one is "which steps declare `needs=("agent",)`"
#: (this one declares nothing — it renders a template and sends a notification), this one is
#: "which steps does the optional module register at all".
STEPS = frozenset({"prepare_meeting", "feedback_turn", "await_scaffold", "await_claim",
                   "mail_workspace_invite"})

#: The flows it registers, likewise (flows_defs/README.md's own table).
FLOWS = frozenset({"meeting_prep", "email_chat", "desk_setup", "desk_claim", "workspace_invite"})


def only_if_present(names) -> set:
    """`names` on a tree that has the agent half, nothing on a tree that does not.

    For the set-equality assertions: the expected set is composed rather than skipped, so the
    contract keeps its exact strictness on both trees instead of going quiet on one of them."""
    return set(names) if PRESENT else set()
