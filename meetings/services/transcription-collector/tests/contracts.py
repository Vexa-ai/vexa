"""Load the SEALED api.v1 contract BY PATH and validate the collector's responses against it.

The api.v1 contract lives in the gateway lane (``v0.12/gateway/contracts/api.v1/``) and is frozen
by ``contracts.seal.json``. The collector CONFORMS to it (never edits it). This helper loads
``api.schema.json`` off disk and validates a payload against a named ``#/components/schemas/<Shape>``,
exactly as the gateway conformance harness does — the same oracle, in-package, so the collector's
own tests prove its bodies conform to the sealed shapes the gateway proxies verbatim.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


def _find_api_schema() -> Path:
    """Walk up to the monorepo root and locate the sealed api.v1 schema by path."""
    rel = Path("gateway") / "contracts" / "api.v1" / "api.schema.json"
    for parent in Path(__file__).resolve().parents:
        candidate = parent / rel
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"sealed contract not found by path: {rel}")


@lru_cache(maxsize=None)
def api_schema() -> dict[str, Any]:
    return json.loads(_find_api_schema().read_text(encoding="utf-8"))


def api_component_validator(shape: str) -> Draft202012Validator:
    root = api_schema()
    base_id = root.get("$id") or "https://vexa.ai/contracts/api.v1"
    if shape not in root.get("components", {}).get("schemas", {}):
        raise KeyError(f"api.v1 has no component schema '{shape}'")
    resource = Resource.from_contents(root, default_specification=DRAFT202012)
    registry = Registry().with_resource(uri=base_id, resource=resource)
    return Draft202012Validator({"$ref": f"{base_id}#/components/schemas/{shape}"}, registry=registry)


def assert_api_conforms(shape: str, payload: Any) -> None:
    errors = sorted(api_component_validator(shape).iter_errors(payload), key=lambda e: str(e.path))
    if errors:
        joined = "; ".join(f"{list(e.absolute_path)}: {e.message}" for e in errors)
        raise AssertionError(f"payload does not conform to api.v1 #/components/schemas/{shape}: {joined}")
