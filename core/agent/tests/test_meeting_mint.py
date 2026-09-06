"""THE MEETING DOC EXISTS FROM BOT-SEND, AND ITS PATH HAS ONE SPELLING (Vexa-ai/vexa#1601).

Founder, 2026-09-06 12:50Z, in a live Google Meet he had started from a chat, the transcript pinned
beside it and nothing on the right: *"where is it?"* — the meeting doc with the transcript embedded
(#1598). The page was written by `core/flows`' `drop_to_attendees` when the call ENDED, so the one
page a live meeting is supposed to BE did not exist while the meeting was running. #1598's own
report named the missing piece: *"minting it at bot-send time needs the flow's `_note_path` recipe
reachable from agent-api (or the row to carry the path)"*.

The row carries the path. So this file holds two things and they are the same thing:

  · THE MINT — a send puts the meeting's page on the sender's desk, in the same turn, with the
    transcript slot and the empty regions Expand and the flow's report fill. It is idempotent, and
    that is the safety property: the page is written by three hands afterwards.
  · THE ONE SPELLING — the minter RECORDS the path on the meeting row, and `/api/meeting/note`
    answers from that record before it scans. `core/flows`' `_note_path` reads the same record back
    (`core/flows/tests/test_one_note_path.py`) instead of composing a second name for one file.

L2 for the turn: a real FastAPI app over fakes, no redis, no runtime, no claude.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control_plane import meeting_mint, meeting_note  # noqa: E402
from control_plane.api import _Sessions, create_app  # noqa: E402
from control_plane.dispatch import Dispatcher  # noqa: E402
from control_plane.workspace_reader import WorkspaceReader  # noqa: E402
from shared.config import load_settings  # noqa: E402

INTERNAL = "internal-tier-secret-for-tests"
SUBJECT = "u_priya"

#: The row `bot_send` just created: a Meet started from a chat, so nobody ever named it.
ROW = {"id": 118, "native_meeting_id": "cqb-egsq-vmt", "platform": "google_meet",
       "created_at": "2026-09-06T12:50:00+00:00", "data": {}}

SEND_TURN = [
    {"type": "message-delta", "text": "Sending the bot."},
    {"type": "artifact", "path": "meeting:118", "pin": True, "focus": True,
     "native": "cqb-egsq-vmt"},
    {"type": "turn-complete"},
]


# ── the recipe, on the side that writes ─────────────────────────────────────────────────────────

def test_an_untitled_meeting_is_named_by_its_room_not_by_nothing():
    """A chat that sends a bot to a Meet link names nothing, so this is the ORDINARY case here.
    Two untitled meetings would otherwise share one filename and the second would silently adopt
    the first's page."""
    assert meeting_mint.compose(ROW) == \
        "kg/entities/meeting/2026-09-06-1250-google-meet-meeting-cqb-egsq-vmt.md"
    other = {**ROW, "id": 119, "native_meeting_id": "zzz-zzzz-zzz"}
    assert meeting_mint.compose(other) != meeting_mint.compose(ROW)


def test_two_occurrences_of_one_recurring_meeting_are_two_files():
    """`%Y-%m-%d-%H%M`, for the reason `core/flows`' `_meeting_stamp` gives: a recurring meeting
    keeps ONE title across occurrences, and a name that collides overwrites the morning with the
    afternoon while nothing fails."""
    morning = {"id": 1, "native_meeting_id": "n", "data": {"title": "DNA TSC",
                                                           "scheduled_at": "2026-03-02T09:00:00Z"}}
    evening = {**morning, "id": 2, "data": {**morning["data"],
                                            "scheduled_at": "2026-03-02T17:30:00Z"}}
    assert meeting_mint.compose(morning) == "kg/entities/meeting/2026-03-02-0900-dna-tsc.md"
    assert meeting_mint.compose(evening) == "kg/entities/meeting/2026-03-02-1730-dna-tsc.md"


@pytest.mark.parametrize("title", ["../../.ssh/authorized_keys", "a/b", ".hidden", "   ", ""])
def test_a_title_can_never_name_a_file_outside_the_meeting_folder(title):
    """A title is attacker-adjacent text off an invite anybody in the room can edit. The guard is
    the ALPHABET, not a blacklist somebody has to keep complete."""
    path = meeting_mint.compose({"id": 9, "native_meeting_id": "n", "data": {"title": title}})
    assert meeting_note.is_note_path(path)
    assert ".." not in path and path.count("/") == 3


# ── the record: what a row says about where its page is ─────────────────────────────────────────

