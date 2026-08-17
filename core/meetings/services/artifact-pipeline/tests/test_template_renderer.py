"""The deterministic renderer: what it claims, and what it refuses to claim."""

from __future__ import annotations

import pytest

from conftest import conversation, record_payload, segment
from vexa_artifact_pipeline import (
    FetchedRecord,
    LlmRenderer,
    Recipient,
    TemplateRenderer,
)
from vexa_artifact_pipeline.render_template import blocks

MARVIN = Recipient(display_name="Marvin Hanke", email="marvin@example.test")
DMITRY = Recipient(display_name="Dmitry Grankin", email="dmitry@example.test", is_creator=True)
LAIBA = Recipient(display_name="Laiba Warraich")


def fetched(segments=None, **kwargs) -> FetchedRecord:
    payload = record_payload("12615", segments=segments, **kwargs)
    return FetchedRecord(
        requested_id="12615",
        found=True,
        payload={k: v for k, v in payload.items() if k != "segments"},
        segments=payload["segments"],
        transcript_available=True,
    )


def render(recipient: Recipient, record: FetchedRecord | None = None, **kwargs):
    return TemplateRenderer(**kwargs).render(
        record=record or fetched(),
        recipient=recipient,
        participants=(DMITRY, MARVIN, LAIBA),
        meeting_id="12615",
        meeting_label="2026-05-18 · Microsoft Teams · 60m",
        language="en",
    )


def kinds(artifact) -> list[str]:
    return [s.kind for s in artifact.ordered_sections]


def items(artifact, kind) -> list[str]:
    return [i for s in artifact.ordered_sections if s.kind == kind for i in s.items]


def test_the_recipients_own_commitments_land_under_you_committed():
    artifact = render(MARVIN)
    assert any("Toby" in i for i in items(artifact, "you_committed"))


def test_someone_elses_commitment_in_the_conversation_lands_under_owed_to_you():
    owed = items(render(MARVIN), "owed_to_you")
    assert any(i.endswith("— Dmitry Grankin") and "updated proposal" in i for i in owed)
    assert all(" — " in i for i in owed), "an obligation must name who carries it"


def test_a_commitment_is_never_filed_under_both_people():
    marvin_committed = set(items(render(MARVIN), "you_committed"))
    dmitry_committed = set(items(render(DMITRY), "you_committed"))
    assert marvin_committed and dmitry_committed
    assert not (marvin_committed & dmitry_committed)


def test_a_decision_reaches_everyone_because_it_is_not_about_who_spoke():
    for person in (MARVIN, DMITRY, LAIBA):
        assert any("per-seat" in i for i in items(render(person), "decided")), person.display_name


def test_a_question_naming_the_recipient_reaches_them():
    asked = items(render(MARVIN), "asked_of_you")
    assert any("integration work" in i for i in asked)


def test_a_participant_who_never_speaks_still_gets_what_names_them():
    artifact = render(LAIBA)
    assert "decided" in kinds(artifact)
    assert any("OIDC" in i for i in items(artifact, "owed_to_you") + items(artifact, "asked_of_you"))


def test_items_are_whole_sentences_not_transcript_fragments():
    """Segments are chunked by the recogniser; blocks are re-split on sentence boundaries."""
    fragments = [
        segment("Marvin Hanke", "on your setup. So I will see if it is", 0.0),
        segment("Marvin Hanke", "going to work on the German calls this week.", 6.0),
    ]
    merged = blocks(fetched(segments=fragments))
    assert len(merged) == 1
    assert merged[0].text.endswith("this week.")


def test_nothing_is_emitted_that_no_cue_matched():
    """The renderer does not infer. Chatter with no commitment, decision or question yields
    the honest empty artifact rather than a padded one."""
    chatter = [
        segment("Marvin Hanke", "The weather here has been genuinely strange all week long.", i * 6.0)
        if i % 2
        else segment("Dmitry Grankin", "Yes, it rained the whole way over from the airport today.", i * 6.0)
        for i in range(30)
    ]
    artifact = render(MARVIN, fetched(segments=chatter))
    assert kinds(artifact) == ["nothing_recorded"]
    assert artifact.is_empty is False  # the notice itself is a section


def test_the_empty_notice_can_be_turned_off_so_the_shrink_question_is_measurable():
    chatter = [segment("Marvin Hanke", "It rained the whole way over from the airport today.", i * 6.0) for i in range(30)]
    artifact = render(MARVIN, fetched(segments=chatter), emit_when_empty=False)
    assert artifact.is_empty
    assert artifact.to_markdown().count("**") >= 6  # header survives; body does not


def test_items_are_capped_and_deduplicated():
    repeated = [segment("Dmitry Grankin", "I will send you the updated proposal tomorrow morning.", i * 6.0) for i in range(20)]
    repeated += [segment("Marvin Hanke", "Understood, that is fine by me and by Toby as well.", (20 + i) * 6.0) for i in range(20)]
    artifact = render(MARVIN, fetched(segments=repeated), max_items=3)
    assert len(items(artifact, "owed_to_you")) == 1  # twenty copies of one sentence


def test_russian_records_render_russian_sections():
    lines = [
        ("Алексей Рогов", "Я пришлю тебе транскрипт этой встречи сегодня вечером, как договаривались."),
        ("Дмитрий Гранкин", "Хорошо, а я подготовлю болванку коммерческого предложения к пятнице."),
    ]
    record = fetched(segments=conversation(lines, repeats=12, language="ru"))
    artifact = TemplateRenderer().render(
        record=record,
        recipient=Recipient(display_name="Алексей Рогов"),
        participants=(),
        meeting_id="11706",
        meeting_label="2026-05-05 · Microsoft Teams · 78 мин",
        language="ru",
    )
    assert "Ты взял на себя" in artifact.to_markdown()
    assert any("транскрипт" in i for i in items(artifact, "you_committed"))


def test_the_llm_renderer_refuses_rather_than_quietly_degrading():
    with pytest.raises(NotImplementedError) as excinfo:
        LlmRenderer().render(
            record=fetched(),
            recipient=MARVIN,
            participants=(),
            meeting_id="12615",
            meeting_label="",
            language="en",
        )
    assert "BYOT" in str(excinfo.value)
