"""WHERE A MEETING'S REPORT IS, AND WHO GETS TO SPELL IT (Vexa-ai/vexa#1588).

The founder opened the meeting chat for DNA TSC 2026-03-02. `drop_to_attendees` had written the
report to `kg/entities/meeting/2026-03-02-0000-dna-tsc-2026-03-02.md` on his desk an hour earlier —
6.3 KB, the full sourced thing. The pinned Minutes tab opened `kg/entities/meeting/96088138284.md`
and said *"No page here yet"*. One path, two spellings, in two languages.

So the tests that matter here are not about a directory scan. They are:

  · the resolver agrees with `core/flows`' `_note_path` — the ONE recipe, pinned against a filename
    produced by that recipe rather than by a fixture somebody typed;
  · it returns a file that EXISTS and never composes one;
  · a recurring meeting (one title, many occurrences) resolves to its own occurrence;
  · a meeting with no report answers None, which the caller must be able to tell from a failure.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control_plane import meeting_note  # noqa: E402

DNA = "2026-03-02-0000-dna-tsc-2026-03-02.md"


def _entity(title: str, day: str, *, meeting: str = "", native: str = "") -> str:
    lines = ["---", "type: meeting", "id: x"]
    if meeting:
        lines.append(f"meeting: {meeting}")
    if native:
        lines.append(f"native: {native}")
    lines += [f'title: "{title}"', f"date: {day}", 'organizer: "a@b.test"',
              "participants: []", "tags: [vexa-meeting]", "---", "", f"# {title}", "", "the report"]
    return "\n".join(lines) + "\n"


def _desk(tmp_path: Path, uid: str = "175") -> Path:
    d = tmp_path / uid / meeting_note.MEETING_DIR
    d.mkdir(parents=True)
    return d


def _row(**kw) -> dict:
    row = {"id": 147, "native_meeting_id": "96088138284",
           "data": {"title": "DNA TSC 2026-03-02", "scheduled_at": "2026-03-02T00:00:00+00:00"}}
    data = kw.pop("data", None)
    row.update(kw)
    if data is not None:
        row["data"] = data
    return row


# ── the live defect ──────────────────────────────────────────────────────────────────────────────

def test_the_report_the_flow_wrote_is_the_path_the_client_is_given(tmp_path):
    """The exact shape of 2026-09-06: the report is on the desk under the flow's filename, and the
    row carries the native id the client used to compose from."""
    desk = _desk(tmp_path)
    (desk / DNA).write_text(_entity("DNA TSC 2026-03-02", "2026-03-02"))
    (desk / "index.md").write_text("# meeting\n\n- [DNA TSC 2026-03-02](%s) — 2026-03-02\n" % DNA)
    assert meeting_note.resolve(tmp_path, "175", _row()) == f"kg/entities/meeting/{DNA}"


def test_the_native_spelling_is_never_produced(tmp_path):
    """The path the client used to build (`<native>.md`) is not on the desk, so nothing may answer
    with it. A resolver that composed would return it here and look right doing so."""
    desk = _desk(tmp_path)
    (desk / DNA).write_text(_entity("DNA TSC 2026-03-02", "2026-03-02"))
    got = meeting_note.resolve(tmp_path, "175", _row())
    assert got != "kg/entities/meeting/96088138284.md"
    assert (tmp_path / "175" / got).is_file()


def test_a_meeting_with_no_report_answers_none(tmp_path):
    """The honest answer, and the ordinary one before the drop runs. NOT an error: the caller opens
    one document fewer, and a tab onto a guessed path is the thing this replaces."""
    _desk(tmp_path)
    assert meeting_note.resolve(tmp_path, "175", _row()) is None


def test_a_desk_that_does_not_exist_answers_none(tmp_path):
    assert meeting_note.resolve(tmp_path, "nobody", _row()) is None


def test_another_meetings_report_is_not_this_meetings(tmp_path):
    desk = _desk(tmp_path)
    (desk / "2026-03-01-0900-standup.md").write_text(_entity("Standup", "2026-03-01"))
    assert meeting_note.resolve(tmp_path, "175", _row()) is None


# ── the identity the writer stamps ───────────────────────────────────────────────────────────────

def test_the_frontmatter_ids_win_over_the_title(tmp_path):
    """Tier 1. A report that names its row is matched on the row — the only tier that can tell two
    occurrences of a recurring meeting apart."""
    desk = _desk(tmp_path)
    (desk / "2026-02-24-0000-dna-tsc.md").write_text(
        _entity("DNA TSC", "2026-02-24", meeting="140", native="111"))
    (desk / "2026-03-02-0000-dna-tsc.md").write_text(
        _entity("DNA TSC", "2026-03-02", meeting="147", native="96088138284"))
    row = _row(data={"title": "DNA TSC", "scheduled_at": "2026-03-02T00:00:00+00:00"})
    assert meeting_note.resolve(tmp_path, "175", row) == "kg/entities/meeting/2026-03-02-0000-dna-tsc.md"


def test_the_native_id_alone_resolves_it(tmp_path):
    desk = _desk(tmp_path)
    (desk / "2026-03-02-0000-whatever.md").write_text(
        _entity("A name nobody kept", "2026-03-02", native="96088138284"))
    assert meeting_note.resolve(tmp_path, "175", _row()) == "kg/entities/meeting/2026-03-02-0000-whatever.md"


def test_a_recurring_meeting_without_ids_takes_its_own_day(tmp_path):
    """Tier 2, and the case that makes a day tie-break load-bearing: one title, three weeks, and
    the reports written before the ids existed carry only the title."""
    desk = _desk(tmp_path)
    for day in ("2026-02-16", "2026-02-23", "2026-03-02"):
        (desk / f"{day}-0000-dna-tsc.md").write_text(_entity("DNA TSC", day))
    row = _row(data={"title": "DNA TSC", "scheduled_at": "2026-03-02T00:00:00+00:00"})
    assert meeting_note.resolve(tmp_path, "175", row) == "kg/entities/meeting/2026-03-02-0000-dna-tsc.md"


def test_two_occurrences_on_one_day_take_the_later_file(tmp_path):
    """`<day>-<time>-<slug>` sorts chronologically as a string, so this is a fact about the files.
    What it must never be is a coin flip — two runs of the same open must answer the same."""
    desk = _desk(tmp_path)
    (desk / "2026-03-02-0900-dna-tsc.md").write_text(_entity("DNA TSC", "2026-03-02"))
    (desk / "2026-03-02-1400-dna-tsc.md").write_text(_entity("DNA TSC", "2026-03-02"))
    row = _row(data={"title": "DNA TSC", "scheduled_at": "2026-03-02T00:00:00+00:00"})
    assert meeting_note.resolve(tmp_path, "175", row) == "kg/entities/meeting/2026-03-02-1400-dna-tsc.md"
    assert meeting_note.resolve(tmp_path, "175", row) == "kg/entities/meeting/2026-03-02-1400-dna-tsc.md"


def test_the_index_is_never_the_answer(tmp_path):
    desk = _desk(tmp_path)
    (desk / "index.md").write_text("---\ntype: meeting\ntitle: \"DNA TSC 2026-03-02\"\n---\n")
    assert meeting_note.resolve(tmp_path, "175", _row()) is None


def test_a_row_with_nothing_to_match_on_answers_none(tmp_path):
    """No id, no native, no title — there is nothing here that could be a match, only something
    that could be a guess."""
    desk = _desk(tmp_path)
    (desk / DNA).write_text(_entity("DNA TSC 2026-03-02", "2026-03-02"))
    assert meeting_note.resolve(tmp_path, "175", {"data": {}}) is None


# ── the recipe, not a fixture ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("title", ["DNA TSC 2026-03-02", "Pilot sync", "Weekly: eng/product review"])
def test_it_resolves_the_filename_the_flows_recipe_actually_produces(tmp_path, monkeypatch, title):
    """THE PIN THE ISSUE ASKS FOR — against `core/flows`' `_note_path`, imported, not re-spelled.

    The two live in different services and ship in different images, which is exactly how they came
    to disagree. This test is the only place they meet."""
    flows_src = Path(__file__).resolve().parents[2] / "flows" / "src"
    sys.path.insert(0, str(flows_src))
    try:
        from flows_defs import production  # noqa: PLC0415
    except Exception as e:  # pragma: no cover — a missing sibling checkout is not a failing product
        pytest.skip(f"core/flows not importable here ({e})")

    class _Ctx:
        refs = {"uid": "175", "start": 1772409600.0, "title": title}
        scratch: dict = {}

    # UTC, no per-user timezone in this test — the same seam `core/flows`' own tests set.
    monkeypatch.setattr(production, "setting", lambda _uid, _key: None)
    path = production._note_path(_Ctx(), "175", title)
    assert path.startswith(meeting_note.MEETING_DIR + "/")

    desk = _desk(tmp_path)
    (desk / path.rsplit("/", 1)[-1]).write_text(_entity(title, path.rsplit("/", 1)[-1][:10]))
    row = _row(data={"title": title, "scheduled_at": "2026-03-02T00:00:00+00:00"})
    assert meeting_note.resolve(tmp_path, "175", row) == path


# ── the frontmatter reader ───────────────────────────────────────────────────────────────────────

def test_front_matter_reads_the_shapes_the_writer_emits():
    fm = meeting_note.front_matter(_entity('He said "hi"', "2026-03-02", meeting="147", native="96"))
    assert fm["type"] == "meeting"
    assert fm["meeting"] == "147"
    assert fm["native"] == "96"
    assert fm["title"] == 'He said "hi"'
    assert fm["date"] == "2026-03-02"


def test_a_file_with_no_front_matter_is_not_a_meeting(tmp_path):
    desk = _desk(tmp_path)
    (desk / "notes.md").write_text("# DNA TSC 2026-03-02\n\nsome prose\n")
    assert meeting_note.resolve(tmp_path, "175", _row()) is None


# ── AND WHETHER IT IS THE MEETING'S ONE PAGE (Vexa-ai/vexa#1598) ─────────────────────────────────
#
# Founder, live, 2026-09-06: the meeting is ONE page with the transcript embedded in it. `describe`
# is what lets the room know whether THIS page is that: it reads the widget slot and the cursor off
# the file. The claim worth pinning is the negative one — every report written before the widget
# exists on somebody's desk right now, and answering "there is a note, so the transcript is in it"
# would take the room off exactly their screen, silently.

def test_describe_reads_the_widget_and_the_cursor_off_the_page(tmp_path):
    from shared import meeting_doc
    desk = _desk(tmp_path)
    page = _entity("DNA TSC 2026-03-02", "2026-03-02", meeting="147")
    page = meeting_doc.ensure_slot(page, "147")
    page = meeting_doc.advance_cursor(page, "2026-09-06T12:04:31.000Z")
    (desk / DNA).write_text(page)
    got = meeting_note.describe(tmp_path, "175", _row())
    assert got == {"path": f"kg/entities/meeting/{DNA}", "transcript": "147",
                   "cursor": "2026-09-06T12:04:31.000Z"}


def test_a_page_written_before_the_widget_says_so_rather_than_guessing(tmp_path):
    desk = _desk(tmp_path)
    (desk / DNA).write_text(_entity("DNA TSC 2026-03-02", "2026-03-02", meeting="147"))
    got = meeting_note.describe(tmp_path, "175", _row())
    assert got["path"] == f"kg/entities/meeting/{DNA}"
    assert (got["transcript"], got["cursor"]) == ("", "")


def test_no_report_describes_as_nothing_and_never_raises(tmp_path):
    _desk(tmp_path)
    assert meeting_note.describe(tmp_path, "175", _row()) == {"path": None, "transcript": "", "cursor": ""}
    # a desk that is not there at all is the same answer, not an exception
    assert meeting_note.describe(tmp_path / "nope", "175", _row()) == {"path": None, "transcript": "", "cursor": ""}
