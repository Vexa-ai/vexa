"""L2/L3 — owner triage: accept, reject, and the guards that keep a machine out of both.

The rule under test is "no machine ever writes acknowledgement". It is enforced three ways and
each is proved separately here: the actor is required and must be the owner; the machine path in
``router.py`` has no import of the triage module; and the table refuses a decided row that does
not name a human.
"""

from __future__ import annotations

import ast
import inspect
import re

import pytest
from sqlalchemy import text

from context_stack import (
    AccessDenied,
    ContextDelta,
    Policy,
    ProposalAlreadyDecided,
    accept_proposal,
    land_delta,
    pending_proposals,
    reject_proposal,
    router,
    triage,
)

from conftest import MEMBER, OUTSIDER, OWNER


async def _proposed(store, make_workspace, body: str = "Renewal moved to Q3."):
    await make_workspace("acme", Policy.GROUP, OWNER, members=(MEMBER,))
    landed = await land_delta(
        store,
        ContextDelta(
            workspace_id="acme",
            path="kg/pricing.md",
            body=body,
            author_subject=MEMBER,
            source_ref="meeting-42",
        ),
    )
    return landed.proposal


async def test_accept_applies_the_delta_to_group_context(store, make_workspace):
    """Accept is the only way group context gains a revision."""
    proposal = await _proposed(store, make_workspace)

    landed = await accept_proposal(store, proposal_id=proposal.id, actor=OWNER, note="checked")

    current = await store.current_revision(workspace_id="acme", path="kg/pricing.md")
    assert current.body == "Renewal moved to Q3."
    assert current.from_proposal_id == proposal.id
    assert landed.proposal.state == "accepted"
    assert landed.proposal.decided_by == OWNER
    assert landed.proposal.decided_at is not None


async def test_accepted_revision_credits_the_proposer_and_the_decider(store, make_workspace):
    """Two people, both named: who wrote the knowledge, and who let it in."""
    proposal = await _proposed(store, make_workspace)

    landed = await accept_proposal(store, proposal_id=proposal.id, actor=OWNER)

    assert landed.revision.author_subject == MEMBER
    assert landed.revision.source_kind == "triage"
    assert landed.proposal.decided_by == OWNER


async def test_reject_leaves_context_untouched_and_keeps_the_record(store, make_workspace):
    """Rejection writes nothing to context and loses nothing from the record."""
    proposal = await _proposed(store, make_workspace)

    decided = await reject_proposal(
        store, proposal_id=proposal.id, actor=OWNER, note="already covered"
    )

    assert decided.state == "rejected"
    assert decided.decided_by == OWNER
    assert decided.decision_note == "already covered"
    assert await store.current_revision(workspace_id="acme", path="kg/pricing.md") is None
    assert decided.body == "Renewal moved to Q3."


async def test_a_member_may_propose_but_not_decide(store, make_workspace):
    """Triage is one of the four things an owner does. A member's write is a proposal, full stop."""
    proposal = await _proposed(store, make_workspace)

    with pytest.raises(AccessDenied) as accepted:
        await accept_proposal(store, proposal_id=proposal.id, actor=MEMBER)
    with pytest.raises(AccessDenied) as rejected:
        await reject_proposal(store, proposal_id=proposal.id, actor=MEMBER)

    assert accepted.value.decision.reason == "not-owner"
    assert rejected.value.decision.reason == "not-owner"
    assert (await store.get_proposal(proposal.id)).state == "pending"


async def test_an_outsider_cannot_decide_or_even_read_the_queue(store, make_workspace):
    """Adversarial: the queue is a view of what a group's members are writing."""
    proposal = await _proposed(store, make_workspace)

    with pytest.raises(AccessDenied) as decided:
        await accept_proposal(store, proposal_id=proposal.id, actor=OUTSIDER)
    with pytest.raises(AccessDenied) as read:
        await pending_proposals(store, workspace_id="acme", actor=OUTSIDER)

    assert decided.value.decision.reason == "not-owner"
    assert read.value.decision.reason == "not-owner"


async def test_a_decided_proposal_is_final(store, make_workspace):
    """Re-deciding would overwrite one human's answer with another's under the same id."""
    proposal = await _proposed(store, make_workspace)
    await accept_proposal(store, proposal_id=proposal.id, actor=OWNER)

    with pytest.raises(ProposalAlreadyDecided):
        await reject_proposal(store, proposal_id=proposal.id, actor=OWNER)


async def test_the_owners_queue_holds_only_pending_work(store, make_workspace):
    """What the owner is asked to look at shrinks as they answer it."""
    first = await _proposed(store, make_workspace, body="one")
    landed = await land_delta(
        store,
        ContextDelta(
            workspace_id="acme", path="kg/other.md", body="two", author_subject=MEMBER
        ),
    )

    await accept_proposal(store, proposal_id=first.id, actor=OWNER)
    queue = await pending_proposals(store, workspace_id="acme", actor=OWNER)

    assert [p.id for p in queue] == [landed.proposal.id]


# ── the structural guards ─────────────────────────────────────────────────────────────────────


def test_no_auto_accept_exists_anywhere_in_the_surface():
    """Enumerate the triage surface: two decision verbs, both requiring a named human. No
    accept-all, no auto-accept, no default actor."""
    verbs = {
        name
        for name, obj in vars(triage).items()
        if callable(obj) and not name.startswith("_") and getattr(obj, "__module__", "") == triage.__name__
    }
    assert verbs == {"accept_proposal", "reject_proposal", "pending_proposals"}

    for name in verbs:
        signature = inspect.signature(getattr(triage, name))
        actor = signature.parameters["actor"]
        assert actor.default is inspect.Parameter.empty, f"{name} defaults its actor"
        assert actor.kind is inspect.Parameter.KEYWORD_ONLY, f"{name} takes a positional actor"

    assert not [
        name
        for name in dir(triage)
        if not name.startswith("__") and re.search(r"auto|accept_all|bulk|batch", name, re.I)
    ]


def test_the_machine_path_cannot_reach_the_human_path():
    """``router.py`` is what a meeting drives. It imports no triage module and names no accept
    symbol, so there is no call chain from a meeting to an acceptance.

    Read from the parse tree, not the text: prose about the rule is not a violation of it.
    """
    tree = ast.parse(inspect.getsource(router))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    referenced = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }

    assert not [m for m in imported if m and "triage" in m]
    assert not [r for r in referenced if "accept" in r or "triage" in r]


async def test_the_table_refuses_an_unattributed_decision(store, make_workspace):
    """The last guard: even a caller bypassing this package entirely cannot leave an accepted
    proposal that names nobody."""
    proposal = await _proposed(store, make_workspace)

    with pytest.raises(Exception) as raised:
        await store.session.execute(
            text("UPDATE context_proposals SET state='accepted' WHERE id=:id"),
            {"id": proposal.id},
        )
        await store.session.commit()

    assert "ck_proposal_decision_is_attributed" in str(raised.value)
