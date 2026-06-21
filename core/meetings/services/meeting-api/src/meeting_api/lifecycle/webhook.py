"""Build the ``meeting.status_change`` webhook envelope (webhook.v1) from a FSM ``StatusChange``.

P3a — every bot lifecycle callback that advances the meeting FSM emits a
``meeting.status_change`` webhook. Its body is the parent's ``schedule_status_webhook_task``
payload: ``{old_status, new_status, reason, transition_source}`` where
``transition_source ∈ {user_stop, bot_callback, scheduler_timeout}``. The envelope is a sealed
``webhook.v1`` ``Envelope`` — validated against the frozen schema AT THE SEAM (P8, by path) so a
malformed payload never ships.

The envelope's ``data`` block is ``{meeting: {...}, status_change: {...}}`` — the same open
``data`` shape every ``meeting.*`` webhook carries (the schema leaves ``data`` unlocked for exactly
this). We DO NOT edit webhook.v1 (it is sealed/frozen); we conform to it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

import jsonschema
from referencing import Registry, Resource

from .machine import StatusChange

WEBHOOK_API_VERSION = "2026-03-01"


def _load_webhook_schema() -> dict:
    rel = Path("meetings") / "contracts" / "webhook.v1" / "webhook.schema.json"
    for parent in Path(__file__).resolve().parents:
        candidate = parent / rel
        if candidate.is_file():
            return json.loads(candidate.read_text())
    raise FileNotFoundError(f"sealed contract not found by path: {rel}")


_SCHEMA = _load_webhook_schema()
_REGISTRY = Registry().with_resource(_SCHEMA["$id"], Resource.from_contents(_SCHEMA))


def _conforms(obj: Dict[str, Any], shape: str) -> None:
    jsonschema.Draft202012Validator(
        {"$ref": f"{_SCHEMA['$id']}#/$defs/{shape}"}, registry=_REGISTRY
    ).validate(obj)


def build_status_change_envelope(
    change: StatusChange,
    *,
    meeting: Optional[Dict[str, Any]] = None,
    event_id: Optional[str] = None,
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Wrap a ``StatusChange`` as a sealed ``webhook.v1`` ``Envelope`` (``meeting.status_change``).

    ``meeting`` is the meeting projection the envelope carries (a ``MeetingResponse``-ish dict); if
    omitted, a minimal ``{connection_id, status, completion_reason, failure_stage}`` block is built
    from the record so the eval can drive this with no DB. The returned envelope is validated
    against the frozen schema before it is returned.
    """
    rec = change.record
    if meeting is None:
        meeting = {
            "connection_id": rec.connection_id,
            "status": rec.status.value if rec.status is not None else None,
            "completion_reason": (
                rec.completion_reason.value if rec.completion_reason is not None else None
            ),
            "failure_stage": (
                rec.failure_stage.value if rec.failure_stage is not None else None
            ),
            "data": rec.data,
        }
    envelope = {
        "event_id": event_id or f"evt_{uuid4().hex}",
        "event_type": "meeting.status_change",
        "api_version": WEBHOOK_API_VERSION,
        "created_at": created_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": {
            "meeting": meeting,
            "status_change": change.to_webhook_payload(),
        },
    }
    _conforms(envelope, "Envelope")
    return envelope
