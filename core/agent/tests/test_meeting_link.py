"""A link to a meeting's notes page is answered as the MEETING — its title and date — per reader.

Founder, 2026-10-11, on a chat reply that chipped the people and printed the meeting as
`2026-10-09-1401-zoom-meeting-88410021706.md`: *"i think we need a special label for a meeting
alongside people companies etc"*.

`POST /api/links/resolve` answers a ref naming `kg/entities/meeting/<name>.md` with `kind:
"meeting"`, the meeting's own title (`data.title`, what a rename writes) and its time — read off the
row through the one meeting access decision. A reader the meeting is not answered to gets
`not-yours` and no title, even when the page itself is on their desk.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from control_plane import meeting_link
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_ids import ACCESS_GONE, ACCESS_NOT_YOURS, ACCESS_READABLE
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from shared.meeting_doc import scaffold

from tests.test_api import _FakeIdentity, _FakeRuntime

OWNER = "126"
STRANGER = "200"
ROW = "118"
NOTE = "kg/entities/meeting/2026-10-09-1401-zoom-meeting-88410021706.md"
ROW_DATA = {"id": int(ROW), "platform": "zoom", "native_meeting_id": "88410021706",
            "start_time": "2026-10-09T14:01:00Z", "created_at": "2026-10-09T13:58:00Z",
            "data": {"title": "Acme"}}


def _as(subject: str) -> dict:
    return {"X-User-Id": subject, "X-User-Email": f"{subject}@example.com"}


@pytest.fixture()
def client(tmp_path):
    asked: list = []

    def _owner(user_id, meeting_id, workspaces=None):
        asked.append((user_id, str(meeting_id)))
        return dict(ROW_DATA) if (user_id == OWNER and str(meeting_id) == ROW) else None

    c = TestClient(create_app(
        Dispatcher(load_settings(workspaces_dir=str(tmp_path)), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(tmp_path)), meeting_owner_lookup=_owner))
    page = scaffold(meeting=ROW, title="Zoom meeting", native="88410021706", date="2026-10-09")
    # The same page on BOTH desks: the stranger can read their copy, and still must not learn the
    # meeting's name from it.
    for who in (OWNER, STRANGER):
        r = c.put("/api/workspace/file", headers=_as(who), json={"path": NOTE, "content": page})
        assert r.status_code == 200, r.text
    asked.clear()
    return c, asked


def _resolve(c, who: str, ref: str) -> dict:
    r = c.post("/api/links/resolve", headers=_as(who), json={"refs": [ref]})
    assert r.status_code == 200, r.text
    (row,) = r.json()["results"]
    return row


def test_a_notes_page_link_is_answered_as_the_meeting(client):
    c, asked = client
    row = _resolve(c, OWNER, NOTE)
    assert row["kind"] == "meeting"
    assert row["access"] == ACCESS_READABLE
    # the meeting's own name — never the file's, never the page's "Zoom meeting"
    assert row["title"] == "Acme"
    assert row["at"] == "2026-10-09T14:01:00Z"
    assert row["meeting"] == {"id": ROW, "platform": "zoom", "native": "88410021706"}
    assert (OWNER, ROW) in asked


def test_every_spelling_of_the_notes_path_is_the_same_meeting(client):
    c, _ = client
    for ref in (f"./{NOTE}", f"/workspaces/{OWNER}/{NOTE}"):
        row = _resolve(c, OWNER, ref)
        assert row["kind"] == "meeting" and row["title"] == "Acme", ref


def test_a_reader_the_meeting_is_not_answered_to_gets_no_title(client):
    """THE DENY. The stranger holds a copy of the page; the meeting is not theirs."""
    c, asked = client
    row = _resolve(c, STRANGER, NOTE)
    assert row["kind"] == "meeting"
    assert row["access"] == ACCESS_NOT_YOURS
    assert row["title"] == ""
    assert "meeting" not in row and "at" not in row
    assert "Acme" not in str(row)
    assert (STRANGER, ROW) in asked     # it was ASKED, under the stranger's identity


def test_a_notes_page_nobody_can_read_here_is_gone(client):
    c, asked = client
    row = _resolve(c, OWNER, "kg/entities/meeting/2026-01-01-0900-not-there.md")
    assert row["kind"] == "meeting" and row["access"] == ACCESS_GONE and row["title"] == ""
    assert asked == []                  # no page, no meeting id, nothing to ask


def test_other_refs_resolve_as_pages_exactly_as_before(client):
    c, _ = client
    for ref in ("kg/entities/meeting/index.md", "notes.md", "Nora Quill"):
        assert "kind" not in _resolve(c, OWNER, ref), ref


def test_note_path_of_is_the_minters_alphabet():
    assert meeting_link.note_path_of(NOTE) == NOTE
    assert meeting_link.note_path_of(f"/workspaces/x/{NOTE}?v=1") == NOTE
    for bad in ("kg/entities/meeting/index.md", "kg/entities/meeting/a/b.md",
                "kg/entities/meeting/../person/x.md", "kg/entities/person/x.md", ""):
        assert meeting_link.note_path_of(bad) == "", bad


def test_a_page_with_no_meeting_stamp_keeps_its_own_title(tmp_path):
    """A report written before the stamp existed: the reader may read the page, so its title is
    theirs; there is no row to ask about."""
    (tmp_path / "kg/entities/meeting").mkdir(parents=True)
    (tmp_path / NOTE).write_text("---\ntype: meeting\ntitle: Weekly sync\ndate: 2026-10-02\n---\n")
    called = []
    row = meeting_link.resolve(NOTE, roots=[tmp_path], access=lambda mid: called.append(mid))
    assert row["access"] == ACCESS_READABLE and row["title"] == "Weekly sync"
    assert row["at"] == "2026-10-02" and row["meeting"] is None and called == []


def test_an_access_check_that_raises_is_a_refusal(tmp_path):
    (tmp_path / "kg/entities/meeting").mkdir(parents=True)
    (tmp_path / NOTE).write_text(scaffold(meeting=ROW, title="x"))

    def boom(_mid):
        raise RuntimeError("meeting-api down")

    row = meeting_link.resolve(NOTE, roots=[tmp_path], access=boom)
    assert row["access"] == ACCESS_NOT_YOURS and row["title"] == ""


def test_the_agent_is_told_to_name_a_meeting_by_its_link():
    """The chip appears only if the agent writes the link — so the rule ships on every turn, and
    the seeded workspace guide says the same."""
    from pathlib import Path

    from worker import engine

    rule = engine.kg_links_preamble()
    assert "[<title>](/?meeting=<meeting id>)" in rule
    assert "Never name a meeting by its notes filename" in rule
    guide = (Path(__file__).resolve().parents[3] / "behavior/workspaces/default/CLAUDE.md").read_text()
    assert "Reference a meeting by its link, not its notes file." in guide
