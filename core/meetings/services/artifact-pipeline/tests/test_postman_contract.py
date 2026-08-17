"""The other half of the wrong-link incident: does the postman read what we emit?

The header shape is a contract between two services, and a contract asserted only by the
producer is how it broke the first time — two renderers, incompatible headers, a parser that
read one of them, and a magic link scoped to the wrong meeting with nothing raising.

This module runs the **real** parser (``chat_door.artifact``) against artifacts this service
emits. It cannot import across the service boundary in the normal case (``gate:isolation``),
so it skips unless an operator points it at a chat-door checkout:

    VEXA_CHAT_DOOR_SRC=../chat-door/src uv run pytest tests/test_postman_contract.py -q

Until the chat door is merged and both services live at known paths in one tree, this is the
seam where the contract is actually verified rather than assumed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from vexa_artifact_pipeline import Artifact, Recipient, Section

CHAT_DOOR_SRC = os.environ.get("VEXA_CHAT_DOOR_SRC")

pytestmark = pytest.mark.skipif(
    not CHAT_DOOR_SRC or not (Path(CHAT_DOOR_SRC) / "chat_door" / "artifact.py").exists(),
    reason="VEXA_CHAT_DOOR_SRC is not set — the chat door is a separate, unmerged service",
)


@pytest.fixture(scope="module")
def load_artifact():
    sys.path.insert(0, str(Path(CHAT_DOOR_SRC).resolve()))
    from chat_door.artifact import load_artifact as loader  # noqa: PLC0415

    return loader


def english(meeting_id: str = "5175") -> Artifact:
    return Artifact(
        recipient=Recipient(display_name="Marvin Hanke", email="marvin@example.test"),
        meeting_id=meeting_id,
        meeting_label="2026-05-18 · Microsoft Teams · 60m",
        language="en",
        sections=(Section(kind="you_committed", title="You committed to", items=("Show it to Toby today.",)),),
        renderer="template",
    )


def russian() -> Artifact:
    return Artifact(
        recipient=Recipient(display_name="Алексей Рогов"),
        meeting_id="11706",
        meeting_label="2026-05-05 · Microsoft Teams · 78 мин",
        language="ru",
        sections=(Section(kind="you_committed", title="Ты взял на себя", items=("Прислать транскрипт встречи.",)),),
        renderer="template",
    )


def write(tmp_path: Path, artifact: Artifact, *, directory: str) -> Path:
    folder = tmp_path / directory
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{artifact.recipient.slug}.md"
    path.write_text(artifact.to_markdown(), "utf-8")
    return path


def test_the_postman_reads_the_record_id_off_the_line_not_off_the_directory(tmp_path, load_artifact):
    """The exact defect: the corpus keyed a folder 5174 for a record that states 5175."""
    parsed = load_artifact(write(tmp_path, english("5175"), directory="5174"))
    assert parsed.meeting_id == "5175"


def test_the_postman_reads_the_recipient_and_the_meeting_label(tmp_path, load_artifact):
    parsed = load_artifact(write(tmp_path, english(), directory="5174"))
    assert parsed.recipient_name == "Marvin Hanke"
    assert parsed.meeting_label == "2026-05-18 · Microsoft Teams · 60m"
    assert parsed.language == "en"
    assert parsed.subject == "2026-05-18 · Microsoft Teams · 60m — what changed for you"


def test_a_russian_artifact_parses_in_russian(tmp_path, load_artifact):
    parsed = load_artifact(write(tmp_path, russian(), directory="11706"))
    assert parsed.meeting_id == "11706"
    assert parsed.recipient_name == "Алексей Рогов"
    assert parsed.language == "ru"
    assert parsed.participant_slug == "aleksey-rogov"


def test_the_placeholder_link_is_the_one_the_postman_rewrites(tmp_path, load_artifact):
    from chat_door.artifact import with_record_link  # noqa: PLC0415

    parsed = load_artifact(write(tmp_path, english(), directory="5175"))
    body, rewrote = with_record_link(parsed.body, "https://door.test/m/token")
    assert rewrote, "the postman did not recognise the placeholder link slot"
    assert "https://door.test/m/token" in body
    assert "(placeholder link)" not in body


def test_an_already_linked_artifact_is_not_double_linked(tmp_path, load_artifact):
    from chat_door.artifact import with_record_link  # noqa: PLC0415

    linked = english().with_link("https://door.test/m/original")
    parsed = load_artifact(write(tmp_path, linked, directory="5175"))
    body, rewrote = with_record_link(parsed.body, "https://door.test/m/second")
    assert not rewrote
    assert "https://door.test/m/second" not in body
