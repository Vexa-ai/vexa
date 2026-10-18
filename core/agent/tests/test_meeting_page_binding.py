"""A PAGE IS BOUND TO ITS MEETING BY IDENTITY, NEVER BY A LOOK-ALIKE TITLE (#1783).

Founder, 2026-10-15: "why not map page to meeting reliably?" His agent wrote an agenda page for a
Teams call before the bot was sent; the bot's mint then made a second page and the report went there.
`meeting_attach_page` stamps the meeting's identity on a page (before the meeting exists: its native id
or link) or binds it to an existing meeting (recorded on the row with its workspace); the mint adopts an
exact match; one meeting has one page.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from control_plane import meeting_mint, meeting_note
from control_plane import workspace_membership as m
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

from tests.test_api import _FakeIdentity, _FakeRuntime

JANE = "u_jane"
SLOT = "oenb-pilot"
AGENDA = "kg/entities/meeting/vexa-channel-call-15-oct-2026.md"
TEAMS_URL = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc%40thread.v2/0?context=%7b%7d"
MEET_URL = "https://meet.google.com/jmk-qxpo-yzg"


def _git_init(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    for a in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(p), *a], check=True, capture_output=True)
    (p / "README.md").write_text("hi\n")
    subprocess.run(["git", "-C", str(p), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(p), "commit", "-qm", "seed"], check=True, capture_output=True)
    return p


def _root(tmp_path: Path):
    root = tmp_path / "workspaces"
    desk = _git_init(root / JANE)
    store = root / ".attached" / JANE
    other = _git_init(store / SLOT)
    (store / "state.json").write_text(json.dumps({
        "_flat_v1": True, "active": None, "hidden": [], "active_set": ["seed", SLOT],
        "slots": {"seed": {}, SLOT: {"name": "OeNB pilot"}}}))
    (root / "_global").mkdir(exist_ok=True)
    return root, desk, other


def _agenda(d: Path, extra: str = "") -> None:
    (d / AGENDA).parent.mkdir(parents=True, exist_ok=True)
    (d / AGENDA).write_text(f"---\ntype: meeting\ntitle: Vexa Channel call\n{extra}---\n\n## Agenda\n- pilot users\n")


def _client(root: Path, rows: dict, recorded: list) -> TestClient:
    settings = load_settings(workspaces_dir=str(root), global_system_workspace_path=str(root / "_global"),
                             internal_api_secret="s", ui_url="https://app.example.test", redis_url="")
    return TestClient(create_app(
        Dispatcher(settings, _FakeRuntime(), _FakeIdentity()), reader=WorkspaceReader(str(root)),
        membership_index=m.InMemoryMembershipIndex(),
        meeting_owner_lookup=lambda subject, mid, *_a, **_k: rows.get(str(mid)) if subject == JANE else None,
        meeting_note_recorder=lambda *a: recorded.append(a) or True))


def _row(mid: int, native: str, url: str, platform: str) -> dict:
    return {"id": mid, "user_id": JANE, "platform": platform, "native_meeting_id": native,
            "start_time": "2026-10-15T17:00:00+00:00", "data": {"constructed_meeting_url": url}}


def test_a_teams_page_made_before_the_bot_is_adopted_when_the_meeting_is_created(tmp_path):
    root, desk, other = _root(tmp_path)
    _agenda(other)
    recorded: list = []
    c = _client(root, {}, recorded)
    r = c.post("/api/meeting/note/attach", headers={"X-User-Id": JANE},
               json={"path": AGENDA, "workspace": SLOT, "meeting_url": TEAMS_URL, "native": "9b1f0c2a7d", "platform": "teams"})
    assert r.status_code == 200, r.text
    fm = meeting_note.front_matter((other / AGENDA).read_text())
    assert fm["native"] == "9b1f0c2a7d" and fm["meeting_url"] == meeting_note.normal_url(TEAMS_URL)
    # the bot goes in: its row carries the same native id; the mint ADOPTS the page, makes none
    row = _row(140, "9b1f0c2a7d", TEAMS_URL, "teams")
    rec: list = []
    out = meeting_mint.mint(root, JANE, row, record=lambda *a: rec.append(a))
    assert out["path"] == AGENDA and out["workspace"] == SLOT and out["created"] is False
    assert rec == [(JANE, "140", AGENDA, SLOT)]
    assert not (desk / "kg/entities/meeting").exists(), "a second page for the same meeting"


def test_a_meet_page_is_adopted_by_its_link(tmp_path):
    root, desk, other = _root(tmp_path)
    _agenda(desk, f"meeting_url: {MEET_URL}?authuser=0\n")
    row = _row(141, "jmk-qxpo-yzg", "https://meet.google.com/jmk-qxpo-yzg", "google_meet")
    out = meeting_mint.mint(root, JANE, row, record=lambda *a: None)
    assert out["path"] == AGENDA and out["created"] is False
    assert sorted(p.name for p in (desk / "kg/entities/meeting").iterdir()) == [Path(AGENDA).name]


def test_a_title_that_looks_alike_is_not_adopted(tmp_path):
    root, desk, other = _root(tmp_path)
    _agenda(other)                               # no identity at all
    row = _row(142, "zzz-zzzz-zzz", "https://meet.google.com/zzz-zzzz-zzz", "google_meet")
    row["data"]["title"] = "Vexa Channel call"
    assert meeting_note.resolve_exact_in(other, row) is None


def test_manual_attach_binds_an_existing_meeting_and_the_sidebar_reads_that_page(tmp_path):
    root, desk, other = _root(tmp_path)
    _agenda(other)
    row = _row(143, "abc-defg-hij", "https://meet.google.com/abc-defg-hij", "google_meet")
    recorded: list = []
    c = _client(root, {"143": row}, recorded)
    r = c.post("/api/meeting/note/attach", headers={"X-User-Id": JANE},
               json={"path": AGENDA, "workspace": SLOT, "meeting_id": "143"})
    assert r.status_code == 200, r.text
    assert recorded and recorded[-1][2:] == (AGENDA, SLOT)
    fm = meeting_note.front_matter((other / AGENDA).read_text())
    assert fm["meeting"] == "143" and fm["native"] == "abc-defg-hij"
    # the row now records the page and its workspace: describe answers it, in that workspace
    row["data"]["metadata"] = {"note_path": AGENDA, "note_workspace": SLOT}
    got = meeting_note.describe(root, JANE, row)
    assert got["path"] == AGENDA and got["workspace"] == SLOT


def test_one_meeting_one_page_a_second_page_is_refused_until_merged(tmp_path):
    root, desk, other = _root(tmp_path)
    first = "kg/entities/meeting/2026-10-15-1700-teams-meeting-abc.md"
    (desk / first).parent.mkdir(parents=True, exist_ok=True)
    (desk / first).write_text("---\ntype: meeting\nmeeting: 144\n---\n\n## Decisions\n- ship\n")
    _agenda(other)
    row = _row(144, "abc", "https://teams.microsoft.com/x", "teams")
    row["data"]["metadata"] = {"note_path": first, "note_workspace": ""}
    c = _client(root, {"144": row}, [])
    r = c.post("/api/meeting/note/attach", headers={"X-User-Id": JANE},
               json={"path": AGENDA, "workspace": SLOT, "meeting_id": "144"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["current"] == {"path": first, "workspace": ""}
    r = c.post("/api/meeting/note/attach", headers={"X-User-Id": JANE},
               json={"path": AGENDA, "workspace": SLOT, "meeting_id": "144", "replace": True})
    assert r.status_code == 200, r.text


def test_attach_refuses_a_page_outside_the_meeting_folder_and_a_meeting_it_cannot_read(tmp_path):
    root, desk, other = _root(tmp_path)
    c = _client(root, {}, [])
    r = c.post("/api/meeting/note/attach", headers={"X-User-Id": JANE},
               json={"path": "notes/agenda.md", "meeting_id": "1"})
    assert r.status_code == 400
    _agenda(desk)
    r = c.post("/api/meeting/note/attach", headers={"X-User-Id": JANE}, json={"path": AGENDA, "meeting_id": "999"})
    assert r.status_code == 403
