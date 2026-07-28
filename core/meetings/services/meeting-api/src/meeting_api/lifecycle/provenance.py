"""Privacy-safe service facts derived at the meeting lifecycle boundary."""
from __future__ import annotations

from typing import Any, Dict, Optional


LIFECYCLE_CONTRACT_VERSION = "2026-07-28"
_PROVIDERS = frozenset({"vexa", "customer", "none"})


def _latest_session(transitions: list[dict]) -> list[dict]:
    """Return only the last bot run carried by a continued meeting row."""
    for index in range(len(transitions) - 1, -1, -1):
        if transitions[index].get("to") == "joining":
            return transitions[index:]
    return transitions


def _transition(transitions: list[dict], status: str) -> Optional[dict]:
    for transition in reversed(transitions):
        if transition.get("to") == status:
            return transition
    return None


def _producer_time(transition: Optional[dict]) -> Optional[str]:
    if not transition or transition.get("timestamp_source") != "producer":
        return None
    timestamp = transition.get("timestamp")
    return timestamp if isinstance(timestamp, str) else None


def build_service_provenance(meeting: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return facts sufficient to rate delivered service, or ``None`` for legacy custody.

    Provider ownership is frozen at spawn. It is deliberately never reconstructed from a URL,
    token, current user setting, or the old ``transcribe_enabled`` intent flag. A meeting without
    that frozen discriminator predates this contract and remains unresolved downstream.
    """
    data = meeting.get("data") if isinstance(meeting.get("data"), dict) else {}
    provider = data.get("transcription_provider")
    if provider not in _PROVIDERS:
        return None

    transitions = data.get("status_transition")
    if not isinstance(transitions, list):
        transitions = []
    transitions = _latest_session(
        [item for item in transitions if isinstance(item, dict)],
    )

    admitted_transition = _transition(transitions, "active")
    admitted_at = _producer_time(admitted_transition)
    terminal_status = meeting.get("status")
    terminal_transition = (
        _transition(transitions, terminal_status)
        if terminal_status in ("completed", "failed")
        else None
    )
    departed_at = _producer_time(terminal_transition)
    if admitted_transition is None:
        bot_outcome = "never_admitted"
        departed_at = None
    elif admitted_at is None or departed_at is None:
        # A mixed-version bot can omit producer time. Receiver time is operationally useful but
        # retry latency makes it unfit for rating, so an admitted legacy session stays unresolved.
        return None
    elif terminal_status == "failed":
        bot_outcome = "failed"
    else:
        bot_outcome = "served"

    if provider == "none":
        transcription_outcome = "disabled"
    elif data.get("stt_fault") or data.get("transcript_finalize_failed"):
        transcription_outcome = "failed"
    elif int(data.get("segments_captured") or 0) > 0:
        transcription_outcome = "served"
    else:
        transcription_outcome = "no_output"

    return {
        "bot_admitted_at": admitted_at,
        "bot_departed_at": departed_at,
        "bot_outcome": bot_outcome,
        "transcription_provider": provider,
        "transcription_outcome": transcription_outcome,
        "lifecycle_contract_version": LIFECYCLE_CONTRACT_VERSION,
    }
