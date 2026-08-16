"""Branch-by-branch proof of the three outcomes."""

from __future__ import annotations

import pytest
from conftest import DOMESTIC, blocks, conversation, record

from presend_gate import Outcome, Policy, evaluate, gate, measure, route_recipients
from presend_gate.record import Segment, from_transcript_payload
from presend_gate.signals import same_person

CREATOR = "Dmitry Grankin"


# ── send ──────────────────────────────────────────────────────────────────────────


def test_two_party_conversation_sends():
    rec = record(
        conversation([CREATOR, "Marvin Hanke"], 400),
        roster=(CREATOR, "Marvin Hanke"),
        roster_source="invite",
        creator=CREATOR,
    )
    v = evaluate(rec)
    assert v.outcome is Outcome.SEND
    assert "dialogue_confirmed" in v.reasons
    assert v.may_broadcast


def test_group_meeting_with_a_bystander_sends():
    segs = list(conversation([CREATOR, "Marvin Hanke", "Laiba Warraich"], 300))
    segs.append(Segment(speaker="Tobias Hutterer", text="Thanks, bye.", start=1500, end=1502, language="en"))
    rec = record(tuple(segs), roster=(CREATOR, "Marvin Hanke", "Laiba Warraich", "Tobias Hutterer"),
                 roster_source="invite", creator=CREATOR)
    v = evaluate(rec)
    assert v.outcome is Outcome.SEND
    # The bystander is measured but is not what makes it a meeting.
    assert v.signals.speaker_count == 4
    assert v.signals.substantive_speaker_count == 3


def test_one_soft_flag_alone_does_not_hold_a_real_meeting():
    """A bot that sat through long silences is still in a real meeting."""
    rec = record(
        conversation([CREATOR, "Alexey Rogov"], 300),
        roster=(CREATOR, "Alexey Rogov"),
        roster_source="observed",
        creator=CREATOR,
        wall_seconds=300 * 5 / 0.23,  # 23% speech density — sparse, and genuine
    )
    v = evaluate(rec)
    assert v.soft_flags == ("sparse_speech",)
    assert v.outcome is Outcome.SEND


# ── hold_for_creator ──────────────────────────────────────────────────────────────


def test_collapsed_attribution_holds_rather_than_suppresses():
    """A real meeting whose attribution fell back to one label.

    Structurally identical to a monologue — so it must NOT send. But with no roster to
    contradict it we have no evidence there was no second party, so it must not be
    suppressed either. This is the case that decides the whole hold/suppress split.
    """
    rec = record(conversation([CREATOR], 350), roster=(), roster_source="none", creator=CREATOR)
    v = evaluate(rec)
    assert v.outcome is Outcome.HOLD_FOR_CREATOR
    assert v.reasons == ("no_dialogue_structure",)


def test_sensitive_context_holds_even_when_the_conversation_is_real():
    """Two people genuinely talking — about the school run. Never broadcast."""
    rec = record(
        conversation([CREATOR, "Anna"], 200, lines=DOMESTIC, language="ru"),
        roster=(CREATOR, "Anna"),
        roster_source="invite",
        creator=CREATOR,
    )
    v = evaluate(rec)
    assert v.sensitive_context is True
    assert v.outcome is Outcome.HOLD_FOR_CREATOR
    assert "sensitive_context" in v.reasons


def test_playback_holds_when_the_bot_name_is_unknown():
    """Degraded mode: the same record, without telling the gate what the bot is called.

    It loses the `suppress` evidence but must never fall through to `send`.
    """
    segs = blocks(["Vexa test (Unverified)", CREATOR], 150)
    rec = record(segs, roster=(), roster_source="none", creator=CREATOR, bot_names=())
    v = evaluate(rec)
    assert v.outcome is Outcome.HOLD_FOR_CREATOR
    assert v.signals.bot_speaker_present is False