def test_the_row_carries_the_path_and_the_reader_takes_it():
    row = {**ROW, "data": {"metadata": {"note_path": "kg/entities/meeting/2026-09-06-1250-x.md"}}}
    assert meeting_note.recorded_path(row) == "kg/entities/meeting/2026-09-06-1250-x.md"


@pytest.mark.parametrize("recorded", [
    "kg/entities/meeting/../../../etc/passwd",     # out of the folder
    "kg/entities/meeting/sub/dir.md",              # a second segment
    "/etc/passwd",                                 # absolute
    "kg/entities/meeting/index.md",                # the folder's index, which the flow maintains
    "kg/entities/person/ada.md",                   # a page, just not a meeting's
    "kg/entities/meeting/.git",
])
def test_a_recorded_path_that_is_not_one_of_ours_is_refused(recorded):
    """The record rides an owner-scoped annotation an account's own API key can write, and it names
    a file written onto EVERY desk in the room. It is checked, never trusted."""
    assert meeting_note.recorded_path({**ROW, "data": {"metadata": {"note_path": recorded}}}) == ""
    assert not meeting_note.is_note_path(recorded)


def test_a_row_nobody_minted_simply_carries_no_path():
    """The ordinary answer for every meeting that predates this — the scan is what answers there."""
    assert meeting_note.recorded_path(ROW) == ""
    assert meeting_note.recorded_path({"id": 1, "data": {"metadata": {}}}) == ""
    assert meeting_note.recorded_path(None) == ""


def test_the_note_route_answers_from_the_record_before_it_scans(tmp_path):
    """The record is tier 0: exact, and the only tier that can name a page whose title nobody ever
    matched. Here the desk holds a page the scan would MISS — the row is untitled and the file
    carries no ids — and the record still finds it."""
    desk = tmp_path / SUBJECT / meeting_note.MEETING_DIR
    desk.mkdir(parents=True)
    (desk / "2026-09-06-1250-whatever.md").write_text("---\ntype: meeting\n---\n\n# whatever\n")
    plain = meeting_note.resolve(tmp_path, SUBJECT, ROW)
    assert plain is None, "the scan cannot match an untitled row against an unstamped file"
    recorded = {**ROW, "data": {"metadata":
                                {"note_path": "kg/entities/meeting/2026-09-06-1250-whatever.md"}}}
    assert meeting_note.resolve(tmp_path, SUBJECT, recorded) == \
        "kg/entities/meeting/2026-09-06-1250-whatever.md"


def test_a_recorded_page_that_is_not_on_THIS_desk_is_not_answered(tmp_path):
    """The record is ONE fact about the meeting, shared by everybody in the room. An attendee whose
    drop has not run yet has no such file, and a tab onto a path nobody wrote on this desk is the
    exact failure `meeting_note` exists to refuse."""
    (tmp_path / SUBJECT / meeting_note.MEETING_DIR).mkdir(parents=True)
    recorded = {**ROW, "data": {"metadata":
                                {"note_path": "kg/entities/meeting/2026-09-06-1250-gone.md"}}}
    assert meeting_note.resolve(tmp_path, SUBJECT, recorded) is None


# ── the mint itself ─────────────────────────────────────────────────────────────────────────────

def test_the_minted_page_is_the_meetings_own_page(tmp_path):
    out = meeting_mint.mint(tmp_path, SUBJECT, ROW)
    assert out["created"] is True
    doc = (tmp_path / SUBJECT / out["path"]).read_text()
    # the frontmatter the widget, the resolver and Expand all read
    assert "type: meeting" in doc and "meeting: 118" in doc and "native: cqb-egsq-vmt" in doc
    assert "title: Google Meet meeting" in doc and "date: 2026-09-06" in doc
    assert "transcript_cursor:" in doc
    # the slot — the live transcript renders IN this page, which is what makes it the whole room
    assert "<!-- vexa:transcript meeting=118 -->" in doc
    # …and the empty regions the flow's report and Expand write between
    for key in ("about", "decisions", "commitments", "people", "questions", "report"):
        assert f"<!-- meeting:{key}:start -->" in doc and f"<!-- meeting:{key}:end -->" in doc


def test_a_page_that_is_already_there_is_never_rewritten(tmp_path):
    """THE SAFETY PROPERTY. The page is written by three hands after the mint — the person, their
    Expand, and the flow's report — so a mint that refreshed it would delete somebody's writing at
    the moment the room got busy."""
    first = meeting_mint.mint(tmp_path, SUBJECT, ROW)
    f = tmp_path / SUBJECT / first["path"]
    grown = f.read_text().replace("<!-- meeting:decisions:start -->",
                                  "<!-- meeting:decisions:start -->\n- ship it")
    f.write_text(grown + "\nI still owe Cara the doc.\n")
    kept = f.read_text()
    again = meeting_mint.mint(tmp_path, SUBJECT, ROW)
    assert again == {"path": first["path"], "created": False}
    assert f.read_text() == kept


