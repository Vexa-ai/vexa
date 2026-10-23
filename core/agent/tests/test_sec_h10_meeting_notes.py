"""H10 for a meeting's page: binding a page to its meeting, minting it on request, and the note a
room's call gets when its signal wakes the room's agent all say an agent wrote them when one did."""
from __future__ import annotations

from fastapi.testclient import TestClient

from control_plane.api import _Sessions, create_app
from control_plane.workspace_reader import WorkspaceReader
from tests.test_meeting_page_binding import AGENDA, JANE, SLOT, _agenda, _client, _root, _row
from tests.test_r1_rooms import SECRET, _FakeReader, _room, world  # noqa: F401
from tests.test_sec_h10_commit_sites import assert_agents, assert_persons

UNIT = "agent-jane-chat-c4"
AGENT_OF_JANE = {"X-User-Id": JANE, "x-user-regime": "human", "x-user-delegation-unit": UNIT}
SUBJ = f"{SLOT}: {AGENDA}"[:40]


def _attach(tmp_path, headers):
    root, _desk, other = _root(tmp_path)
    _agenda(other)
    c = _client(root, {"143": _row(143, "abc-defg-hij", "https://meet.google.com/abc-defg-hij", "google_meet")}, [])
    r = c.post("/api/meeting/note/attach", headers=headers,
               json={"path": AGENDA, "workspace": SLOT, "meeting_id": "143"})
    assert r.status_code == 200, r.text
    return other


def test_a_page_an_agent_binds_to_its_meeting_is_attributed(tmp_path):
    assert_agents(_attach(tmp_path, AGENT_OF_JANE), SUBJ, person=JANE, unit=UNIT)


def test_a_page_the_person_binds_carries_no_agent_trailer(tmp_path):
    assert_persons(_attach(tmp_path, {"X-User-Id": JANE}), SUBJ)


def _mint(tmp_path, headers):
    root, desk, _other = _root(tmp_path)
    c = _client(root, {"143": _row(143, "abc-defg-hij", "https://meet.google.com/abc-defg-hij", "google_meet")}, [])
    r = c.post("/api/meeting/note", headers=headers, json={"meeting_id": "143"})
    assert r.status_code == 200 and r.json()["created"] is True, r.text
    return desk


def test_a_meeting_page_an_agent_asks_to_mint_is_attributed(tmp_path):
    assert_agents(_mint(tmp_path, AGENT_OF_JANE), f"{JANE}: kg/entities/meeting/", person=JANE, unit=UNIT)


def test_a_meeting_page_the_person_mints_carries_no_agent_trailer(tmp_path):
    assert_persons(_mint(tmp_path, {"X-User-Id": JANE}), f"{JANE}: kg/entities/meeting/")


def test_the_note_a_rooms_call_gets_when_its_signal_wakes_the_agent_is_attributed(world):
    """The platform makes the call's note in the room's workspace for the room agent's turn, on
    behalf of the person whose call woke it (the internal tier names them)."""
    client, store, calls, spawned, root, dispatcher = world
    (root / "_global" / "POLICIES.md").write_text("---\ntranscript_signals: on\n---\n")
    sid = _room(client)
    rows = {"77": {"id": 77, "native_meeting_id": "call-x", "platform": "vexa_call", "data": {"title": "Call"}}}
    app = create_app(dispatcher, stream_reader=_FakeReader(), reader=WorkspaceReader(str(root)),
                     sessions=_Sessions(), session_store=store,
                     meeting_owner_lookup=lambda uid, mid, workspaces=None: rows.get(str(mid)) if uid == "alice" else None)
    ev = {"name": "meeting.signal", "subject": "alice", "target": {"chat": sid},
          "plan": {"prompt": "Signals since your last look."},
          "data": {"meeting_row": "77", "score": 1.0, "reason": "addressed",
                   "buffer": [{"segment_id": "77:1", "speaker": "A", "text": "Vexa, note that", "start": 1.0}]}}
    r = TestClient(app, raise_server_exceptions=False).post("/events", json=ev, headers={"X-Internal-Secret": SECRET})
    assert r.status_code == 202, r.text
    assert_agents(root / "team", "meeting note for call 77", person="alice", unit="")
