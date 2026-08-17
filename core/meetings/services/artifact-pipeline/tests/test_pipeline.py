"""The spine end to end, on the real gate.

Every test here runs the shipped ``presend_gate`` module. The gateway is faked at the
transport and the delivery is a recording fake; the decision that matters — who may be
written to — is never faked.
"""

from __future__ import annotations

from conftest import RecordingDelivery, monologue, record_payload, transport_for
from vexa_artifact_pipeline import (
    ArtifactPipeline,
    CompletedMeeting,
    DeliveryResult,
    FileDelivery,
    HttpMeetingGateway,
    JsonlRunLog,
    ListSource,
    MemoryRunLog,
    RosterDirectory,
)

CREATOR = "Dmitry Grankin"
BOOK = {
    "Dmitry Grankin": "dmitry@example.test",
    "Marvin Hanke": "marvin@example.test",
    "Laiba Warraich": "laiba@example.test",
}


def build(records, *, delivery=None, run_log=None, address_book=BOOK, include_speakers=True, **kwargs):
    return ArtifactPipeline(
        gateway=HttpMeetingGateway("http://meetings.test", transport=transport_for(records)),
        directory=RosterDirectory(address_book=address_book, include_speakers=include_speakers),
        delivery=delivery or RecordingDelivery(),
        run_log=run_log or MemoryRunLog(),
        **kwargs,
    )


def trigger(meeting_id: str, **kwargs) -> CompletedMeeting:
    base = dict(creator=CREATOR, creator_email="dmitry@example.test", bot_names=("Vexa test",))
    base.update(kwargs)
    return CompletedMeeting(meeting_id=meeting_id, **base)


# ── the happy path ────────────────────────────────────────────────────────────────────


def test_a_real_meeting_fans_out_one_artifact_per_participant():
    records = {"12615": record_payload(12615, participants=["Dmitry Grankin", "Hanke, Marvin", "Laiba Warraich"])}
    delivery = RecordingDelivery()
    result = build(records, delivery=delivery).run(trigger("12615"))

    assert result.verdict == "send"
    assert len(result.artifacts) == 3
    assert sorted(delivery.identities) == ["dmitry@example.test", "laiba@example.test", "marvin@example.test"]
    assert {a.recipient.display_name for a in result.artifacts} == {
        "Dmitry Grankin",
        "Hanke, Marvin",
        "Laiba Warraich",
    }


def test_each_artifact_is_addressed_to_its_own_recipient():
    records = {"12615": record_payload(12615, participants=["Dmitry Grankin", "Marvin Hanke"])}
    delivery = RecordingDelivery()
    build(records, delivery=delivery, include_speakers=False).run(trigger("12615"))

    assert len(delivery.sent) == 2
    for identity, artifact in delivery.sent:
        assert artifact.recipient.identity == identity
        assert f"**To:** {artifact.recipient.display_name}" in artifact.to_markdown()


def test_the_run_log_line_carries_verdict_signals_recipients_and_outcomes():
    records = {"12615": record_payload(12615, participants=["Dmitry Grankin", "Marvin Hanke"])}
    log = MemoryRunLog()
    build(records, run_log=log, include_speakers=False).run(trigger("12615"))

    entry = log.records[-1]
    assert entry["meeting_id"] == "12615"
    assert entry["verdict"] == "send"
    assert entry["reasons"]
    assert entry["signals"]["substantive_speaker_count"] >= 2
    assert entry["roster_source"] == "observed"
    assert len(entry["recipients"]) == len(entry["outcomes"]) == 2
    assert {o["status"] for o in entry["outcomes"]} == {"sent"}
    assert entry["outcomes"][0]["artifact"]["schema_version"] == 1


# ── the gate governs, and nothing bypasses it ─────────────────────────────────────────


