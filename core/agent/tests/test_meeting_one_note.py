"""ONE NOTE PER MEETING (#1783).

A meeting's note is minted on the person's desk when the bot is sent, and the meeting view reads it
there. A chat that works in another workspace used to write the same relative path into THAT
workspace: the agent said "extended", the view kept showing the empty desk copy, and the person was
told nothing had been written. These pin the three places that decide where the note is written:
the page write itself, the Update-notes ask's workspace token, and the ask's own wording.
"""
from __future__ import annotations

from pathlib import Path

from control_plane import meeting_note
from control_plane.chat_intents import tokens_for
from shared import meeting_doc

from tests.test_page_verbs_at_the_edge import JANE, SHARED, _as_worker, _git, _world

NOTE = "kg/entities/meeting/2026-10-14-2056-google-meet-meeting-abc-defg-hij.md"
ASK = Path(__file__).resolve().parents[3] / "behavior" / "asks" / "extend-meeting.md"


def _mint_on_desk(desk: Path) -> str:
    doc = meeting_doc.scaffold(meeting="111", title="Google Meet meeting", native="abc-defg-hij",
                               date="2026-10-14")
    f = desk / NOTE
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(doc)
    _git(desk, "add", "-A")
    _git(desk, "commit", "-q", "-m", "mint")
    return doc


def test_an_agent_write_of_the_note_from_a_chat_in_another_workspace_lands_on_the_desk(tmp_path):
    client, desk, shared = _world(tmp_path)
    doc = _mint_on_desk(desk)
    written = meeting_doc.write_region(doc, "decisions", "- ship it")
    r = client.put("/api/workspace/file", headers=_as_worker(SHARED),
                   json={"path": NOTE, "content": written})
    assert r.status_code == 200, r.text
    assert "- ship it" in (desk / NOTE).read_text()
    assert not (shared / NOTE).exists(), "a second copy of the note in the chat's workspace"


def test_a_named_workspace_is_still_obeyed(tmp_path):
    client, desk, shared = _world(tmp_path)
    _mint_on_desk(desk)
    r = client.put("/api/workspace/file", headers=_as_worker(SHARED),
                   json={"path": NOTE, "content": "---\ntype: meeting\n---\nthere\n", "slug": SHARED})
    assert r.status_code == 200, r.text
    assert (shared / NOTE).read_text().endswith("there\n")


def test_an_ordinary_page_with_no_slug_still_lands_in_the_chats_target(tmp_path):
    client, desk, shared = _world(tmp_path)
    _mint_on_desk(desk)
    other = "kg/entities/meeting/some-other-meeting.md"   # not on the desk: an ordinary page
    r = client.put("/api/workspace/file", headers=_as_worker(SHARED),
                   json={"path": other, "content": "# x\n"})
    assert r.status_code == 200, r.text
    assert (shared / other).is_file() and not (desk / other).exists()


def test_desk_holds_note_needs_a_meeting_page_at_exactly_that_path(tmp_path):
    client, desk, _shared = _world(tmp_path)
    assert not meeting_note.desk_holds_note(tmp_path, JANE, NOTE)
    _mint_on_desk(desk)
    assert meeting_note.desk_holds_note(tmp_path, JANE, NOTE)
    assert not meeting_note.desk_holds_note(tmp_path, JANE, "notes/plan.md")
    assert not meeting_note.desk_holds_note(tmp_path, JANE, "kg/entities/meeting/../../README.md")


def test_update_notes_names_the_desk_when_the_note_is_on_the_desk():
    t = tokens_for({"kind": "extend", "path": NOTE, "meeting": "111"})
    assert t["workspace"] == "personal"
    t = tokens_for({"kind": "extend", "path": NOTE, "meeting": "111", "workspace": "team-x"})
    assert t["workspace"] == "team-x"
    # a page that is not a meeting's keeps an empty workspace: the chat's target, as before
    assert tokens_for({"kind": "extend", "path": "kg/plan.md"})["workspace"] == ""


def test_the_ask_writes_the_note_in_its_workspace_by_name():
    ask = ASK.read_text()
    assert 'workspace_write(slug="{{workspace}}", path="{{path}}")' in ask


def test_a_minted_note_starts_empty_no_title_no_empty_headings():
    doc = meeting_doc.scaffold(meeting="111", title="Google Meet meeting", native="abc", date="2026-10-14")
    _fm, body = doc.split("\n---\n", 1)
    assert "#" not in body, "a fresh note carries no headings: sections appear when there is content"
    assert meeting_doc.slot_meeting(doc) == "111"
    assert meeting_doc.read_cursor(doc) == ""
    # …and the first update still has somewhere to write
    filled = meeting_doc.write_region(doc, "decisions", "- ship it")
    assert "## Decisions" in filled and "- ship it" in filled


def test_the_notes_link_what_they_name():
    """Founder, 2026-10-15: "we want notes to be connected to entities". The Update-notes ask links
    existing pages as chips, pages a clearly identified new person or company with the meeting as
    source, backlinks each, and keeps an unidentified speaker plain."""
    ask = ASK.read_text()
    assert "[[Its Page Title]]" in ask and "[[meeting:<its id>|<its name>]]" in ask
    assert "entity_upsert" in ask and "this meeting as its source" in ask
    assert "stays plain text" in ask and "Never make a page for it" in ask
    assert "[[meeting:{{meeting}}|" in ask        # the backlink on each linked page


def test_the_post_meeting_report_links_more_than_people():
    report = (ASK.parents[1] / "prompts" / "process-meeting.md").read_text()
    assert "people, companies and projects as [[wikilinks]]" in report
