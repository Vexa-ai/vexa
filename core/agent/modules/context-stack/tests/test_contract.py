"""L1 — the published contract: what this module emits conforms to ``context-stack.v1``.

``gate:schema`` already proves the goldens match the schema. This proves the other half — that
live output matches it too — so the published shape cannot drift from the code that produces it
while the goldens sit there still passing.

The schema is loaded **by path**, never by importing the owning domain, which is the convention
``core/agent/contracts/loader.py`` already follows.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from referencing import Registry, Resource

from context_stack import (
    Action,
    ContextDelta,
    Mode,
    Policy,
    Role,
    accept_proposal,
    decide,
    land_delta,
    resolve_stack,
    set_secret,
)
from context_stack.workspaces import ensure_personal, ensure_user_system

from conftest import MEMBER, OUTSIDER, OWNER

SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "contracts"
    / "context-stack.v1"
    / "context-stack.schema.json"
)


@pytest.fixture(scope="session")
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def _validator(schema: dict, shape: str) -> jsonschema.Draft202012Validator:
    registry = Registry().with_resource(schema["$id"], Resource.from_contents(schema))
    return jsonschema.Draft202012Validator(
        {"$ref": f"{schema['$id']}#/$defs/{shape}"}, registry=registry
    )


def test_the_schema_is_where_the_goldens_are(schema):
    """A wrong path here would make every assertion below vacuous."""
    assert schema["$id"] == "https://vexa.ai/schemas/context-stack.v1"
    assert (SCHEMA_PATH.parent / "golden").is_dir()


def test_the_layer_enum_matches_the_code(schema):
    """The four layers, in stack order, in both places."""
    assert schema["$defs"]["Policy"]["enum"] == [p.value for p in Policy]
    assert [p.value for p in Policy] == ["global", "group", "personal", "user-system"]


async def test_a_resolved_stack_conforms(schema, store, make_workspace):
    await make_workspace("global", Policy.GLOBAL, "vexa")
    await make_workspace("acme", Policy.GROUP, OWNER, members=(MEMBER,))
    await ensure_personal(store, subject=MEMBER, address="p@vexa.ai")
    await ensure_user_system(store, subject=MEMBER, address="s@vexa.ai")

    for mode in (Mode.PINNED, Mode.FREE):
        stack = await resolve_stack(store, subject=MEMBER, mode=mode)
        _validator(schema, "ResolvedStack").validate(stack.to_contract())
        for slot in stack.slots:
            _validator(schema, "StackSlot").validate(slot.to_contract())


async def test_a_denial_conforms(schema, store, make_workspace):
    """Every denial reason the resolver can emit is in the published enum."""
    await make_workspace("acme", Policy.GROUP, OWNER)
    await store.set_pointer(subject=OUTSIDER, slot=Policy.GROUP, workspace_id="acme")
    await store.set_pointer(subject=OUTSIDER, slot=Policy.PERSONAL, workspace_id="ghost")
    await store.commit()

    stack = await resolve_stack(store, subject=OUTSIDER, mode=Mode.FREE)

    assert stack.denied
    for denial in stack.denied:
        _validator(schema, "Denial").validate(denial.to_contract())


async def test_both_routings_conform(schema, store, make_workspace):
    await make_workspace("acme", Policy.GROUP, OWNER, members=(MEMBER,))
    personal = await ensure_personal(store, subject=MEMBER, address="p@vexa.ai")

    group = await land_delta(
        store,
        ContextDelta(workspace_id="acme", path="kg/x.md", body="b", author_subject=MEMBER),
    )
    direct = await land_delta(
        store,
        ContextDelta(workspace_id=personal.id, path="n/x.md", body="b", author_subject=MEMBER),
    )
    accepted = await accept_proposal(store, proposal_id=group.proposal.id, actor=OWNER)

    for landed in (group, direct, accepted):
        _validator(schema, "Routing").validate(landed.routing.to_contract())


def test_every_access_decision_conforms(schema):
    """Exhaust the decision table against the published reason enum: no code the schema lacks."""
    import itertools

    for policy, role, action in itertools.product(Policy, (None, Role.MEMBER, Role.OWNER), Action):
        verdict = decide(
            subject="s", workspace_id="w", policy=policy, role=role, action=action
        )
        _validator(schema, "AccessDecision").validate(verdict.to_contract())


async def test_secret_metadata_conforms_and_the_shape_holds_no_material(schema, store, make_workspace):
    """The published shape has no material field, and what the code emits fits it exactly —
    ``additionalProperties: false`` means a leaked field would fail this test, not pass it."""
    await make_workspace("acme", Policy.GROUP, OWNER)
    metadata = await set_secret(
        store,
        workspace_id="acme",
        name="llm_api_key",
        material="sk-live-000000000000cafe",
        actor=OWNER,
    )

    _validator(schema, "SecretMetadata").validate(metadata.to_contract())

    published = schema["$defs"]["SecretMetadata"]
    assert published["additionalProperties"] is False
    assert "material" not in published["properties"]
    assert not [
        name
        for shape in schema["$defs"].values()
        for name in shape.get("properties", {})
        if name in {"material", "plaintext", "secret_value"}
    ]
