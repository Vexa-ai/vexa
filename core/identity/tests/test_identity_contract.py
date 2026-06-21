"""identity.v1 conformance (Python side) — goldens + minted/decided objects validate.

The authoritative gate:schema check is `contracts/identity.v1/validate.mjs` (ajv2020). This mirrors
it in-process so the identity core's own emitted shapes (ScopedToken.to_contract, AccessDecision)
are proven to conform to the sealed schema, and the goldens are double-checked under jsonschema.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource as RefResource
from referencing.jsonschema import DRAFT202012

from identity_core import Resource, can_access, mint_token

CONTRACT_DIR = Path(__file__).resolve().parents[1] / "contracts" / "identity.v1"
SCHEMA = json.loads((CONTRACT_DIR / "identity.schema.json").read_text())
GOLDEN_DIR = CONTRACT_DIR / "golden"

# Register the schema under its $id so #/$defs/<Shape> refs resolve (modern referencing API).
_REGISTRY = Registry().with_resource(SCHEMA["$id"], RefResource.from_contents(SCHEMA, DRAFT202012))


def _validator_for(defname: str) -> Draft202012Validator:
    return Draft202012Validator(
        {"$ref": f"{SCHEMA['$id']}#/$defs/{defname}"}, registry=_REGISTRY
    )


@pytest.mark.parametrize("path", sorted(GOLDEN_DIR.glob("*.json")))
def test_golden_conforms_to_its_def(path):
    """Each golden `<Shape>.<case>.json` conforms to #/$defs/<Shape>."""
    shape = path.name.split(".")[0]
    data = json.loads(path.read_text())
    _validator_for(shape).validate(data)


def test_minted_token_conforms():
    """A token from mint_token().to_contract() conforms to ScopedToken."""
    now = datetime.now(timezone.utc)
    blob = mint_token("42", ["bot", "tx"], expires_at=now + timedelta(hours=1), email="o@vexa.ai").to_contract()
    _validator_for("ScopedToken").validate(blob)


def test_decision_conforms():
    """A canAccess verdict conforms to AccessDecision (both allow and deny)."""
    res = Resource(kind="meeting_transcript", id="m-1", owner="42")
    _validator_for("AccessDecision").validate(can_access("42", res).to_contract())
    _validator_for("AccessDecision").validate(can_access("99", res).to_contract())
