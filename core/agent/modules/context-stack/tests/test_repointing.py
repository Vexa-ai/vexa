"""L2 — the loose-coupling ruling: a user can be re-pointed at a different personal workspace.

Slots are pointers, not foreign-key constraints. Re-pointing has to work in the *product* path,
not only in free composition — it is how a personal workspace is migrated, and an ordinary user
must not have to see composition for it to happen.
"""

from __future__ import annotations

from context_stack import ContextDelta, Destination, Mode, Policy, land_delta, resolve_stack
from context_stack.workspaces import ensure_personal

from conftest import OWNER


async def test_repointing_personal_changes_the_pinned_stack(store, make_workspace):
    """The ruling, in the product path. No pointer → the user's own personal workspace; a pointer
    → the workspace it names, in pinned mode as well as free."""
    original = await ensure_personal(store, subject=OWNER, address=f"p-{OWNER}@vexa.ai")

    before = await resolve_stack(store, subject=OWNER)
    assert before.at(Policy.PERSONAL)[0].workspace_id == original.id

    replacement = await make_workspace("personal-new", Policy.PERSONAL, OWNER)
    await store.set_pointer(
        subject=OWNER, slot=Policy.PERSONAL, workspace_id=replacement.id
    )
    await store.commit()

    for mode in (Mode.PINNED, Mode.FREE):
        after = await resolve_stack(store, subject=OWNER, mode=mode)
        assert after.at(Policy.PERSONAL)[0].workspace_id == "personal-new"
        assert len(after.at(Policy.PERSONAL)) == 1


async def test_repointing_moves_where_personal_writes_land(store, make_workspace):
    """A re-pointed slot is not cosmetic: the delta goes to the workspace the stack now names."""
    await ensure_personal(store, subject=OWNER, address=f"p-{OWNER}@vexa.ai")
    await make_workspace("personal-new", Policy.PERSONAL, OWNER)
    await store.set_pointer(subject=OWNER, slot=Policy.PERSONAL, workspace_id="personal-new")
    await store.commit()

    stack = await resolve_stack(store, subject=OWNER)
    target = stack.at(Policy.PERSONAL)[0].workspace_id
    landed = await land_delta(
        store,
        ContextDelta(
            workspace_id=target, path="notes/x.md", body="after the move", author_subject=OWNER
        ),
    )

    assert landed.routing.destination is Destination.DIRECT
    assert (
        await store.current_revision(workspace_id="personal-new", path="notes/x.md")
    ).body == "after the move"


async def test_repointing_replaces_rather_than_accumulates(store, make_workspace):
    """A singleton slot holds one pointer. Re-pointing twice leaves one, not three."""
    await ensure_personal(store, subject=OWNER, address=f"p-{OWNER}@vexa.ai")
    await make_workspace("personal-a", Policy.PERSONAL, OWNER)
    await make_workspace("personal-b", Policy.PERSONAL, OWNER)

    await store.set_pointer(subject=OWNER, slot=Policy.PERSONAL, workspace_id="personal-a")
    await store.set_pointer(subject=OWNER, slot=Policy.PERSONAL, workspace_id="personal-b")
    await store.commit()

    pointers = [p for p in await store.pointers(OWNER) if p.slot is Policy.PERSONAL]
    stack = await resolve_stack(store, subject=OWNER)

    assert [p.workspace_id for p in pointers] == ["personal-b"]
    assert stack.at(Policy.PERSONAL)[0].workspace_id == "personal-b"


async def test_a_pointer_survives_its_target_being_absent(store, make_workspace):
    """No foreign key, on purpose. Pointing at a workspace this deployment does not hold is a row
    that inserts and a resolution that reports — not an integrity error at write time."""
    await ensure_personal(store, subject=OWNER, address=f"p-{OWNER}@vexa.ai")

    await store.set_pointer(
        subject=OWNER, slot=Policy.PERSONAL, workspace_id="not-provisioned-yet"
    )
    await store.commit()

    stack = await resolve_stack(store, subject=OWNER)

    assert stack.at(Policy.PERSONAL) == ()
    assert [
        (d.workspace_id, d.reason) for d in stack.denied if d.layer is Policy.PERSONAL
    ] == [("not-provisioned-yet", "dangling-pointer")]


async def test_user_system_can_be_repointed_too(store, make_workspace):
    """The same mechanism on the other hidden singleton — one rule, not a personal special case."""
    from context_stack.workspaces import ensure_user_system

    await ensure_user_system(store, subject=OWNER, address=f"s-{OWNER}@vexa.ai")
    await make_workspace("system-new", Policy.USER_SYSTEM, OWNER)

    await store.set_pointer(
        subject=OWNER, slot=Policy.USER_SYSTEM, workspace_id="system-new"
    )
    await store.commit()

    stack = await resolve_stack(store, subject=OWNER)

    assert stack.at(Policy.USER_SYSTEM)[0].workspace_id == "system-new"
