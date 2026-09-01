"""Sealed-contract loaders + validators (the seam, P8 — by path, not import).

Mirrors meeting-api's ``bot_spawn/invocation.py`` loader pattern: walk up from this file to find
each ``meetings/contracts/<name>.v1/<name>.schema.json``, so this package can never drift from the
contracts it speaks — without a forbidden cross-package ``src`` import (gate:isolation-py forbids
importing another service's package directly; loading its published schema BY PATH is the seam).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import jsonschema
from referencing import Registry, Resource


def _load_schema(rel: Path) -> dict:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / rel
        if candidate.is_file():
            return json.loads(candidate.read_text())
    raise FileNotFoundError(f"sealed contract not found by path: {rel}")


_INVOCATION_SCHEMA = _load_schema(Path("meetings") / "contracts" / "invocation.v1" / "invocation.schema.json")
_TRANSCRIPT_SCHEMA = _load_schema(Path("meetings") / "contracts" / "transcript.v1" / "transcript.schema.json")
_LIFECYCLE_SCHEMA = _load_schema(Path("meetings") / "contracts" / "lifecycle.v1" / "lifecycle.schema.json")
_ACTS_SCHEMA = _load_schema(Path("meetings") / "contracts" / "acts.v1" / "acts.schema.json")

_REGISTRIES = {
    schema["$id"]: Registry().with_resource(schema["$id"], Resource.from_contents(schema))
    for schema in (_INVOCATION_SCHEMA, _TRANSCRIPT_SCHEMA, _LIFECYCLE_SCHEMA, _ACTS_SCHEMA)
}

# The spawnable-platform set this package must accept — read from the schema itself (mirrors
# bot_spawn/invocation.py's SPAWNABLE_PLATFORMS) so it can never drift from the sealed enum.
SPAWNABLE_PLATFORMS = frozenset(_INVOCATION_SCHEMA["$defs"]["Platform"]["enum"])

# The known acts.v1 verbs, read from the schema itself (mirrors SPAWNABLE_PLATFORMS above) so
# parse_act's leniency below can never drift from the sealed enum.
_ACT_ACTIONS = frozenset(_ACTS_SCHEMA["$defs"]["ActAction"]["enum"])


def _conforms(obj: Any, schema: dict, shape: str) -> None:
    registry = _REGISTRIES[schema["$id"]]
    jsonschema.Draft202012Validator({"$ref": f"{schema['$id']}#/$defs/{shape}"}, registry=registry).validate(obj)


def conforms_invocation(obj: dict) -> None:
    """Validate ``obj`` against ``invocation.v1#/$defs/Invocation`` (raises on non-conformance)."""
    _conforms(obj, _INVOCATION_SCHEMA, "Invocation")


def conforms_transcript_segment(obj: dict) -> None:
    """Validate ``obj`` against ``transcript.v1#/$defs/TranscriptSegment``."""
    _conforms(obj, _TRANSCRIPT_SCHEMA, "TranscriptSegment")


def conforms_lifecycle_event(obj: dict) -> None:
    """Validate ``obj`` against ``lifecycle.v1#/$defs/LifecycleEvent``."""
    _conforms(obj, _LIFECYCLE_SCHEMA, "LifecycleEvent")


def parse_act(raw: Any) -> Optional[dict]:
    """Recognize ``raw`` as an acts.v1 command: ``action`` must be a known acts.v1 verb.

    Deliberately NOT full jsonschema validation against ``acts.v1#/$defs/Act`` (the strict
    per-shape ``oneOf``, each with ``additionalProperties: false``). This mirrors the TS
    reference adapter's ``parseAct`` (``services/bot/src/contracts.ts``), which only checks that
    ``action`` is a known string rather than validating the whole shape.

    This leniency is required, not stylistic: the real leave-command producer,
    meeting-api's ``lifecycle/stop.py`` ``leave_command_payload``, sends
    ``{"action": "leave", "meeting_id": <id>}``, while the sealed acts.v1 schema's ``Leave``
    variant forbids ``meeting_id`` (``additionalProperties: false``, no such property). That is a
    genuine producer/schema mismatch upstream (tracked separately, since the schema is sealed and
    the producer is shared beyond this lane, so neither is fixed here). Strict validation here
    would silently drop every real leave command and the bot could never be gracefully stopped.

    Off-contract / unrecognized-action input is IGNORED, returning ``None``, per the acts.v1
    README's forward-compatibility promise ("unknown actions are ignored"), not raised.
    """
    if not isinstance(raw, dict):
        return None
    action = raw.get("action")
    if not isinstance(action, str) or action not in _ACT_ACTIONS:
        return None
    return raw
