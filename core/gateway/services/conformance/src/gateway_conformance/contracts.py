"""Load the SEALED gateway contracts BY PATH and validate payloads against them.

The contracts live one lane up at ``v0.12/gateway/contracts/{api.v1,ws.v1}/`` and are
frozen by ``contracts.seal.json``. This module is the ONLY place the schema is read; it
never invents shapes — it loads ``api.schema.json`` / ``ws.schema.json`` off disk and
validates against a named component:

- api.v1 components live at ``#/components/schemas/<Shape>`` (OpenAPI 3.1 doc).
- ws.v1   components live at ``#/$defs/<Shape>`` (a JSON-Schema 2020-12 doc).

Validation uses the Draft 2020-12 validator (matches the contracts' ``$schema``). A
RefResolver-style store is built so internal ``$ref``s (e.g. ``MeetingListResponse`` →
``MeetingResponse`` → ``MeetingStatus``) resolve against the loaded document.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

# v0.12/gateway/services/conformance/src/gateway_conformance/contracts.py
#   .parents[4] == v0.12/gateway  →  contracts/ sits beside services/
_GATEWAY_ROOT = Path(__file__).resolve().parents[4]
CONTRACTS_DIR = _GATEWAY_ROOT / "contracts"

API_SCHEMA_PATH = CONTRACTS_DIR / "api.v1" / "api.schema.json"
WS_SCHEMA_PATH = CONTRACTS_DIR / "ws.v1" / "ws.schema.json"
LOGEVENT_SCHEMA_PATH = CONTRACTS_DIR / "logevent.v1" / "logevent.schema.json"


@lru_cache(maxsize=None)
def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"sealed contract not found by path: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def api_schema() -> dict[str, Any]:
    """The frozen api.v1 OpenAPI 3.1 document (≡ main api-gateway 1.5.0)."""
    return _load(API_SCHEMA_PATH)


def ws_schema() -> dict[str, Any]:
    """The frozen ws.v1 JSON-Schema 2020-12 document (the /ws multiplex)."""
    return _load(WS_SCHEMA_PATH)


def logevent_schema() -> dict[str, Any]:
    """The logevent.v1 JSON-Schema 2020-12 document (the structured-log envelope)."""
    return _load(LOGEVENT_SCHEMA_PATH)


def logevent_validator() -> Draft202012Validator:
    """Validator for a logevent.v1 ``#/$defs/LogEvent`` line (loaded BY PATH)."""
    root = logevent_schema()
    base_id = root.get("$id") or "https://vexa.ai/schemas/logevent.v1"
    return _validator_for(root, "#/$defs/LogEvent", base_id)


def assert_logevent_conforms(payload: Any) -> None:
    """Raise AssertionError with a readable message if ``payload`` is not a valid LogEvent."""
    errors = sorted(logevent_validator().iter_errors(payload), key=lambda e: str(e.path))
    if errors:
        joined = "; ".join(f"{list(e.absolute_path)}: {e.message}" for e in errors)
        raise AssertionError(f"log line does not conform to logevent.v1 #/$defs/LogEvent: {joined}")


def is_conformant_logevent(payload: Any) -> bool:
    """True iff ``payload`` is a valid logevent.v1 LogEvent (no raise)."""
    return not list(logevent_validator().iter_errors(payload))


def _validator_for(root: dict[str, Any], pointer: str, base_id: str) -> Draft202012Validator:
    """Build a Draft 2020-12 validator for ``{"$ref": base_id#pointer}``.

    The sealed document is added to a referencing ``Registry`` under ``base_id``; the
    validator's schema is a ``$ref`` into it, so the sub-schema AND every nested ref resolve
    against the loaded document (no network, no invented shapes).
    """
    resource = Resource.from_contents(root, default_specification=DRAFT202012)
    registry = Registry().with_resource(uri=base_id, resource=resource)
    return Draft202012Validator({"$ref": f"{base_id}{pointer}"}, registry=registry)


def api_component_validator(shape: str) -> Draft202012Validator:
    """Validator for an api.v1 ``#/components/schemas/<shape>``."""
    root = api_schema()
    base_id = root.get("$id") or "https://vexa.ai/contracts/api.v1"
    if shape not in (root.get("components", {}).get("schemas", {})):
        raise KeyError(f"api.v1 has no component schema '{shape}'")
    return _validator_for(root, f"#/components/schemas/{shape}", base_id)


def ws_def_validator(shape: str) -> Draft202012Validator:
    """Validator for a ws.v1 ``#/$defs/<shape>``."""
    root = ws_schema()
    base_id = root.get("$id") or "https://vexa.ai/contracts/ws.v1"
    if shape not in root.get("$defs", {}):
        raise KeyError(f"ws.v1 has no $def '{shape}'")
    return _validator_for(root, f"#/$defs/{shape}", base_id)


def assert_api_conforms(shape: str, payload: Any) -> None:
    """Raise AssertionError with a readable message if ``payload`` violates api.v1 ``shape``."""
    errors = sorted(api_component_validator(shape).iter_errors(payload), key=lambda e: e.path)
    if errors:
        joined = "; ".join(f"{list(e.absolute_path)}: {e.message}" for e in errors)
        raise AssertionError(f"payload does not conform to api.v1 #/components/schemas/{shape}: {joined}")


def assert_ws_conforms(shape: str, payload: Any) -> None:
    """Raise AssertionError with a readable message if ``payload`` violates ws.v1 ``shape``."""
    errors = sorted(ws_def_validator(shape).iter_errors(payload), key=lambda e: e.path)
    if errors:
        joined = "; ".join(f"{list(e.absolute_path)}: {e.message}" for e in errors)
        raise AssertionError(f"frame does not conform to ws.v1 #/$defs/{shape}: {joined}")


def api_core_paths() -> list[tuple[str, str]]:
    """The CORE (path, method) pairs the api.v1 validate.mjs asserts — re-read off the
    same frozen document so this harness drives EXACTLY the sealed surface."""
    return [
        ("/bots", "get"),
        ("/bots", "post"),
        ("/bots/status", "get"),
        ("/bots/{platform}/{native_meeting_id}", "delete"),
        ("/bots/{platform}/{native_meeting_id}/config", "put"),
        ("/bots/{platform}/{native_meeting_id}/speak", "post"),
        ("/transcripts/{platform}/{native_meeting_id}", "get"),
        ("/recordings", "get"),
        ("/recordings/{recording_id}", "get"),
        ("/meetings", "get"),
    ]
