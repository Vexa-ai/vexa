"""The pydantic `SetupSpec` is the sealed `credential-broker.v1` `$defs.SetupSpec`, key for key.

A setup proposal's shape is written three times: the sealed contract, `setup_schema.py` here, and
its byte-identical twin in agent-api. The twins are held together by `gate:fact-parity`; this test
holds this one to the contract, so a field, bound or enum that drifts on either side fails.

Only presentation is normalised: titles, descriptions and defaults (pydantic writes them, the
contract does not), `anyOf` against `oneOf` for an optional field, an `enum`'s implied
`"type": "string"`, and `$ref` names (both are resolved inline).
"""
import json

from conftest import schema
from credential_broker.setup_schema import SetupSpec

_PRESENTATION = {"title", "description", "default", "examples"}


def normalise(node, defs):
    if isinstance(node, list):
        return [normalise(n, defs) for n in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        return normalise(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
    out = {}
    for key, value in node.items():
        if key in _PRESENTATION or key == "$defs":
            continue
        if key in ("anyOf", "oneOf"):
            out["oneOf"] = sorted((normalise(v, defs) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
        elif key == "required":
            if value:
                out[key] = sorted(value)
        else:
            out[key] = normalise(value, defs)
    if "enum" in out and out.get("type") == "string":
        del out["type"]
    return out


def test_the_pydantic_setup_spec_is_the_sealed_one():
    sealed = schema()["$defs"]
    generated = SetupSpec.model_json_schema()
    assert normalise(generated, generated.get("$defs", {})) == normalise(sealed["SetupSpec"], sealed)


def test_the_comparison_sees_a_drift():
    """The normaliser is not so forgiving that it hides a change: a bound moved on one side fails."""
    sealed = json.loads(json.dumps(schema()["$defs"]))
    sealed["OAuthSpec"]["properties"]["scopes"]["maxItems"] = 31
    generated = SetupSpec.model_json_schema()
    assert normalise(generated, generated.get("$defs", {})) != normalise(sealed["SetupSpec"], sealed)
