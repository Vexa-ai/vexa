"""Producer-owned service facts for the hosted billing boundary (#984)."""

from meeting_api.lifecycle.provenance import build_service_provenance


def meeting(
    *,
    provider="vexa",
    segments=2,
    transitions=None,
    stt_fault=None,
):
    data = {
        "transcribe_enabled": provider != "none",
        "transcription_provider": provider,
        "segments_captured": segments,
        "status_transition": transitions
        or [
            {"to": "joining", "timestamp": "2026-07-28T10:00:00Z", "timestamp_source": "producer"},
            {"to": "active", "timestamp": "2026-07-28T10:05:00Z", "timestamp_source": "producer"},
            {"to": "completed", "timestamp": "2026-07-28T10:30:00Z", "timestamp_source": "producer"},
        ],
        # Seed secrets in the producer record: no value may cross the public fact block.
        "transcription_service_url": "https://secret.example.test/v1/audio",
        "transcription_service_token": "do-not-serialize",
    }
    if stt_fault is not None:
        data["stt_fault"] = stt_fault
    return {"status": transitions[-1]["to"] if transitions else "completed", "data": data}


def test_vexa_service_uses_admitted_to_departed_runtime_and_observed_output():
    assert build_service_provenance(meeting()) == {
        "bot_admitted_at": "2026-07-28T10:05:00Z",
        "bot_departed_at": "2026-07-28T10:30:00Z",
        "bot_outcome": "served",
        "transcription_provider": "vexa",
        "transcription_outcome": "served",
        "lifecycle_contract_version": "2026-07-28",
    }


def test_customer_endpoint_is_distinct_and_never_leaks_configuration():
    provenance = build_service_provenance(meeting(provider="customer"))

    assert provenance["transcription_provider"] == "customer"
    serialized = repr(provenance)
    assert "secret.example.test" not in serialized
    assert "do-not-serialize" not in serialized


def test_never_admitted_is_explicit_and_has_no_runtime():
    transitions = [
        {"to": "joining", "timestamp": "2026-07-28T10:00:00Z", "timestamp_source": "producer"},
        {"to": "awaiting_admission", "timestamp": "2026-07-28T10:01:00Z", "timestamp_source": "producer"},
        {"to": "failed", "timestamp": "2026-07-28T10:10:00Z", "timestamp_source": "producer"},
    ]

    provenance = build_service_provenance(
        meeting(provider="vexa", segments=0, transitions=transitions),
    )

    assert provenance["bot_outcome"] == "never_admitted"
    assert provenance["bot_admitted_at"] is None
    assert provenance["bot_departed_at"] is None
    assert provenance["transcription_outcome"] == "no_output"


def test_disabled_and_failed_transcription_are_not_reported_as_served():
    disabled = build_service_provenance(meeting(provider="none", segments=0))
    failed = build_service_provenance(
        meeting(provider="vexa", segments=0, stt_fault={"total": 2}),
    )

    assert disabled["transcription_outcome"] == "disabled"
    assert failed["transcription_outcome"] == "failed"


def test_legacy_meeting_without_frozen_provider_stays_unresolved():
    legacy = meeting()
    del legacy["data"]["transcription_provider"]

    assert build_service_provenance(legacy) is None


def test_admitted_legacy_session_without_producer_timestamps_stays_unresolved():
    legacy = meeting()
    for transition in legacy["data"]["status_transition"]:
        transition["timestamp_source"] = "receiver"

    assert build_service_provenance(legacy) is None


def test_continued_meeting_uses_latest_session_runtime_not_prior_cycle():
    continued = meeting(
        transitions=[
            {"to": "active", "timestamp": "2026-07-28T09:00:00Z", "timestamp_source": "producer"},
            {"to": "completed", "timestamp": "2026-07-28T09:30:00Z", "timestamp_source": "producer"},
            {"to": "joining", "timestamp": "2026-07-28T10:00:00Z", "timestamp_source": "producer"},
            {"to": "active", "timestamp": "2026-07-28T10:05:00Z", "timestamp_source": "producer"},
            {"to": "completed", "timestamp": "2026-07-28T10:20:00Z", "timestamp_source": "producer"},
        ],
    )

    provenance = build_service_provenance(continued)

    assert provenance["bot_admitted_at"] == "2026-07-28T10:05:00Z"
    assert provenance["bot_departed_at"] == "2026-07-28T10:20:00Z"


def test_continued_meeting_never_admitted_does_not_reuse_prior_runtime():
    continued = meeting(
        segments=0,
        transitions=[
            {"to": "active", "timestamp": "2026-07-28T09:00:00Z", "timestamp_source": "producer"},
            {"to": "completed", "timestamp": "2026-07-28T09:30:00Z", "timestamp_source": "producer"},
            {"to": "joining", "timestamp": "2026-07-28T10:00:00Z", "timestamp_source": "producer"},
            {"to": "failed", "timestamp": "2026-07-28T10:02:00Z", "timestamp_source": "producer"},
        ],
    )

    provenance = build_service_provenance(continued)

    assert provenance["bot_outcome"] == "never_admitted"
    assert provenance["bot_admitted_at"] is None
    assert provenance["bot_departed_at"] is None
