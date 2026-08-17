"""L2 — enforcement, tested adversarially: every way a non-member might reach group context.

The rule is one sentence — a non-member must never read group context — and the tests are the
ways round it: resolve a stack, read the workspace, read a document, read the queue, propose into
it, decide in it, add yourself to it. Each is refused with a stable code.

The default-deny half is proved by exhausting the decision table rather than by sampling it.
"""

from __future__ import annotations

import itertools

import pytest

from context_stack import (
    AccessDenied,
    Action,
    ContextDelta,
    Destination,
    InvalidWorkspace,
    Policy,
    Role,
    add_member,
    decide,
    land_delta,
    pending_proposals,
    remove_member,
    resolve_stack,
)
from context_stack.api import _readable

from conftest import MEMBER, OUTSIDER, OWNER


async def test_every_route_to_a_group_is_closed_to_a_non_member(store, make_workspace):
    """Seven attempts, one workspace, zero reachable."""
    await make_workspace("acme", Policy.GROUP, OWNER, members=(MEMBER,))
    await land_delta(
        store,
        ContextDelta(
            workspace_id="acme", path="kg/pricing.md", body="secret plan", author_subject=MEMBER
        ),
    )

    # 1. the composed stack does not contain it
    stack = await resolve_stack(store, subject=OUTSIDER)
    assert "acme" not in stack.workspace_ids

    # 2. reading the workspace itself
    with pytest.raises(AccessDenied) as workspace_read:
        await _readable(store, "acme", OUTSIDER)
    assert workspace_read.value.decision.reason == "not-member"

    # 3. the read decision, directly
    assert not decide(
        subject=OUTSIDER, workspace_id="acme", policy=Policy.GROUP, role=None, action=Action.READ
    ).allow

    # 4. reading the proposal queue
    with pytest.raises(AccessDenied):
        await pending_proposals(store, workspace_id="acme", actor=OUTSIDER)

    # 5. writing into it
    landed = await land_delta(
        store,
        ContextDelta(workspace_id="acme", path="kg/x.md", body="x", author_subject=OUTSIDER),
    )
    assert landed.routing.destination is Destination.REFUSED

    # 6. adding themselves to it
    with pytest.raises(AccessDenied) as self_add:
        await add_member(store, workspace_id="acme", subject=OUTSIDER, actor=OUTSIDER)
    assert self_add.value.decision.reason == "not-owner"

    # 7. a member cannot let them in either — sharing is the owner's
    with pytest.raises(AccessDenied) as member_add:
        await add_member(store, workspace_id="acme", subject=OUTSIDER, actor=MEMBER)
    assert member_add.value.decision.reason == "not-owner"


async def test_one_users_personal_layer_is_closed_to_another(store, make_workspace):
    """Personal is read/write for its owner and nobody else — it is not a small group."""
    await make_workspace("personal-o", Policy.PERSONAL, OWNER)

    with pytest.raises(AccessDenied) as raised:
        await _readable(store, "personal-o", MEMBER)

    assert raised.value.decision.reason == "not-member"


async def test_user_system_is_never_sharable(store, make_workspace):
    """Hidden, one per user, never shared. Sharing it is refused at the layer, not per workspace."""
    await make_workspace("system-o", Policy.USER_SYSTEM, OWNER)

    with pytest.raises(AccessDenied) as raised:
        await add_member(store, workspace_id="system-o", subject=MEMBER, actor=OWNER)

    assert raised.value.decision.reason == "layer-not-sharable"


async def test_personal_cannot_take_a_second_member(store, make_workspace):
    """"The user is not a group": a personal workspace is the one-member case, so there is no
    second seat. A workspace that needs two members is a group workspace."""
    await make_workspace("personal-o", Policy.PERSONAL, OWNER)

    with pytest.raises(AccessDenied) as raised:
        await add_member(store, workspace_id="personal-o", subject=MEMBER, actor=OWNER)

    assert raised.value.decision.reason == "personal-is-not-a-group"
    assert await store.member_count("personal-o") == 1


async def test_global_is_readable_by_everyone_and_writable_by_nobody(store, make_workspace):
    """Product-level knowledge: mounted by every stack, owned by us, changed by neither."""
    await make_workspace("global", Policy.GLOBAL, "vexa")

    assert (await _readable(store, "global", OUTSIDER)).policy is Policy.GLOBAL
    assert not decide(
        subject="vexa",
        workspace_id="global",
        policy=Policy.GLOBAL,
        role=Role.OWNER,
        action=Action.WRITE,
    ).allow
    with pytest.raises(AccessDenied):
        await add_member(store, workspace_id="global", subject=OUTSIDER, actor="vexa")


async def test_the_owners_seat_cannot_be_removed(store, make_workspace):
    """A workspace with no owner has nobody to triage it, and its queue silently stops being
    answerable."""
    await make_workspace("acme", Policy.GROUP, OWNER, members=(MEMBER,))

    with pytest.raises(InvalidWorkspace):
        await remove_member(store, workspace_id="acme", subject=OWNER, actor=OWNER)

    assert await store.role_of(workspace_id="acme", subject=OWNER) is Role.OWNER
    assert await remove_member(store, workspace_id="acme", subject=MEMBER, actor=OWNER)


def test_the_decision_table_denies_by_default():
    """Exhaust it: every (policy, role, action) triple resolves to an explicit allow rule or a
    refusal with a named reason. No combination falls through to an accidental allow."""
    allowed = set()
    for policy, role, action in itertools.product(Policy, (None, Role.MEMBER, Role.OWNER), Action):
        verdict = decide(
            subject="s", workspace_id="w", policy=policy, role=role, action=action
        )
        assert verdict.reason, f"{policy}/{role}/{action} has no reason code"
        if verdict.allow:
            allowed.add((policy, role, action))

    # The complete allow-list, written out. A new allow anywhere changes this set, and changing
    # it is the point at which someone has to justify the new permission.
    assert allowed == {
        # global: everyone reads, nobody writes, nobody shares, and its credentials are
        # deployment config rather than rows — so not even its owner sets a secret here
        (Policy.GLOBAL, None, Action.READ),
        (Policy.GLOBAL, Role.MEMBER, Action.READ),
        (Policy.GLOBAL, Role.OWNER, Action.READ),
        # group: members read and write (via triage), the owner also triages, shares, holds keys
        (Policy.GROUP, Role.MEMBER, Action.READ),
        (Policy.GROUP, Role.MEMBER, Action.WRITE),
        (Policy.GROUP, Role.OWNER, Action.READ),
        (Policy.GROUP, Role.OWNER, Action.WRITE),
        (Policy.GROUP, Role.OWNER, Action.TRIAGE),
        (Policy.GROUP, Role.OWNER, Action.SECRETS),
        (Policy.GROUP, Role.OWNER, Action.SHARE),
        # personal: the owner's, and only theirs
        (Policy.PERSONAL, Role.MEMBER, Action.READ),
        (Policy.PERSONAL, Role.MEMBER, Action.WRITE),
        (Policy.PERSONAL, Role.OWNER, Action.READ),
        (Policy.PERSONAL, Role.OWNER, Action.WRITE),
        (Policy.PERSONAL, Role.OWNER, Action.SECRETS),
        # user-system: read only. No context delta writes it, and it holds no external
        # credentials ever — so SECRETS is absent for every role, including the owner's
        (Policy.USER_SYSTEM, Role.MEMBER, Action.READ),
        (Policy.USER_SYSTEM, Role.OWNER, Action.READ),
    }
