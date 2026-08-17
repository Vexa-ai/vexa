"""L2 — the write router: policy decides where a delta lands, both ways.

Group-policy content goes to the proposal queue and stays there; personal-policy content goes
into context directly. The two refusing layers are proved too, because a router that only ever
says yes has no table.
"""

from __future__ import annotations

import pytest

from context_stack import ContextDelta, Destination, Policy, Write, land_delta, route
from context_stack.layers import RULES

from conftest import MEMBER, OUTSIDER, OWNER


# ── the table, without a database ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("policy", "member", "destination", "reason"),
    [
        (Policy.PERSONAL, True, Destination.DIRECT, "personal-policy-writes-direct"),
        (Policy.GROUP, True, Destination.PROPOSAL, "group-policy-routes-to-triage"),
        (Policy.GROUP, False, Destination.REFUSED, "not-member"),
        (Policy.PERSONAL, False, Destination.REFUSED, "not-member"),
        (Policy.GLOBAL, True, Destination.REFUSED, "global-is-read-only"),
        (Policy.USER_SYSTEM, True, Destination.REFUSED, "user-system-is-platform-only"),
    ],
)
def test_routing_table(policy, member, destination, reason):
    """Every cell of the routing table, as a pure function of policy and membership."""
    routing = route(policy=policy, workspace_id="w", member=member)
    assert (routing.destination, routing.reason) == (destination, reason)


def test_no_layer_routes_a_group_write_direct():
    """The invariant the split of policy-and-layer into one field exists to protect: nothing can
    be at the group layer and take direct writes."""
    for policy, rules in RULES.items():
        if policy is Policy.GROUP:
            assert rules.write is Write.VIA_TRIAGE
        else:
            assert rules.write is not Write.VIA_TRIAGE


# ── the same table, against the store ─────────────────────────────────────────────────────────


async def test_personal_delta_lands_in_context_immediately(store, make_workspace):
    """Personal-policy content → personal context directly."""
    await make_workspace("personal-o", Policy.PERSONAL, OWNER)

    landed = await land_delta(
        store,
        ContextDelta(
            workspace_id="personal-o",
            path="notes/acme.md",
            body="They run Teams, not Meet.",
            author_subject=OWNER,
            source_ref="meeting-42",
        ),
    )

    assert landed.routing.destination is Destination.DIRECT
    assert landed.proposal is None
    current = await store.current_revision(workspace_id="personal-o", path="notes/acme.md")
    assert current.revision == 1
    assert current.body == "They run Teams, not Meet."


async def test_group_delta_lands_in_the_queue_and_not_in_context(store, make_workspace):
    """Group-policy content → the proposal queue. PR-style, never direct: the document does not
    exist in the group's context until an owner says so."""
    await make_workspace("acme", Policy.GROUP, OWNER, members=(MEMBER,))

    landed = await land_delta(
        store,
        ContextDelta(
            workspace_id="acme",
            path="kg/pricing.md",
            body="Renewal moved to Q3.",
            author_subject=MEMBER,
            source_ref="meeting-42",
        ),
    )

    assert landed.routing.destination is Destination.PROPOSAL
    assert landed.revision is None
    assert landed.proposal.state == "pending"
    assert await store.current_revision(workspace_id="acme", path="kg/pricing.md") is None


async def test_a_hundred_group_deltas_accept_none_of_themselves(store, make_workspace):
    """The machine path has a ceiling. However much a meeting produces, nothing is accepted —
    acceptance is a human act and no volume of machine writes reaches it."""
    await make_workspace("acme", Policy.GROUP, OWNER, members=(MEMBER,))

    for i in range(100):
        await land_delta(
            store,
            ContextDelta(
                workspace_id="acme", path=f"kg/{i}.md", body=str(i), author_subject=MEMBER
            ),
        )

    assert len(await store.proposals(workspace_id="acme", state="pending")) == 100
    assert await store.proposals(workspace_id="acme", state="accepted") == ()
    assert await store.documents("acme") == ()


async def test_appending_to_the_same_path_makes_a_new_revision(store, make_workspace):
    """Context is append-only: a second write supersedes, and the first is still readable."""
    await make_workspace("personal-o", Policy.PERSONAL, OWNER)
    delta = ContextDelta(
        workspace_id="personal-o", path="notes/acme.md", body="v1", author_subject=OWNER
    )
    await land_delta(store, delta)
    await land_delta(store, ContextDelta(**{**vars(delta), "body": "v2"}))

    history = await store.revisions(workspace_id="personal-o", path="notes/acme.md")

    assert [(r.revision, r.body) for r in history] == [(1, "v1"), (2, "v2")]


async def test_outsider_cannot_even_propose(store, make_workspace):
    """A queue anyone may fill is a queue the owner stops reading."""
    await make_workspace("acme", Policy.GROUP, OWNER, members=(MEMBER,))

    landed = await land_delta(
        store,
        ContextDelta(
            workspace_id="acme", path="kg/x.md", body="spam", author_subject=OUTSIDER
        ),
    )

    assert landed.routing.destination is Destination.REFUSED
    assert landed.routing.reason == "not-member"
    assert await store.proposals(workspace_id="acme") == ()


async def test_global_and_user_system_refuse_context_deltas(store, make_workspace):
    """Global is ours and read-only; user-system is the platform's to write."""
    await make_workspace("global", Policy.GLOBAL, "vexa")
    await make_workspace("system-o", Policy.USER_SYSTEM, OWNER)

    for workspace_id, reason in (
        ("global", "global-is-read-only"),
        ("system-o", "user-system-is-platform-only"),
    ):
        landed = await land_delta(
            store,
            ContextDelta(
                workspace_id=workspace_id, path="x.md", body="b", author_subject=OWNER
            ),
        )
        assert landed.routing.destination is Destination.REFUSED
        assert landed.routing.reason == reason
        assert await store.documents(workspace_id) == ()
