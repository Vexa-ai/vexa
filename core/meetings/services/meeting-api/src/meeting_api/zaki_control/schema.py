"""Load and validate the versioned zaki-control.v1 schema at the HTTP seam."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
from referencing import Registry, Resource


def _load_schema() -> dict:
    rel = Path("meetings") / "contracts" / "zaki-control.v1" / "zaki-control.schema.json"
    for parent in Path(__file__).resolve().parents:
        candidate = parent / rel
        if candidate.is_file():
            return json.loads(candidate.read_text())
    raise FileNotFoundError(f"monorepo root with {rel} not found")


SCHEMA = _load_schema()
REGISTRY = Registry().with_resource(SCHEMA["$id"], Resource.from_contents(SCHEMA))


def conforms(value: Any, shape: str) -> None:
    jsonschema.Draft202012Validator(
        {"$ref": f"{SCHEMA['$id']}#/$defs/{shape}"}, registry=REGISTRY
    ).validate(value)