def test_two_soft_flags_hold_an_otherwise_conversational_record():
    rec = record(
        conversation([CREATOR, "Someone"], 300, languages=["ru", "en", "pt", "es"]),
        roster=(CREATOR, "Someone"),
        roster_source="observed",
        creator=CREATOR,
        wall_seconds=300 * 5 / 0.2,
    )
    v = evaluate(rec)
    assert set(v.soft_flags) >= {"sparse_speech", "language_churn"}
    assert v.outcome is Outcome.HOLD_FOR_CREATOR
    assert "soft_risk" in v.reasons


def test_single_substantive_speaker_holds():
    """Interleaved, but the second voice is three seconds of "mm-hm" across an hour."""
    segs = list(conversation([CREATOR], 300))
    for i in range(0, 300, 20):
        segs.insert(i, Segment(speaker="Guest", text="mm-hm", start=i * 5, end=i * 5 + 0.2, language="en"))
    rec = record(tuple(segs), roster=(CREATOR, "Guest"), roster_source="invite", creator=CREATOR)
    v = evaluate(rec)
    assert v.signals.substantive_speaker_count == 1
    assert v.outcome is Outcome.HOLD_FOR_CREATOR


# ── suppress ──────────────────────────────────────────────────────────────────────


def test_solo_capture_with_a_self_only_roster_is_suppressed():
    """The forgotten-bot class: one voice, and the roster confirms nobody else was asked."""
    rec = record(
        conversation(["Dmtiry Grankin"], 60, lines=DOMESTIC, language="ru"),
        roster=("Dmtiry Grankin",),  # note the typo — fuzzy match must still fold it into the creator
        roster_source="observed",
        creator=CREATOR,
    )
    v = evaluate(rec)
    assert v.signals.counterparty_count == 0
    assert v.outcome is Outcome.SUPPRESS
    assert "no_counterparty_on_roster" in v.reasons
    assert "sensitive_context" in v.reasons


def test_bot_labelled_audio_is_suppressed():
    """Tab/room playback: the bot's own display name is attributed as a speaking source."""
    rec = record(
        blocks(["Vexa test (Unverified)", CREATOR], 150),
        roster=(),
        roster_source="none",
        creator=CREATOR,
        bot_names=("Vexa test",),
    )
    v = evaluate(rec)
    assert v.signals.bot_speaker_present is True
    assert v.outcome is Outcome.SUPPRESS
    assert v.reasons == ("bot_audio_source",)


def test_empty_record_is_suppressed():
    rec = record(conversation([CREATOR, "Guest"], 6))
    assert evaluate(rec).outcome is Outcome.SUPPRESS
    assert evaluate(rec).reasons == ("empty_record",)


def test_no_segments_at_all_is_suppressed():
    rec = record(())
    assert evaluate(rec).outcome is Outcome.SUPPRESS


# ── routing ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "outcome,expected",
    [
        (Outcome.SEND, ("a@x.com", "b@x.com", "c@x.com")),
        (Outcome.HOLD_FOR_CREATOR, ("a@x.com",)),
        (Outcome.SUPPRESS, ()),
    ],
)
def test_route_recipients(outcome, expected):
    from presend_gate.policy import Verdict
    from presend_gate.signals import Signals

    v = Verdict(outcome=outcome, reasons=(), signals=Signals())
    assert route_recipients(v, ["a@x.com", "b@x.com", "c@x.com"], "a@x.com") == expected


def test_gate_returns_verdict_and_recipients_together():
    rec = record(
        conversation([CREATOR, "Marvin Hanke"], 400),
        roster=(CREATOR, "Marvin Hanke"),
        roster_source="invite",
        creator=CREATOR,
    )
    verdict, recipients = gate(rec, participants=["a@x.com", "b@x.com"])
    assert verdict.outcome is Outcome.SEND
    assert recipients == ("a@x.com", "b@x.com")

    solo, nobody = gate(
        record(conversation([CREATOR], 350), roster=(), roster_source="none", creator=CREATOR),
        participants=["a@x.com", "b@x.com"],
    )
    assert solo.outcome is Outcome.HOLD_FOR_CREATOR
    assert nobody == (CREATOR,)


