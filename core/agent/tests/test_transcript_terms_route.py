"""`transcript_terms` as an agent-api route — the Highlight button on a standard deployment.

`behavior/asks/highlight.md`, `extend-transcript.md` and `extend-meeting.md` call
`transcript_terms(meeting_id, since)` to LOOK at what a meeting named, and again with `keep=` to
PUBLISH the chips. The extractor (`shared/terms.py`) and the durable map (`meeting_terms.py`,
`POST /api/meeting/terms`) were agent-api's; the composition that reads the transcript, matches it
against the reader's entity index and publishes was the dogfood rig's alone, so on a standard
deployment Highlight painted nothing.

What has to be true:

* `POST /api/meeting/terms/scan` reads the meeting's transcript AS THE CALLER, from `since`, and
  answers every term with whether a page for it exists where THIS reader can read it — desk first;
* a first call publishes NOTHING (`emit: []`) — a list nobody judged never reaches a screen;
* `keep=` publishes exactly those terms, durably (the canvas re-reads them on reload), `keep="*"`
  all of them, and names any kept term the room never said;
* the answer has the shape the harness turns into the chat's `terms` event (`meeting`, `cursor`,
  `emit`), and the manifest serves the route as `transcript_terms`;
* a meeting the caller may not read is a 403, and a transcript that could not be read is said to be
  a failed READ — never an empty room.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from llm.tool_events import _published_terms
from shared.config import load_settings

from tests.test_api import _FakeIdentity, _FakeRuntime

AGENT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((AGENT / "mcp.tools.v1.json").read_text())
JANE = "u_jane"
ROW = "147"
SEGMENTS = [
    {"start": 1.0, "absolute_start_time": "2026-10-09T10:00:01Z", "speaker": "Ana",
     "text": "Northwind Labs came back on the pricing."},
    {"start": 5.0, "absolute_start_time": "2026-10-09T10:00:05Z", "speaker": "Ben",
     "text": "Robin Vale is the one who signs it."},
]


@pytest.fixture()
def world(tmp_path):
    desk = tmp_path / JANE
    (desk / "kg/entities/company").mkdir(parents=True)
    (desk / "kg/entities/company/northwind-labs.md").write_text("# Northwind Labs\n")
    reads: list = []
    state = {"down": False}

    def _owner(user_id, meeting_id, workspaces=None):
        return {"id": meeting_id, "native_meeting_id": "abc-defg-hij"} if user_id == JANE else None

    def _transcript(user_id, meeting_id, workspaces=None):
        reads.append((user_id, meeting_id))
        if state["down"]:
            return None
        return list(SEGMENTS) if user_id == JANE else None

    client = TestClient(create_app(
        Dispatcher(load_settings(workspaces_dir=str(tmp_path)), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(tmp_path)), meeting_owner_lookup=_owner,
        meeting_transcript_lookup=_transcript))
    return client, reads, state


def _scan(client, subject=JANE, **body):
    return client.post("/api/meeting/terms/scan", headers={"X-User-Id": subject},
                       json={"meeting_id": ROW, **body})


def test_the_manifest_serves_the_scan_as_transcript_terms():
    tool = next((t for t in MANIFEST["tools"] if t["name"] == "transcript_terms"), None)
    assert tool is not None, "transcript_terms is named by the Highlight prompts and not served"
    assert tool["route"] == {"method": "POST", "path": "/api/meeting/terms/scan"}
    assert tool["arguments"] == ["meeting_id", "since", "keep"]


def test_a_look_lists_every_term_and_publishes_nothing(world):
    client, reads, _ = world
    r = _scan(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert reads == [(JANE, ROW)], "the transcript is read as the caller"
    assert [t["term"] for t in body["terms"]] == ["Northwind Labs", "Robin Vale"]
    known = {t["term"]: t["known"] for t in body["terms"]}
    assert known["Northwind Labs"]["path"] == "kg/entities/company/northwind-labs.md"
    assert known["Robin Vale"] is None
    assert body["emit"] == [] and body["published"] == 0 and body["stored"] is None
    assert body["cursor"] == "2026-10-09T10:00:05Z"
    assert _published_terms(json.dumps(body)) is None, "a look must not paint a chip"
    got = client.get(f"/api/meeting/terms?meeting_id={ROW}", headers={"X-User-Id": JANE}).json()
    assert got["terms"] == []


def test_keep_publishes_exactly_those_and_they_survive_a_reload(world):
    client, _, _ = world
    r = _scan(client, keep="northwind labs, Nobody Said This")
    body = r.json()
    assert [t["term"] for t in body["emit"]] == ["Northwind Labs"]
    assert body["stored"] is True
    assert body["keep_not_found"] == ["nobody said this"]
    event = _published_terms(json.dumps(body))
    assert event == {"type": "terms", "meeting": ROW, "cursor": "2026-10-09T10:00:05Z",
                     "terms": body["emit"]}
    got = client.get(f"/api/meeting/terms?meeting_id={ROW}", headers={"X-User-Id": JANE}).json()
    assert [t["term"] for t in got["terms"]] == ["Northwind Labs"]


def test_keep_star_publishes_all_of_them(world):
    client, _, _ = world
    assert [t["term"] for t in _scan(client, keep="*").json()["emit"]] == \
        ["Northwind Labs", "Robin Vale"]


def test_since_reads_only_what_was_said_after_the_cursor(world):
    client, _, _ = world
    body = _scan(client, since="2026-10-09T10:00:01Z").json()
    assert [t["term"] for t in body["terms"]] == ["Robin Vale"]
    assert body["scanned_segments"] == 1


def test_a_meeting_the_caller_may_not_read_is_refused(world):
    client, reads, _ = world
    assert _scan(client, subject="u_mallory").status_code == 403
    assert reads == [], "nothing was read for a caller with no claim on the meeting"


def test_a_transcript_that_could_not_be_read_is_a_failed_read_not_a_quiet_room(world):
    client, _, state = world
    state["down"] = True
    r = _scan(client)
    assert r.status_code == 502
    assert r.json()["detail"]["read_ok"] is False
