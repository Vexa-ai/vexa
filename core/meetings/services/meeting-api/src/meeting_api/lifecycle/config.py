"""The mid-call bot-config command — ``PUT /bots/{platform}/{native_meeting_id}/config`` (api.v1).

The sealed operation says "Updates the language and/or task for an active bot. Sends command via
Bot Manager." Its mechanism is the SAME command bus the stop path uses: an ``acts.v1``
``Reconfigure`` published on ``bot_commands:meeting:{meeting_id}``, which the running bot applies to
its live STT config so the NEXT transcription request carries the new language/task.

Two halves live here, both pure so the route is a thin HTTP wrapper (P2):

  * **reconfigure_command_payload(...)** — the act body, omitting fields the caller did not send
    (an absent field means "leave it alone"; an explicit ``null`` means "clear it").
  * **conforms(act)** — validation against the SEALED acts.v1 schema, loaded BY PATH (P8), so a
    malformed command is refused HERE and never reaches a bot that would silently ignore it.

The channel itself is ``lifecycle.stop.leave_command_channel`` — one bus, one channel function, so
a rename cannot split the stop path from the config path.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import jsonschema
from referencing import Registry, Resource

from .stop import leave_command_channel

#: The command bus channel — shared with the stop path (acts.v1 rides ONE channel per meeting).
reconfigure_command_channel = leave_command_channel


def _load_acts_schema() -> dict:
    """Locate the sealed acts.v1 schema by walking up to the monorepo root (the P8 seam)."""
    rel = Path("meetings") / "contracts" / "acts.v1" / "acts.schema.json"
    for parent in Path(__file__).resolve().parents:
        candidate = parent / rel
        if candidate.is_file():
            return json.loads(candidate.read_text())
    raise FileNotFoundError(f"monorepo root with {rel} not found")


_SCHEMA = _load_acts_schema()
_REGISTRY = Registry().with_resource(_SCHEMA["$id"], Resource.from_contents(_SCHEMA))


def conforms(obj: Dict[str, Any], shape: str = "Reconfigure") -> None:
    """Validate `obj` against ``acts.v1#/$defs/<shape>``. Raises ``ValidationError``."""
    jsonschema.Draft202012Validator(
        {"$ref": f"{_SCHEMA['$id']}#/$defs/{shape}"}, registry=_REGISTRY
    ).validate(obj)


#: The config fields the sealed operation names, plus the contract's third (accepted, inert —
#: nothing consumes ``allowedLanguages`` in the bot today; see the route docstring).
CONFIG_FIELDS = ("language", "task", "allowedLanguages")


def reconfigure_command_payload(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Build the acts.v1 ``Reconfigure`` act from the fields the caller actually SENT.

    `fields` carries only keys present in the request body — an absent key is not the same as a
    ``null`` one: absent means "leave this alone", ``null`` means "clear it back to the default".
    Collapsing the two would make ``{"task":"translate"}`` silently unpin the language.
    """
    act: Dict[str, Any] = {"action": "reconfigure"}
    for name in CONFIG_FIELDS:
        if name in fields:
            act[name] = fields[name]
    return act


def persisted_config(act: Dict[str, Any]) -> Dict[str, Any]:
    """The subset of the act that belongs on the meeting record, so ``GET /bots``/``/meetings``
    report the config the bot is actually running rather than the one it was spawned with.

    ``allowedLanguages`` is NOT persisted: nothing consumes it, and a stored value would read as a
    constraint the stack enforces.
    """
    out: Dict[str, Any] = {}
    for name in ("language", "task"):
        if name in act:
            out[name] = act[name]
    return out


def missing_config_fields(fields: Dict[str, Any]) -> bool:
    """True when the body names no config field at all — a command that commands nothing."""
    return not any(name in fields for name in CONFIG_FIELDS)


def merged_config(current: Optional[Dict[str, Any]], applied: Dict[str, Any]) -> Dict[str, Any]:
    """The config the bot is running after this command — the record's stored values overlaid with
    what was just applied. Returned to the caller so the 202 says what is now in force, not merely
    what was asked for."""
    out = {k: v for k, v in (current or {}).items() if k in ("language", "task")}
    out.update(applied)
    return out