def test_hold_and_suppress_carry_a_plain_note():
    from presend_gate.policy import NOTES

    assert "only you received it" in NOTES[Outcome.HOLD_FOR_CREATOR]
    assert "still in your list" in NOTES[Outcome.SUPPRESS]
    assert NOTES[Outcome.SEND] == ""


# ── measurement details ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("Dmitry Grankin", "Dmtiry Grankin", True),
        ("Dmitry Grankin", "Dmitriy Grankin", True),
        ("Hanke, Marvin", "Marvin Hanke", True),
        ("Dmitry Grankin", "Marvin Hanke", False),
        ("Dmitry Grankin", "", False),
    ],
)
def test_same_person(a, b, expected):
    assert same_person(a, b) is expected


def test_epoch_shaped_timestamps_do_not_break_the_signals():
    """Some records carry absolute epoch values in start/end. Only spans are used."""
    base = 1786568356.0
    segs = conversation([CREATOR, "Guest"], 200, start_at=base)
    v = evaluate(record(segs, roster=(CREATOR, "Guest"), roster_source="invite", creator=CREATOR))
    assert v.signals.speech_seconds == pytest.approx(200 * 5, rel=0.01)
    assert v.outcome is Outcome.SEND


def test_absurd_segment_spans_are_clamped():
    segs = (
        Segment(speaker=CREATOR, text="hello there", start=0, end=100_000),
        *conversation([CREATOR, "Guest"], 200, start_at=100_000),
    )
    s = measure(record(segs, creator=CREATOR))
    assert s.speech_seconds <= 200 * 5 + 120


def test_untimed_segments_fall_back_to_segment_weighting():
    segs = tuple(
        Segment(speaker=[CREATOR, "Guest"][i % 2], text="what do you think about that?", start=0, end=0)
        for i in range(200)
    )
    s = measure(record(segs, creator=CREATOR, wall_seconds=None))
    assert s.speech_seconds == 0.0
    assert s.top_speaker_share == pytest.approx(0.5, abs=0.01)
    assert s.monologue_ratio < 0.35


def test_policy_thresholds_are_overridable():
    rec = record(conversation([CREATOR], 350), roster=(), roster_source="none", creator=CREATOR)
    assert evaluate(rec).outcome is Outcome.HOLD_FOR_CREATOR
    reckless = Policy(min_dialogue_window_share=0.0, min_alternation_rate=0.0, max_monologue_ratio=1.0)
    # Still not `send` — the substantive-speaker rule is a separate net.
    assert evaluate(rec, reckless).outcome is Outcome.HOLD_FOR_CREATOR


# ── adapter ───────────────────────────────────────────────────────────────────────


def test_from_transcript_payload_reads_the_observed_roster():
    payload = {
        "id": 12615,
        "platform": "teams",
        "start_time": "2026-05-18T09:06:55.323714Z",
        "end_time": "2026-05-18T10:08:20.273833Z",
        "data": {"participants": ["Dmitry Grankin", "Hanke, Marvin"], "name": "OEnB"},
        "segments": [
            {"speaker": "Dmitry Grankin", "text": "hello", "start": 0, "end": 2, "language": "en"},
            {"speaker": "Hanke, Marvin", "text": "hi", "start": 2, "end": 4, "language": "en"},
            {"speaker": "Hanke, Marvin", "text": "   ", "start": 4, "end": 5, "language": "en"},
        ],
    }
    rec = from_transcript_payload(payload, creator="Dmitry Grankin", bot_names=["Vexa test"])
    assert rec.meeting_id == "12615"
    assert rec.roster_source == "observed"
    assert rec.wall_seconds == pytest.approx(3684.95, abs=1)
    assert len(rec.segments) == 2  # blank-text segment dropped


def test_invite_roster_overrides_the_observed_one():
    payload = {"id": 1, "data": {"participants": ["Someone Observed"]}, "segments": []}
    rec = from_transcript_payload(payload, roster=["a@x.com", "b@x.com"])
    assert rec.roster_source == "invite"
    assert rec.roster == ("a@x.com", "b@x.com")
