"""Privacy-safe service facts derived at the meeting lifecycle boundary."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional


#: The contract a new run is admitted and rated under (ADR 0052 §10). ``2026-11-01`` adds the
#: pod-start time, the product's outcome grade, and keeps departure for never-admitted runs: bot
#: time is billed from pod start until the bot leaves, admitted or not, and a run graded ``ours``
#: is never billed. Billing accepts both versions before this one ships (vexa-platform rating).
LIFECYCLE_CONTRACT_VERSION = "2026-11-01"
LIFECYCLE_CONTRACT_VERSION_V1 = "2026-07-28"
SUPPORTED_LIFECYCLE_CONTRACT_VERSIONS = frozenset({
    LIFECYCLE_CONTRACT_VERSION_V1,
    LIFECYCLE_CONTRACT_VERSION,
})
_PROVIDERS = frozenset({"vexa", "customer", "none"})
_OUTCOME_CLASSES = frozenset({"ours", "host_or_platform", "user", "normal", "unknown"})


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


def _run_contract_version(data: Dict[str, Any]) -> str:
    """The version this run was admitted under, frozen at spawn with the service-authority admit.

    A run admitted before an upgrade settles under the version its admit decision recorded, so the
    billing settlement (which requires the two to match) never sees a mixed pair. Runs with no
    frozen version (no service authority configured) use the current contract."""
    authority = data.get("service_authority")
    if isinstance(authority, dict):
        frozen = authority.get("lifecycle_contract_version")
        if frozen in SUPPORTED_LIFECYCLE_CONTRACT_VERSIONS:
            return frozen
    return LIFECYCLE_CONTRACT_VERSION


def _iso_utc(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return value.strip()


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
    version = _run_contract_version(data)
    pod_start = version == LIFECYCLE_CONTRACT_VERSION
    if admitted_transition is None:
        bot_outcome = "never_admitted"
        if not pod_start:
            departed_at = None
        elif departed_at is None:
            # Bot time runs to departure for never-admitted runs too. A terminal with no producer
            # time (a reconcile-driven reap) is only safe to carry when the run is never billed
            # anyway: graded `ours`, its receiver time is reported and nothing is charged by it.
            # Any other class stays unresolved rather than be billed from a receiver clock.
            if data.get("outcome_class") != "ours" or not terminal_transition:
                return None
            receiver_time = terminal_transition.get("timestamp")
            if not isinstance(receiver_time, str):
                return None
            departed_at = receiver_time
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

    provenance: Dict[str, Any] = {
        "bot_admitted_at": admitted_at,
        "bot_departed_at": departed_at,
        "bot_outcome": bot_outcome,
        "transcription_provider": provider,
        "transcription_outcome": transcription_outcome,
        "lifecycle_contract_version": version,
    }
    if not pod_start:
        return provenance

    # 2026-11-01: the producer's pod-start time and the product's grade. A run missing either is
    # unresolved downstream (never billed from a guess): a bot older than this contract sends no
    # pod start, exactly as a legacy bot without producer timestamps stays unresolved above.
    pod_started_at = _iso_utc(data.get("pod_started_at"))
    outcome_class = data.get("outcome_class")
    if pod_started_at is None or outcome_class not in _OUTCOME_CLASSES:
        return None
    outcome_reason = data.get("outcome_reason")
    provenance["pod_started_at"] = pod_started_at
    provenance["outcome_class"] = outcome_class
    provenance["outcome_reason"] = outcome_reason if isinstance(outcome_reason, str) else None
    return provenance
