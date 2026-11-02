"""Lifecycle contract 2026-11-01: bot time from pod start, the product's grade (ADR 0052 §10)."""

import pytest

from meeting_api.lifecycle.outcome import grade_terminal_meeting
from meeting_api.lifecycle.provenance import (
    LIFECYCLE_CONTRACT_VERSION,
    build_service_provenance,
)
from meeting_api.service_authority import ServiceAuthorityRequest

ADMITTED = [
    {"to": "joining", "timestamp": "2026-11-01T10:00:00Z", "timestamp_source": "producer"},
    {"to": "active", "timestamp": "2026-11-01T10:05:00Z", "timestamp_source": "producer"},
    {"to": "completed", "timestamp": "2026-11-01T10:30:00Z", "timestamp_source": "producer",
     "completion_reason": "stopped"},
]
LOBBY = [
    {"to": "joining", "timestamp": "2026-11-01T10:00:00Z", "timestamp_source": "producer"},
    {"to": "awaiting_admission", "timestamp": "2026-11-01T10:01:00Z", "timestamp_source": "producer"},
    {"to": "failed", "timestamp": "2026-11-01T10:11:00Z", "timestamp_source": "producer"},
]


def meeting(transitions=ADMITTED, *, segments=12, frozen=None, **extra):
    data = {
        "transcription_provider": "vexa",
        "segments_captured": segments,
        "status_transition": [dict(t) for t in transitions],
        "pod_started_at": "2026-11-01T09:50:00Z",
        "outcome_class": "normal",
        "outcome_reason": "delivered_stopped",
        **extra,
    }
    if frozen is not None:
        data["service_authority"] = {"lifecycle_contract_version": frozen}
    return {"status": transitions[-1]["to"], "created_at": "2026-11-01T09:00:00Z", "data": data}


def test_current_contract_is_2026_11_01():
    assert LIFECYCLE_CONTRACT_VERSION == "2026-11-01"


def test_admitted_run_carries_pod_start_and_grade():
    assert build_service_provenance(meeting()) == {
        "bot_admitted_at": "2026-11-01T10:05:00Z",
        "bot_departed_at": "2026-11-01T10:30:00Z",
        "bot_outcome": "served",
        "transcription_provider": "vexa",
        "transcription_outcome": "served",
        "lifecycle_contract_version": "2026-11-01",
        "pod_started_at": "2026-11-01T09:50:00Z",
        "outcome_class": "normal",
        "outcome_reason": "delivered_stopped",
    }


def test_never_admitted_run_keeps_departure():
    provenance = build_service_provenance(meeting(
        LOBBY, segments=0, outcome_class="host_or_platform", outcome_reason="not_admitted"))

    assert provenance["bot_outcome"] == "never_admitted"
    assert provenance["bot_admitted_at"] is None
    assert provenance["bot_departed_at"] == "2026-11-01T10:11:00Z"
    assert provenance["pod_started_at"] == "2026-11-01T09:50:00Z"
    assert provenance["transcription_outcome"] == "no_output"


def test_run_admitted_under_2026_07_28_settles_under_it():
    provenance = build_service_provenance(meeting(frozen="2026-07-28"))

    assert provenance["lifecycle_contract_version"] == "2026-07-28"
    assert "pod_started_at" not in provenance
    assert "outcome_class" not in provenance


@pytest.mark.parametrize("missing", ["pod_started_at", "outcome_class"])
def test_missing_pod_start_or_grade_stays_unresolved(missing):
    row = meeting()
    del row["data"][missing]

    assert build_service_provenance(row) is None


def test_never_admitted_without_producer_departure_stays_unresolved():
    lobby = [dict(t) for t in LOBBY]
    lobby[-1]["timestamp_source"] = "receiver"

    assert build_service_provenance(meeting(lobby, segments=0)) is None


def test_unparseable_pod_start_stays_unresolved():
    assert build_service_provenance(meeting(pod_started_at="soon")) is None


