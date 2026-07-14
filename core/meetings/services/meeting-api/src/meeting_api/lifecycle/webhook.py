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

Typed events (webhook parity with the parent): ``build_typed_envelope`` additionally maps the
terminal-ish transitions to the contract's typed vocabulary — active → ``meeting.started``,
completed → ``meeting.completed`` (the post-meeting ``{meeting}`` envelope), failed →
``bot.failed`` — alongside (never instead of) ``meeting.status_change``.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import jsonschema
from referencing import Registry, Resource

from .machine import BotStatus, StatusChange

WEBHOOK_API_VERSION = "2026-03-01"

# FSM status → the TYPED webhook event that transition ALSO emits (webhook.v1 EventType).
# Mirrors the parent's STATUS_TO_EVENT (webhooks.py) plus the completion event the parent's
# post-meeting path owns: active → meeting.started, failed → bot.failed, completed →
# meeting.completed. Every transition still emits meeting.status_change (additive — the
# per-user event filter in webhooks/delivery.py opts subscribers in per event type).
TYPED_EVENT_BY_STATUS: Dict[BotStatus, str] = {
    BotStatus.ACTIVE: "meeting.started",
    BotStatus.COMPLETED: "meeting.completed",
    BotStatus.FAILED: "bot.failed",
}


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


def derive_event_id(connection_id: Any, event_type: str, new_status: Any) -> str:
    """#519: the ``event_id`` is the STABLE identity of a logical event, not a per-emission nonce.

    Delivery is at-least-once (the initial POST, the Redis retry-queue drain which reuses the stored
    envelope, a restart replay, a cross-replica race, an ambiguous timeout) — every one of these must
    present the SAME ``event_id`` so a receiver can dedupe on it (the #330 4×-billing class, where a
    fresh uuid4 per emission made four deliveries look like four distinct events). We derive it from
    exactly what makes the event unique: ``(connection_id, event_type, new_status)``. Two DIFFERENT
    logical events of one FSM advance (e.g. ``meeting.status_change`` + ``meeting.completed``) get
    different ids because ``event_type`` is in the key — correct: they ARE distinct events.

    32 hex chars = 128 bits, matching the sealed ``evt_<hex>`` wire shape (no schema change)."""
    key = f"{connection_id}|{event_type}|{new_status}"
    return "evt_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def _new_status_value(change: StatusChange) -> Any:
    return change.new_status.value if change.new_status is not None else None


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
    if meeting is None:
        meeting = _minimal_meeting_projection(change)
    envelope = {
        "event_id": event_id
        or derive_event_id(change.record.connection_id, "meeting.status_change", _new_status_value(change)),
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


def _minimal_meeting_projection(change: StatusChange) -> Dict[str, Any]:
    """The DB-free meeting block: `{connection_id, status, completion_reason, failure_stage, data}`
    built from the FSM record, so the eval can drive the builders with no DB."""
    rec = change.record
    return {
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


def typed_event_type(change: StatusChange) -> Optional[str]:
    """The TYPED webhook event this FSM advance also emits, or None for intermediate states.

    active → ``meeting.started``; completed → ``meeting.completed``; failed → ``bot.failed``.
    """
    return TYPED_EVENT_BY_STATUS.get(change.new_status)


def build_typed_envelope(
    change: StatusChange,
    *,
    meeting: Optional[Dict[str, Any]] = None,
    event_id: Optional[str] = None,
    created_at: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Wrap a ``StatusChange`` as the TYPED webhook.v1 ``Envelope`` its transition maps to.

    Returns None when the transition has no typed event (joining / awaiting_admission /
    needs_help — those only emit ``meeting.status_change``). Shapes follow the webhook.v1
    goldens (and the parent's ``webhooks.py``):

    * ``meeting.started`` / ``bot.failed`` — ``data = {meeting, status_change}`` with the
      parent's ``{from, to, reason, timestamp, transition_source}`` block,
    * ``meeting.completed`` — the post-meeting envelope: ``data = {meeting}`` only (the
      parent's ``send_completion_webhook``; no status_change block — golden
      ``Envelope.meeting-completed.json``).

    Validated against the frozen schema before it is returned (the seam, P8) — the schema's
    ``EventType`` enum already declares all three, so this is a code-only, additive change.
    """
    event_type = typed_event_type(change)
    if event_type is None:
        return None
    if meeting is None:
        meeting = _minimal_meeting_projection(change)
    ts = created_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    data: Dict[str, Any] = {"meeting": meeting}
    if event_type != "meeting.completed":
        data["status_change"] = {
            "from": change.old_status.value if change.old_status is not None else None,
            "to": change.new_status.value,
            "reason": change.reason,
            "timestamp": ts,
            "transition_source": change.transition_source.value,
        }
    envelope = {
        "event_id": event_id
        or derive_event_id(change.record.connection_id, event_type, _new_status_value(change)),
        "event_type": event_type,
        "api_version": WEBHOOK_API_VERSION,
        "created_at": ts,
        "data": data,
    }
    _conforms(envelope, "Envelope")
    return envelope
