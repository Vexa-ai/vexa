"""L2 — stack resolution: member, non-member, multi-group, no-group, and the pinned/free split.

Proves the composition resolver returns the four layers in order, as pointers, with the group slot
holding exactly what the subject is entitled to and nothing else.
"""

from __future__ import annotations

import pytest

from context_stack import Mode, Policy, Write, resolve_stack
from context_stack.workspaces import ensure_personal, ensure_user_system

from conftest import MEMBER, OUTSIDER, OWNER


async def _provision(store, make_workspace, subject: str) -> None:
    """Global (once), plus the two singleton layers every user has."""
    if await store.global_workspace() is None:
        await make_workspace("global", Policy.GLOBAL, "vexa")
    await ensure_personal(store, subject=subject, address=f"personal-{subject}@vexa.ai")
    await ensure_user_system(store, subject=subject, address=f"system-{subject}@vexa.ai")


async def test_no_group_resolves_to_three_layers(store, make_workspace):
    """The participant case: someone in no group still gets global → personal → user-system."""
    await _provision(store, make_workspace, OUTSIDER)

    stack = await resolve_stack(store, subject=OUTSIDER)

    assert [s.layer for s in stack.slots] == [
        Policy.GLOBAL,
        Policy.PERSONAL,
        Policy.USER_SYSTEM,
    ]
    assert stack.at(Policy.GROUP) == ()
    assert stack.denied == ()


async def test_member_gets_the_group_layer_in_stack_order(store, make_workspace):
    """A member's stack carries the group between global and personal."""
    await _provision(store, make_workspace, MEMBER)
    await make_workspace("acme", Policy.GROUP, OWNER, members=(MEMBER,))

    stack = await resolve_stack(store, subject=MEMBER)

    assert [s.layer for s in stack.slots] == [
        Policy.GLOBAL,
        Policy.GROUP,
        Policy.PERSONAL,
        Policy.USER_SYSTEM,
    ]
    assert stack.at(Policy.GROUP)[0].workspace_id == "acme"


async def test_non_member_never_sees_the_group(store, make_workspace):
    """The enforcement case. A group exists; the outsider's stack does not contain it."""
    await _provision(store, make_workspace, OUTSIDER)
    await make_workspace("acme", Policy.GROUP, OWNER, members=(MEMBER,))

    stack = await resolve_stack(store, subject=OUTSIDER)

    assert stack.at(Policy.GROUP) == ()
    assert "acme" not in stack.workspace_ids


async def test_multi_group_composes_every_membership(store, make_workspace):
    """Several groups all mount, in a deterministic order."""
    await _provision(store, make_workspace, MEMBER)
    await make_workspace("acme", Policy.GROUP, OWNER, members=(MEMBER,))
    await make_workspace("beta", Policy.GROUP, OWNER, members=(MEMBER,))
    await make_workspace("gamma", Policy.GROUP, OWNER)  # not a member

    stack = await resolve_stack(store, subject=MEMBER)

    assert [s.workspace_id for s in stack.at(Policy.GROUP)] == ["acme", "beta"]


async def test_slots_carry_the_layer_rules_not_content(store, make_workspace):
    """A slot is a pointer plus its layer's access rules — the spec's table, per slot."""
    await _provision(store, make_workspace, MEMBER)
    await make_workspace("acme", Policy.GROUP, OWNER, members=(MEMBER,))

    stack = await resolve_stack(store, subject=MEMBER)
    by_layer = {s.layer: s for s in stack.slots}

    assert (by_layer[Policy.GLOBAL].write, by_layer[Policy.GLOBAL].hidden) == (Write.NONE, True)
    assert by_layer[Policy.GROUP].write is Write.VIA_TRIAGE
    assert by_layer[Policy.GROUP].sharable is True
    assert by_layer[Policy.PERSONAL].write is Write.DIRECT
    assert by_layer[Policy.PERSONAL].sharable is False
    assert (
        by_layer[Policy.USER_SYSTEM].write,
        by_layer[Policy.USER_SYSTEM].hidden,
        by_layer[Policy.USER_SYSTEM].sharable,
    ) == (Write.PLATFORM_ONLY, True, False)
    assert not any(hasattr(s, "body") for s in stack.slots)