def test_a_non_meeting_produces_zero_artifacts_and_zero_sends():
    """One voice, roster naming only the account owner: not a meeting. Nothing is rendered —
    the gate runs BEFORE the renderer, so no artifact for it ever exists to leak."""
    records = {"12085": record_payload(12085, segments=monologue("Dmitry Grankin"), participants=["Dmtiry Grankin"])}
    delivery = RecordingDelivery()
    result = build(records, delivery=delivery).run(trigger("12085"))

    assert result.verdict == "suppress"
    assert result.recipients == ()
    assert result.artifacts == ()
    assert delivery.sent == []
    assert {o.status for o in result.outcomes} == {DeliveryResult.SUPPRESSED}


def test_a_hold_reaches_the_creator_and_nobody_else():
    """A single-voice record with a roster naming other people: the gate cannot confirm a
    conversation, so it holds for the creator rather than broadcasting."""
    records = {
        "26049": record_payload(
            26049, segments=monologue("Dmitry Grankin"), participants=["Dmitry Grankin", "Marvin Hanke"]
        )
    }
    delivery = RecordingDelivery()
    result = build(records, delivery=delivery).run(trigger("26049"))

    assert result.verdict == "hold_for_creator"
    assert delivery.identities == ["dmitry@example.test"]
    assert [o.status for o in result.outcomes if o.recipient.display_name == "Marvin Hanke"] == [
        DeliveryResult.SUPPRESSED
    ]


def test_the_invite_roster_is_passed_to_the_gate_as_the_authoritative_one():
    records = {"12615": record_payload(12615, participants=[])}
    result = build(records).run(
        trigger("12615", invite_participants=({"name": "Marvin Hanke", "email": "marvin@example.test"},))
    )
    assert result.roster_source == "invite"
    assert "marvin@example.test" in result.recipients


# ── the record's own id ───────────────────────────────────────────────────────────────


def test_the_id_used_everywhere_is_the_records_own_not_the_one_requested():
    """The corpus keys six records under a number the payload disagrees with. Using the
    requested id is exactly how a magic link ended up pointing at the wrong meeting."""
    records = {"5174": record_payload(5175, participants=["Dmitry Grankin", "Marvin Hanke"])}
    log = MemoryRunLog()
    result = build(records, run_log=log, include_speakers=False).run(trigger("5174"))

    assert result.requested_id == "5174"
    assert result.meeting_id == "5175"
    assert not result.id_matches_request
    assert all(a.meeting_id == "5175" for a in result.artifacts)
    assert all("**record:** 5175 · " in a.to_markdown() for a in result.artifacts)
    assert log.records[-1]["id_matches_request"] is False


def test_idempotency_is_keyed_on_the_records_own_id_across_both_spellings():
    records = {"5174": record_payload(5175, participants=["Dmitry Grankin", "Marvin Hanke"])}
    log = MemoryRunLog()
    delivery = RecordingDelivery()
    pipeline = build(records, delivery=delivery, run_log=log, include_speakers=False)
    pipeline.run(trigger("5174"))
    second = pipeline.run(trigger("5175"))

    assert len(delivery.sent) == 2
    assert {o.status for o in second.outcomes} == {DeliveryResult.DUPLICATE}


# ── idempotency ───────────────────────────────────────────────────────────────────────


def test_a_second_run_delivers_nothing_twice():
    records = {"12615": record_payload(12615, participants=["Dmitry Grankin", "Marvin Hanke"])}
    delivery = RecordingDelivery()
    pipeline = build(records, delivery=delivery, include_speakers=False)
    first = pipeline.run(trigger("12615"))
    second = pipeline.run(trigger("12615"))

    assert {o.status for o in first.outcomes} == {DeliveryResult.SENT}
    assert {o.status for o in second.outcomes} == {DeliveryResult.DUPLICATE}
    assert len(delivery.sent) == 2  # two recipients, once each