@pytest.mark.parametrize(
    ("transitions", "extra", "want"),
    [
        (ADMITTED, {"completion_reason": "stopped"}, ("normal", "delivered_stopped")),
        (LOBBY, {"completion_reason": "awaiting_admission_timeout", "failure_stage": "awaiting_admission"},
         ("host_or_platform", "not_admitted")),
        ([*ADMITTED[:2], {"to": "failed", "timestamp": "2026-11-01T10:30:00Z",
                          "timestamp_source": "producer"}],
         {"failure_stage": "active"}, ("ours", "died_in_meeting")),
    ],
)
def test_terminal_grade_comes_from_the_product_table(transitions, extra, want):
    row = meeting(transitions, segments=12 if want[0] == "normal" else 0, **extra)
    for key in ("outcome_class", "outcome_reason"):
        row["data"].pop(key)

    assert tuple(grade_terminal_meeting(row)) == want


def test_service_authority_continuation_keeps_the_admitted_version():
    from datetime import datetime, timezone

    at = datetime(2026, 11, 1, 10, 5, tzinfo=timezone.utc)
    request = ServiceAuthorityRequest.continuation(
        user_id=1, request_id="r", service_identity="s", transcription_provider="vexa",
        active_concurrency=1, admitted_at=at, boundary_at=at,
        lifecycle_contract_version="2026-07-28",
    )
    assert request.to_wire()["lifecycle_contract_version"] == "2026-07-28"
    assert ServiceAuthorityRequest.admit(
        user_id=1, request_id="r", service_identity="s", transcription_provider="vexa",
        active_concurrency=0,
    ).lifecycle_contract_version == "2026-11-01"
    with pytest.raises(ValueError):
        ServiceAuthorityRequest.continuation(
            user_id=1, request_id="r", service_identity="s", transcription_provider="vexa",
            active_concurrency=1, admitted_at=at, boundary_at=at,
            lifecycle_contract_version="2099-01-01",
        )


def test_machine_records_the_producer_pod_start():
    from meeting_api.lifecycle import LifecycleSink

    sink = LifecycleSink()
    sink.apply({"connection_id": "c1", "status": "joining", "timestamp": "2026-11-01T10:00:00Z",
                "pod_started_at": "2026-11-01T09:50:00.000+00:00"})
    rec = sink.apply({"connection_id": "c1", "status": "active", "timestamp": "2026-11-01T10:05:00Z",
                      "pod_started_at": "garbage"})

    assert rec.data["pod_started_at"] == "2026-11-01T09:50:00.000+00:00"


def test_machine_omits_pod_start_when_the_bot_sends_none():
    from meeting_api.lifecycle import LifecycleSink

    rec = LifecycleSink().apply({"connection_id": "c2", "status": "joining"})

    assert "pod_started_at" not in rec.data


def test_reaped_lobby_run_graded_ours_carries_receiver_departure_and_is_never_billed():
    lobby = [dict(t) for t in LOBBY]
    lobby[-1]["timestamp_source"] = "receiver"
    provenance = build_service_provenance(meeting(
        lobby, segments=0, outcome_class="ours", outcome_reason="workload_lost"))

    assert provenance["bot_outcome"] == "never_admitted"
    assert provenance["bot_departed_at"] == "2026-11-01T10:11:00Z"
    assert provenance["outcome_class"] == "ours"


def test_failed_before_admission_with_producer_time_is_rated():
    joining_only = [LOBBY[0], {"to": "failed", "timestamp": "2026-11-01T10:02:00Z",
                               "timestamp_source": "producer"}]
    provenance = build_service_provenance(meeting(
        joining_only, segments=0, outcome_class="host_or_platform",
        outcome_reason="tenant_requires_sign_in"))

    assert provenance["bot_outcome"] == "never_admitted"
    assert provenance["bot_departed_at"] == "2026-11-01T10:02:00Z"
    assert provenance["pod_started_at"] == "2026-11-01T09:50:00Z"
