"""The schema, and the header contract the postman parses.

The regexes asserted here are the postman's own (``chat_door.artifact``). They are restated
rather than imported because the postman lives in a different service and importing across
that boundary is a ``gate:isolation`` violation — ``test_postman_contract.py`` closes the
loop by running the real parser when a chat-door checkout is pointed at it.
"""

from __future__ import annotations

import re

from vexa_artifact_pipeline import (
    ARTIFACT_SCHEMA_VERSION,
    Artifact,
    KIND_ORDER,
    Recipient,
    Section,
    slugify,
    vocabulary_for,
)

# The three header fields the postman reads, in its bold spelling.
BOLD_FIELD = r"^\*\*{key}:?\*\*:?\s*(.+)$"


def field(body: str, key: str) -> str:
    m = re.search(BOLD_FIELD.format(key=re.escape(key)), body, re.MULTILINE)
    return m.group(1).strip() if m else ""


def build(**kwargs) -> Artifact:
    base = dict(
        recipient=Recipient(display_name="Marvin Hanke", email="marvin@example.test"),
        meeting_id="12615",
        meeting_label="2026-05-18 · Microsoft Teams · 60m",
        language="en",
        sections=(Section(kind="you_committed", title="You committed to", items=("Show it to Toby today.",)),),
        renderer="template",
    )
    base.update(kwargs)
    return Artifact(**base)


def test_header_carries_the_three_fields_the_postman_parses():
    body = build().to_markdown()
    assert field(body, "To") == "Marvin Hanke"
    assert field(body, "Meeting") == "2026-05-18 · Microsoft Teams · 60m"
    assert field(body, "record").startswith("12615 · ")


def test_record_line_states_the_records_own_id_first():
    """The postman takes the leading token of the record line as the magic link's scope."""
    body = build(meeting_id="5175").to_markdown()
    assert re.match(r"^([A-Za-z0-9_-]+)", field(body, "record")).group(1) == "5175"


def test_russian_header_uses_the_russian_keys():
    body = build(
        language="ru",
        recipient=Recipient(display_name="Алексей Рогов"),
        sections=(Section(kind="you_committed", title="Ты взял на себя", items=("Прислать транскрипт.",)),),
    ).to_markdown()
    assert field(body, "Кому") == "Алексей Рогов"
    assert field(body, "Встреча")
    assert field(body, "запись").startswith("12615 · ")
    assert "открыть запись" in body


def test_placeholder_link_is_what_the_postman_rewrites():
    body = build().to_markdown()
    assert "](#)" in body and "*(placeholder link)*" in body


def test_a_real_link_replaces_the_placeholder_entirely():
    body = build().with_link("https://door.test/m/abc").to_markdown()
    assert "](#)" not in body
    assert "(placeholder link)" not in body
    assert "https://door.test/m/abc" in body


def test_sections_emit_in_the_canonical_order_whatever_order_they_were_built_in():
    artifact = build(
        sections=(
            Section(kind="asked_of_you", title="Asked of you", items=("Can you send the numbers?",)),
            Section(kind="decided", title="Decided", items=("We decided to keep the number.",)),
            Section(kind="you_committed", title="You committed to", items=("Send the proposal.",)),
        )
    )
    assert [s.kind for s in artifact.ordered_sections] == ["decided", "you_committed", "asked_of_you"]
    body = artifact.to_markdown()
    assert body.index("Decided") < body.index("You committed to") < body.index("Asked of you")


def test_empty_sections_are_dropped_not_printed_empty():
    artifact = build(
        sections=(
            Section(kind="decided", title="Decided", items=()),
            Section(kind="you_committed", title="You committed to", items=("Send it.",)),
        )
    )
    assert [s.kind for s in artifact.ordered_sections] == ["you_committed"]
    assert "Decided" not in artifact.to_markdown()


def test_an_artifact_with_no_surviving_section_reports_itself_empty():
    assert build(sections=(Section(kind="decided", title="Decided"),)).is_empty


def test_unknown_section_kinds_survive_and_sort_last():
    artifact = build(
        sections=(
            Section(kind="future_thing", title="Future thing", items=("x" * 30,)),
            Section(kind="decided", title="Decided", items=("We decided.",)),
        )
    )
    assert [s.kind for s in artifact.ordered_sections] == ["decided", "future_thing"]


def test_round_trips_through_serialization():
    original = build(record_link="https://door.test/m/abc")
    restored = Artifact.from_dict(original.to_dict())
    assert restored == original
    assert restored.to_markdown() == original.to_markdown()
    assert original.to_dict()["schema_version"] == ARTIFACT_SCHEMA_VERSION


def test_identity_is_the_email_when_there_is_one_and_is_marked_when_there_is_not():
    assert Recipient("Marvin Hanke", email="Marvin@Example.Test").identity == "marvin@example.test"
    nameless = Recipient("Hanke, Marvin")
    assert nameless.identity == "name:hanke-marvin"
    assert not nameless.addressable


def test_slugify_transliterates_cyrillic_and_folds_accents():
    assert slugify("Алексей Рогов") == "aleksey-rogov"
    assert slugify("Julianne Appleton (she / her)") == "julianne-appleton-she-her"
    assert slugify("") == "participant"


def test_every_vocabulary_names_every_ordered_kind():
    for language in ("en", "ru"):
        vocab = vocabulary_for(language)
        for kind in KIND_ORDER:
            assert vocab.heading(kind) != kind.replace("_", " "), (language, kind)


def test_an_unknown_language_falls_back_to_english_rather_than_inventing_one():
    assert vocabulary_for("de").language == "en"