def test_idempotency_survives_a_new_process_because_it_is_read_from_the_log(tmp_path):
    records = {"12615": record_payload(12615, participants=["Dmitry Grankin", "Marvin Hanke"])}
    path = tmp_path / "runs.jsonl"
    first_delivery, second_delivery = RecordingDelivery(), RecordingDelivery()
    build(records, delivery=first_delivery, run_log=JsonlRunLog(path), include_speakers=False).run(trigger("12615"))
    build(records, delivery=second_delivery, run_log=JsonlRunLog(path), include_speakers=False).run(trigger("12615"))

    assert len(first_delivery.sent) == 2
    assert second_delivery.sent == []
    assert len(path.read_text("utf-8").strip().splitlines()) == 2


def test_a_failed_delivery_is_retried_because_only_sent_is_terminal():
    records = {"12615": record_payload(12615, participants=["Dmitry Grankin", "Marvin Hanke"])}
    log = MemoryRunLog()
    failing = RecordingDelivery(fail={"marvin@example.test"})
    build(records, delivery=failing, run_log=log, include_speakers=False).run(trigger("12615"))
    retry = RecordingDelivery()
    second = build(records, delivery=retry, run_log=log, include_speakers=False).run(trigger("12615"))

    assert retry.identities == ["marvin@example.test"]
    statuses = {o.recipient.identity: o.status for o in second.outcomes}
    assert statuses["dmitry@example.test"] == DeliveryResult.DUPLICATE
    assert statuses["marvin@example.test"] == DeliveryResult.SENT


# ── addressability ────────────────────────────────────────────────────────────────────


def test_a_participant_with_no_address_is_recorded_not_silently_dropped():
    records = {"12615": record_payload(12615, participants=["Dmitry Grankin", "Karl Moll"])}
    delivery = RecordingDelivery()
    result = build(records, delivery=delivery, include_speakers=False).run(trigger("12615"))

    statuses = {o.recipient.display_name: o.status for o in result.outcomes}
    assert statuses["Karl Moll"] == DeliveryResult.NO_ADDRESS
    assert delivery.identities == ["dmitry@example.test"]
    assert any(a.recipient.display_name == "Karl Moll" for a in result.artifacts)


def test_a_sink_that_needs_no_address_writes_for_everyone(tmp_path):
    records = {"12615": record_payload(12615, participants=["Dmitry Grankin", "Karl Moll"])}
    result = build(
        records, delivery=FileDelivery(tmp_path), address_book={}, include_speakers=False
    ).run(trigger("12615"))

    assert {o.status for o in result.outcomes} == {DeliveryResult.SENT}
    assert sorted(p.name for p in (tmp_path / "12615").glob("*.md")) == ["dmitry-grankin.md", "karl-moll.md"]
    assert (tmp_path / "12615" / "karl-moll.json").exists()


# ── the trigger port ──────────────────────────────────────────────────────────────────


def test_draining_the_source_runs_every_meeting_it_holds():
    records = {
        "12615": record_payload(12615, participants=["Dmitry Grankin", "Marvin Hanke"]),
        "12085": record_payload(12085, segments=monologue("Dmitry Grankin"), participants=["Dmtiry Grankin"]),
    }
    pipeline = build(records, source=ListSource([trigger("12615"), trigger("12085")]))
    results = pipeline.drain()
    assert [r.verdict for r in results] == ["send", "suppress"]


def test_a_record_the_api_does_not_have_is_a_recorded_fact_not_a_crash():
    log = MemoryRunLog()
    result = build({}, run_log=log).run(trigger("99999"))
    assert result.found is False
    assert result.verdict == "unavailable"
    assert log.records[-1]["note"]


def test_an_empty_transcript_is_suppressed_with_the_reason_stated():
    records = {"12615": record_payload(12615, segments=[], participants=["Dmitry Grankin", "Marvin Hanke"])}
    result = build(records).run(trigger("12615"))
    assert result.verdict == "suppress"
    assert "empty_record" in result.reasons