async def test_free_mode_composes_a_subset_of_the_groups_you_belong_to(store, make_workspace):
    """Free composition: pointers pick which groups mount, and in what order."""
    await _provision(store, make_workspace, MEMBER)
    await make_workspace("acme", Policy.GROUP, OWNER, members=(MEMBER,))
    await make_workspace("beta", Policy.GROUP, OWNER, members=(MEMBER,))
    await store.set_pointer(
        subject=MEMBER, slot=Policy.GROUP, workspace_id="beta", position=0
    )
    await store.commit()

    free = await resolve_stack(store, subject=MEMBER, mode=Mode.FREE)
    pinned = await resolve_stack(store, subject=MEMBER, mode=Mode.PINNED)

    assert [s.workspace_id for s in free.at(Policy.GROUP)] == ["beta"]
    assert [s.workspace_id for s in pinned.at(Policy.GROUP)] == ["acme", "beta"]


async def test_pinned_mode_ignores_group_pointers(store, make_workspace):
    """The product's composition is not reshapeable by a stored row — the pinned half of the
    'loosely coupled by schema, pinned by product' ruling."""
    await _provision(store, make_workspace, MEMBER)
    await make_workspace("acme", Policy.GROUP, OWNER, members=(MEMBER,))
    await make_workspace("beta", Policy.GROUP, OWNER, members=(MEMBER,))
    await store.set_pointer(subject=MEMBER, slot=Policy.GROUP, workspace_id="beta")
    await store.commit()

    stack = await resolve_stack(store, subject=MEMBER, mode=Mode.PINNED)

    assert [s.workspace_id for s in stack.at(Policy.GROUP)] == ["acme", "beta"]


async def test_free_pointer_at_a_group_you_are_not_in_is_denied(store, make_workspace):
    """Adversarial: composing your way into someone else's group. The slot is dropped and the
    refusal is recorded rather than swallowed."""
    await _provision(store, make_workspace, OUTSIDER)
    await make_workspace("acme", Policy.GROUP, OWNER, members=(MEMBER,))
    await store.set_pointer(subject=OUTSIDER, slot=Policy.GROUP, workspace_id="acme")
    await store.commit()

    stack = await resolve_stack(store, subject=OUTSIDER, mode=Mode.FREE)

    assert stack.at(Policy.GROUP) == ()
    assert [(d.workspace_id, d.reason) for d in stack.denied] == [("acme", "not-member")]


async def test_pointer_at_the_wrong_layer_is_denied(store, make_workspace):
    """Policy is the layer: a group workspace cannot be mounted into the personal slot."""
    await _provision(store, make_workspace, MEMBER)
    await make_workspace("acme", Policy.GROUP, OWNER, members=(MEMBER,))
    await store.set_pointer(subject=MEMBER, slot=Policy.PERSONAL, workspace_id="acme")
    await store.commit()

    stack = await resolve_stack(store, subject=MEMBER, mode=Mode.FREE)

    assert [(d.workspace_id, d.reason) for d in stack.denied] == [("acme", "policy-mismatch")]
    assert stack.at(Policy.PERSONAL) == ()


async def test_dangling_pointer_reports_rather_than_raises(store, make_workspace):
    """Pointers carry no foreign key on purpose; a target this deployment does not hold is a
    resolvable condition, and the rest of the stack still resolves."""
    await _provision(store, make_workspace, MEMBER)
    await store.set_pointer(subject=MEMBER, slot=Policy.GROUP, workspace_id="ghost")
    await store.commit()

    stack = await resolve_stack(store, subject=MEMBER, mode=Mode.FREE)

    assert [(d.workspace_id, d.reason) for d in stack.denied] == [("ghost", "dangling-pointer")]
    assert [s.layer for s in stack.slots] == [
        Policy.GLOBAL,
        Policy.PERSONAL,
        Policy.USER_SYSTEM,
    ]


async def test_missing_personal_is_reported_not_fatal(store, make_workspace):
    """Personal is supposed to exist for everyone. When it does not, the gap is named and the
    remaining layers still resolve."""
    await make_workspace("global", Policy.GLOBAL, "vexa")

    stack = await resolve_stack(store, subject=OUTSIDER)

    reasons = {d.reason for d in stack.denied}
    assert reasons == {"personal-not-provisioned", "user-system-not-provisioned"}
    assert [s.layer for s in stack.slots] == [Policy.GLOBAL]


@pytest.mark.parametrize("mode", [Mode.PINNED, Mode.FREE])
async def test_global_is_mounted_by_everyone(store, make_workspace, mode):
    """Global is product-level knowledge: every stack has it, nobody is a member of it."""
    await _provision(store, make_workspace, OUTSIDER)

    stack = await resolve_stack(store, subject=OUTSIDER, mode=mode)

    assert stack.at(Policy.GLOBAL)[0].workspace_id == "global"
    assert await store.role_of(workspace_id="global", subject=OUTSIDER) is None
