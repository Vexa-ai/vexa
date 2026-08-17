"""L2 — the write-only secret surface: set → rotate → read metadata, and no path to the value.

The strong test here is not that a particular function withholds the material; it is that the
whole surface has nowhere to put it. The types are enumerated, the module's public functions are
enumerated, and their returns are searched for the value.

``tests/test_api_surface.py`` does the same over the HTTP routes.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect

import pytest

from context_stack import (
    AccessDenied,
    InvalidWorkspace,
    NotFound,
    Policy,
    SecretMetadata,
    get_metadata,
    list_metadata,
    secrets as secrets_surface,
    set_secret,
    delete_secret,
)
from context_stack.material import REDACTED, use_material

from conftest import MEMBER, OUTSIDER, OWNER

KEY = "sk-live-000000000000cafe"
ROTATED = "sk-live-111111111111beef"


async def test_set_rotate_and_read_metadata(store, make_workspace):
    """The whole owner-facing lifecycle. Rotation bumps the version and moves last-4; neither
    call, at any point, hands back a key."""
    await make_workspace("acme", Policy.GROUP, OWNER)

    first = await set_secret(
        store, workspace_id="acme", name="llm_api_key", material=KEY, actor=OWNER
    )
    assert (first.last4, first.version, first.rotated_at) == ("cafe", 1, None)

    second = await set_secret(
        store, workspace_id="acme", name="llm_api_key", material=ROTATED, actor=OWNER
    )
    assert (second.last4, second.version) == ("beef", 2)
    assert second.rotated_at is not None

    read = await get_metadata(store, workspace_id="acme", name="llm_api_key", actor=OWNER)
    assert (read.last4, read.version, read.set_by) == ("beef", 2, OWNER)

    assert await delete_secret(store, workspace_id="acme", name="llm_api_key", actor=OWNER)
    with pytest.raises(NotFound):
        await get_metadata(store, workspace_id="acme", name="llm_api_key", actor=OWNER)


def test_the_metadata_type_has_nowhere_to_put_a_secret():
    """The surface is write-only because of the type, not because of the callers."""
    fields = {f.name for f in dataclasses.fields(SecretMetadata)}

    assert fields == {
        "workspace_id",
        "name",
        "last4",
        "version",
        "set_by",
        "set_at",
        "rotated_at",
    }
    assert not fields & {"material", "value", "secret", "plaintext", "key"}


async def test_no_function_on_the_surface_returns_the_value(store, make_workspace):
    """Enumerate the module's public functions, call each, and search every return for the key.

    This is the check that would have caught the settings-read defect this design inverts: the
    question is not whether one endpoint leaks, it is whether any of them do.
    """
    await make_workspace("acme", Policy.GROUP, OWNER)
    await set_secret(store, workspace_id="acme", name="llm_api_key", material=KEY, actor=OWNER)

    public = [
        name
        for name, obj in vars(secrets_surface).items()
        if inspect.iscoroutinefunction(obj) and not name.startswith("_")
    ]
    assert set(public) == {"set_secret", "delete_secret", "get_metadata", "list_metadata"}

    results = [
        await get_metadata(store, workspace_id="acme", name="llm_api_key", actor=OWNER),
        await list_metadata(store, workspace_id="acme", actor=OWNER),
        await set_secret(
            store, workspace_id="acme", name="llm_api_key", material=KEY, actor=OWNER
        ),
        await delete_secret(store, workspace_id="acme", name="llm_api_key", actor=OWNER),
    ]

    for result in results:
        assert KEY not in repr(result)
        assert KEY not in str(result)


async def test_only_the_owner_touches_secrets(store, make_workspace):
    """Secrets are one of the four things an owner does. A member is not an owner."""
    await make_workspace("acme", Policy.GROUP, OWNER, members=(MEMBER,))
    await set_secret(store, workspace_id="acme", name="llm_api_key", material=KEY, actor=OWNER)

    for actor in (MEMBER, OUTSIDER):
        with pytest.raises(AccessDenied) as write:
            await set_secret(
                store, workspace_id="acme", name="llm_api_key", material="hijacked-key", actor=actor
            )
        with pytest.raises(AccessDenied) as read:
            await get_metadata(store, workspace_id="acme", name="llm_api_key", actor=actor)
        assert write.value.decision.reason == "not-owner"
        assert read.value.decision.reason == "not-owner"


async def test_the_user_system_layer_holds_no_credentials_ever(store, make_workspace):
    """A spec property of the layer, enforced as one: not even the owner can put a key there."""
    await make_workspace("system-o", Policy.USER_SYSTEM, OWNER)

    with pytest.raises(AccessDenied) as raised:
        await set_secret(
            store, workspace_id="system-o", name="llm_api_key", material=KEY, actor=OWNER
        )

    assert raised.value.decision.reason == "user-system-holds-no-credentials"


async def test_global_secrets_are_deployment_config(store, make_workspace):
    """Ours, and they live in a k8s Secret or a chart value — not in a workspace row."""
    await make_workspace("global", Policy.GLOBAL, "vexa")

    with pytest.raises(AccessDenied) as raised:
        await set_secret(
            store, workspace_id="global", name="llm_api_key", material=KEY, actor="vexa"
        )

    assert raised.value.decision.reason == "global-secrets-are-deployment-config"


async def test_a_short_secret_is_refused(store, make_workspace):
    """Below the minimum, last-4 would be most of the key — and a key that short is a typo."""
    await make_workspace("acme", Policy.GROUP, OWNER)

    with pytest.raises(InvalidWorkspace):
        await set_secret(store, workspace_id="acme", name="llm_api_key", material="abc", actor=OWNER)


async def test_the_one_reader_wraps_what_it_returns(store, make_workspace):
    """Material is readable exactly once, at call time, through a handle that keeps itself out of
    logs: repr, str and format are all redacted, and ``reveal()`` is one grep from an audit."""
    await make_workspace("acme", Policy.GROUP, OWNER)
    await set_secret(store, workspace_id="acme", name="llm_api_key", material=KEY, actor=OWNER)

    handle = await use_material(
        store, workspace_id="acme", name="llm_api_key", purpose="llm-dispatch"
    )

    assert handle.reveal() == KEY
    assert KEY not in repr(handle)
    assert KEY not in str(handle)
    assert KEY not in f"{handle}"
    assert REDACTED in f"{handle}"
    assert handle.purpose == "llm-dispatch"


def test_the_management_surface_does_not_import_the_reader():
    """``secrets.py`` manages; ``material.py`` reads. The separation is what makes the import
    graph — rather than a code review — the thing that keeps a value out of a response.

    Read from the parse tree: the docstring names the other module on purpose, and prose about
    the rule is not a violation of it.
    """
    tree = ast.parse(inspect.getsource(secrets_surface))

    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    referenced = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }

    assert not [m for m in imported if m and "material" in m]
    assert "use_material" not in referenced
    assert "Material" not in referenced