def test_the_record_is_what_stops_a_RENAMED_meeting_growing_a_second_page(tmp_path):
    """And this is why the path is recorded rather than re-composed. Somebody annotates the meeting
    an hour in — a title is exactly the fact people add — and a mint that composed again would open
    a second, empty page beside the one they have been writing in all meeting."""
    first = meeting_mint.mint(tmp_path, SUBJECT, ROW)
    renamed = {**ROW, "data": {"title": "Acme renewal call"}}
    assert meeting_mint.compose(renamed) != first["path"], "the composition follows the title"
    recorded = {**renamed, "data": {**renamed["data"], "metadata": {"note_path": first["path"]}}}
    assert meeting_mint.mint(tmp_path, SUBJECT, recorded) == {"path": first["path"],
                                                              "created": False}
    folder = tmp_path / SUBJECT / meeting_note.MEETING_DIR
    assert [f.name for f in folder.glob("*.md")] == [first["path"].rsplit("/", 1)[-1]]


def test_the_first_mint_records_the_path_and_the_second_does_not(tmp_path):
    """The FIRST mint decides. Re-recording would let a later composition move a page out from
    under a reader who has it open."""
    calls: list[tuple] = []
    out = meeting_mint.mint(tmp_path, SUBJECT, ROW,
                            record=lambda s, m, p: calls.append((s, m, p)))
    assert calls == [(SUBJECT, "118", out["path"])]
    already = {**ROW, "data": {"metadata": {"note_path": out["path"]}}}
    meeting_mint.mint(tmp_path, SUBJECT, already, record=lambda s, m, p: calls.append((s, m, p)))
    assert len(calls) == 1


def test_a_recorded_path_is_where_the_page_goes(tmp_path):
    """ONE SPELLING. A row that already names its page is minted onto THAT name, so an attendee's
    desk and the organiser's carry the same file."""
    row = {**ROW, "data": {"metadata": {"note_path": "kg/entities/meeting/2026-09-06-1250-x.md"}}}
    assert meeting_mint.mint(tmp_path, SUBJECT, row)["path"] == \
        "kg/entities/meeting/2026-09-06-1250-x.md"


def test_a_row_with_no_id_mints_nothing(tmp_path):
    """A widget naming no meeting is a box with no room behind it."""
    assert meeting_mint.mint(tmp_path, SUBJECT, {"native_meeting_id": "n"}) == \
        {"path": None, "created": False}


def test_a_failed_record_still_leaves_the_page_on_the_desk(tmp_path):
    """The page is what the person is looking at; the record is bookkeeping for the flow."""
    def boom(*_a):
        raise RuntimeError("meeting-api said no")

    out = meeting_mint.mint(tmp_path, SUBJECT, ROW, record=boom)
    assert out["created"] is True and (tmp_path / SUBJECT / out["path"]).is_file()


# ── the turn: a send puts the page on the desk ──────────────────────────────────────────────────

class _FakeRuntime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


class _SendingReader:
    def __init__(self, events):
        self.events = events

    def read(self, unit_id, resume=None):
        yield from self.events


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    (root / SUBJECT).mkdir(parents=True)
    return {"root": root, "recorded": [], "rows": {"118": ROW}}


def _client(stack, events=None):
    settings = load_settings(
        workspaces_dir=str(stack["root"]),
        global_system_workspace_path=str(stack["root"] / "_global"),
        internal_api_secret=INTERNAL, ui_url="https://app.example.test", redis_url="")
    app = create_app(
        Dispatcher(settings, _FakeRuntime(), _FakeIdentity()),
        stream_reader=_SendingReader(events if events is not None else SEND_TURN),
        reader=WorkspaceReader(str(stack["root"])), sessions=_Sessions(),
        meeting_owner_lookup=lambda subject, mid: (stack["rows"].get(str(mid))
                                                   if subject == SUBJECT else None),
        meeting_note_recorder=lambda s, m, p: stack["recorded"].append((s, str(m), p)) or True)
    return TestClient(app)


def _send(client):
    return client.post("/api/chat",
                       json={"prompt": "send a bot to https://meet.google.com/cqb-egsq-vmt",
                             "session": "pchat-abc"}, headers={"X-User-Id": SUBJECT})


