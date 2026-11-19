"""agent-api's copy of the setup shape (`connection_setup_schema.py`) is the sealed
`credential-broker.v1` `$defs.SetupSpec`, key for key.

The agent sees this model on its `connection_request` tool, so a field or bound that drifted from
the contract would publish a shape the broker then refuses. The copy is byte-compared with the
broker's (`gate:fact-parity`); this test holds it to the contract itself. Only presentation is
normalised: titles, descriptions, defaults, `anyOf`/`oneOf` for an optional field, an `enum`'s
implied `"type": "string"`, and `$ref` names (resolved inline).
"""
import json
from pathlib import Path

from control_plane.connection_setup_schema import SetupSpec

CONTRACT = Path(__file__).resolve().parents[1] / "contracts" / "credential-broker.v1" / "credential-broker.schema.json"
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


def sealed_defs():
    return json.loads(CONTRACT.read_text())["$defs"]


def test_the_published_setup_spec_is_the_sealed_one():
    sealed = sealed_defs()
    generated = SetupSpec.model_json_schema()
    assert normalise(generated, generated.get("$defs", {})) == normalise(sealed["SetupSpec"], sealed)


def test_the_comparison_sees_a_drift():
    sealed = sealed_defs()
    sealed["InputField"]["properties"]["label"]["maxLength"] = 81
    generated = SetupSpec.model_json_schema()
    assert normalise(generated, generated.get("$defs", {})) != normalise(sealed["SetupSpec"], sealed)