def _page(stack) -> Path:
    folder = stack["root"] / SUBJECT / meeting_note.MEETING_DIR
    return next(iter(sorted(folder.glob("*.md"))), folder / "nothing.md")


def test_the_send_puts_the_meetings_page_on_the_senders_desk(stack):
    """The founder's own question, answered: he sends the bot and the doc is there."""
    assert _send(_client(stack)).status_code == 200
    doc = _page(stack).read_text()
    assert "<!-- vexa:transcript meeting=118 -->" in doc
    assert "<!-- meeting:report:start -->" in doc


def test_the_page_is_on_the_desk_BEFORE_the_client_is_told(stack):
    """THE ORDERING IS THE FEATURE. The client binds off this same `artifact` event and immediately
    asks `/api/meeting/note` where the page is — minting after the relay would race its own consumer
    and answer `null` on the send that just created it."""
    client = _client(stack)
    seen: list[bool] = []
    with client.stream("POST", "/api/chat",
                       json={"prompt": "send a bot", "session": "pchat-abc"},
                       headers={"X-User-Id": SUBJECT}) as r:
        for line in r.iter_lines():
            if "meeting:118" in line:
                seen.append(_page(stack).is_file())
    assert seen == [True]


def test_the_path_is_recorded_on_the_meeting_row(stack):
    _send(_client(stack))
    assert stack["recorded"] == [(SUBJECT, "118", str(_page(stack).relative_to(
        stack["root"] / SUBJECT)).replace("\\", "/"))]


def test_the_note_route_then_answers_with_that_page_and_its_widget(stack):
    """End to end, and this is the whole issue: the page exists, the room is told where it is, and
    the page declares the transcript — so the room is ONE page (#1598) from the send onward."""
    client = _client(stack)
    _send(client)
    stack["rows"]["118"] = {**ROW, "data": {"metadata": {"note_path": stack["recorded"][0][2]}}}
    body = client.get("/api/meeting/note?meeting_id=118",
                      headers={"X-User-Id": SUBJECT}).json()
    assert body == {"path": stack["recorded"][0][2], "transcript": "118", "cursor": ""}


def test_a_turn_that_sends_nothing_mints_nothing(stack):
    client = _client(stack, [{"type": "message-delta", "text": "hi"}, {"type": "turn-complete"}])
    assert client.post("/api/chat", json={"prompt": "hi", "session": "s"},
                       headers={"X-User-Id": SUBJECT}).status_code == 200
    assert not (stack["root"] / SUBJECT / meeting_note.MEETING_DIR).exists()
    assert stack["recorded"] == []


def test_a_meeting_this_caller_cannot_read_costs_them_a_page_never_the_turn(stack):
    """`_meeting_owner_lookup` fails closed, and so does this: no facts, no page, and the turn the
    person is waiting for is untouched."""
    stack["rows"] = {}
    r = _send(_client(stack))
    assert r.status_code == 200 and "turn-complete" in r.text
    assert not (stack["root"] / SUBJECT / meeting_note.MEETING_DIR).exists()


# ── the door for the caller that is not a chat ──────────────────────────────────────────────────

def test_the_mint_route_is_how_a_meeting_with_no_chat_gets_its_page(stack):
    """`core/flows` calls this at row creation for a mailbox invite — the same act, asked for."""
    client = _client(stack)
    out = client.post("/api/meeting/note", json={"meeting_id": "118"},
                      headers={"X-User-Id": SUBJECT}).json()
    assert out["created"] is True
    assert "<!-- vexa:transcript meeting=118 -->" in (stack["root"] / SUBJECT / out["path"]).read_text()
    assert stack["recorded"] == [(SUBJECT, "118", out["path"])]
    # …and asking twice is asking once
    assert client.post("/api/meeting/note", json={"meeting_id": "118"},
                       headers={"X-User-Id": SUBJECT}).json()["created"] is False


def test_the_mint_route_refuses_a_meeting_the_caller_does_not_own(stack):
    """Owner-scoped before anything is written — row ids are sequential ints and this creates a
    file on a DESK."""
    r = _client(stack).post("/api/meeting/note", json={"meeting_id": "118"},
                            headers={"X-User-Id": "u_someone_else"})
    assert r.status_code == 403
    assert not (stack["root"] / "u_someone_else").exists()


def test_a_proposed_path_that_is_not_a_meeting_page_is_ignored_not_obeyed(stack):
    r = _client(stack).post("/api/meeting/note",
                            json={"meeting_id": "118", "path": "../../etc/passwd"},
                            headers={"X-User-Id": SUBJECT})
    assert r.json()["path"].startswith("kg/entities/meeting/")
