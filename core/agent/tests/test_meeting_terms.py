"""THE ANNOTATION LAYER OVER A TRANSCRIPT, AND WHY IT HAS TO OUTLIVE THE TURN (Vexa-ai/vexa#1595).

Founder, 2026-09-06, in a live Google Meet with the canvas open and Highlight pressed: *"we want
transcript being attributed with extracted entities when we get highlight — it should attribute the
transcript in an efficient way (no rewrite)"*.

Everything worth a test here fails SILENTLY, and each one reaches the person as chips that are
simply not there:

  · a merge that REPLACES instead of adding — the second Highlight of a long meeting wipes the
    first, and the transcript looks like the button stopped working;
  · a publish that is not idempotent — pressing again duplicates the map, and the file grows once
    per press for a room that said nothing new;
  · an EMPTY publish that is written — "and now there are none", over chips already on screen;
  · a cursor that moves on an empty publish — the next Highlight skips the stretch it was going to
    read, and those terms are never offered again;
  · a map that does not round-trip — the reload the whole route exists for still shows plain text;
  · a row id from a caller reaching the filesystem as a path.

The transcript itself is never written here, in any test, because it is never written at all: that
is what "no rewrite" means and the storage shape is what enforces it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control_plane import meeting_terms  # noqa: E402
from control_plane.api import create_app  # noqa: E402
from control_plane.dispatch import Dispatcher  # noqa: E402
from control_plane.workspace_reader import WorkspaceReader  # noqa: E402
from shared.config import load_settings  # noqa: E402

JANE = "u_jane"
ROW = "147"


def known(term: str, kind: str = "company") -> dict:
    slug = term.lower().replace(" ", "-")
    return {"term": term, "kind": kind, "segments": ["s1"], "first_at": "2026-09-06T11:00:00Z",
            "known": {"workspace_id": "w1", "entity_id": slug, "path": f"kg/entities/{kind}/{slug}.md"}}


def unknown(term: str, *segments: str) -> dict:
    return {"term": term, "known": None, "segments": list(segments) or ["s1"]}


# ── the merge: a second Highlight ADDS ────────────────────────────────────────────────────────────

def test_a_second_publish_adds_and_never_removes():
    out = meeting_terms.merge([unknown("Kaar Tech")], [unknown("Blue Light Card")])
    assert [t["term"] for t in out] == ["Kaar Tech", "Blue Light Card"]


def test_the_same_term_twice_is_one_row_however_it_was_spelled():
    """One chip, and the LATER spelling wins — the same rule as the client's `mergeTerms`. The
    casing is cosmetic either way: the renderer matches case-insensitively and draws the words the
    transcript actually used, never the stored spelling."""
    out = meeting_terms.merge([unknown("Kaar Tech")], [unknown("kaar   tech")])
    assert len(out) == 1 and out[0]["term"] == "kaar tech"


def test_a_later_answer_about_known_wins_including_a_later_null():
    assert meeting_terms.merge([unknown("Kaar Tech")], [known("Kaar Tech")])[0]["known"]
    # the page could have been deleted; a chip that stays solid over a page that is gone is the
    # "opens nothing" failure the link resolver already refuses
    assert meeting_terms.merge([known("Kaar Tech")], [unknown("Kaar Tech")])[0]["known"] is None


def test_a_publish_that_answers_less_does_not_erase_what_was_already_answered():
    """`kind` and `first_at` are absent from a row the publisher had nothing new to say about — the
    earlier answer must survive, or a re-press would strip a chip's colour and its provenance."""
    out = meeting_terms.merge([known("Kaar Tech")], [{"term": "Kaar Tech", "known": None}])
    assert out[0]["kind"] == "company"
    assert out[0]["first_at"] == "2026-09-06T11:00:00Z"


def test_segments_union_because_a_since_scoped_publish_only_carries_the_new_stretch():
    out = meeting_terms.merge([unknown("Kaar Tech", "s1", "s2")], [unknown("Kaar Tech", "s2", "s9")])
    assert out[0]["segments"] == ["s1", "s2", "s9"]


def test_the_stored_shape_is_closed():
    """The route takes JSON from a caller and this file is read straight back into a render loop."""
    out = meeting_terms.merge([], [{"term": "Kaar Tech", "known": None, "onclick": "alert(1)",
                                    "segments": "not-a-list"}])
    assert set(out[0]) == {"term", "known"}


def test_a_one_character_term_is_not_a_name():
    assert meeting_terms.merge([], [unknown("A")]) == []


def test_the_map_is_bounded():
    many = [unknown(f"Company {i}") for i in range(meeting_terms.MAX_TERMS + 50)]
    assert len(meeting_terms.merge([], many)) == meeting_terms.MAX_TERMS


# ── the store: it has to be there after a reload ─────────────────────────────────────────────────

def test_an_unhighlighted_meeting_answers_an_empty_map_not_a_failure(tmp_path):
    assert meeting_terms.read(tmp_path, JANE, ROW) == {"meeting": ROW, "cursor": "", "terms": []}


def test_the_map_round_trips_which_is_the_whole_point_of_the_route(tmp_path):
    meeting_terms.extend(tmp_path, JANE, ROW, [known("Kaar Tech"), unknown("Blue Light Card")], "c9")
    back = meeting_terms.read(tmp_path, JANE, ROW)
    assert [t["term"] for t in back["terms"]] == ["Kaar Tech", "Blue Light Card"]
    assert back["cursor"] == "c9"


def test_a_second_highlight_extends_the_stored_map(tmp_path):
    meeting_terms.extend(tmp_path, JANE, ROW, [unknown("Kaar Tech")], "c9")
    out = meeting_terms.extend(tmp_path, JANE, ROW, [unknown("Blue Light Card")], "c12")
    assert [t["term"] for t in out["terms"]] == ["Kaar Tech", "Blue Light Card"]
    assert out["cursor"] == "c12"


def test_publishing_the_same_thing_twice_does_not_even_touch_the_file(tmp_path):
    meeting_terms.extend(tmp_path, JANE, ROW, [known("Kaar Tech")], "c9")
    path = tmp_path / JANE / meeting_terms.TERMS_DIR / f"{ROW}.json"
    before = path.read_bytes(), path.stat().st_mtime_ns
    out = meeting_terms.extend(tmp_path, JANE, ROW, [known("Kaar Tech")], "c9")
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert len(out["terms"]) == 1


def test_an_empty_publish_is_a_non_event_and_does_not_move_the_cursor(tmp_path):
    meeting_terms.extend(tmp_path, JANE, ROW, [unknown("Kaar Tech")], "c9")
    out = meeting_terms.extend(tmp_path, JANE, ROW, [], "c99")
    assert [t["term"] for t in out["terms"]] == ["Kaar Tech"]
    assert out["cursor"] == "c9"


def test_a_publish_without_a_cursor_leaves_the_one_the_server_issued(tmp_path):
    meeting_terms.extend(tmp_path, JANE, ROW, [unknown("Kaar Tech")], "c9")
    assert meeting_terms.extend(tmp_path, JANE, ROW, [unknown("Acme")], "")["cursor"] == "c9"


def test_two_meetings_do_not_share_a_map(tmp_path):
    meeting_terms.extend(tmp_path, JANE, ROW, [unknown("Kaar Tech")], "c9")
    assert meeting_terms.read(tmp_path, JANE, "148")["terms"] == []


def test_the_map_is_kept_out_of_the_desks_history(tmp_path):
    """A file the worker's post-turn `git add -A` would commit once per Highlight — see
    `workspace_ids.mirror_touches`, which excludes the touch log for the same reason."""
    (tmp_path / JANE / ".git" / "info").mkdir(parents=True)
    meeting_terms.extend(tmp_path, JANE, ROW, [unknown("Kaar Tech")], "c9")
    assert f"/{meeting_terms.TERMS_DIR}/" in (tmp_path / JANE / ".git" / "info" / "exclude").read_text()


def test_a_map_that_cannot_be_parsed_costs_the_chips_never_the_transcript(tmp_path):
    path = tmp_path / JANE / meeting_terms.TERMS_DIR / f"{ROW}.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert meeting_terms.read(tmp_path, JANE, ROW)["terms"] == []


@pytest.mark.parametrize("bad", ["../../etc", "..", "a/b", ".vexa", "", "  "])
def test_a_caller_supplied_id_never_becomes_a_path(tmp_path, bad):
    assert meeting_terms.read(tmp_path, JANE, bad)["terms"] == []
    meeting_terms.extend(tmp_path, JANE, bad, [unknown("Kaar Tech")], "c9")
    assert not list(tmp_path.rglob("*.json"))


# ── the route the canvas asks ────────────────────────────────────────────────────────────────────

class _FakeRuntime:
    def spawn(self, workload_id, profile, env): return workload_id
    def await_done(self, workload_id, timeout_sec=0.0): return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools): return "tok"


@pytest.fixture()
def client(tmp_path) -> TestClient:
    (tmp_path / JANE).mkdir(parents=True)

    def _owner(user_id, meeting_id):
        """meeting-api owner-scopes in SQL: another tenant's row is a 404, i.e. None here."""
        return {"id": meeting_id, "native_meeting_id": "96088138284"} if str(user_id) == JANE else None

    return TestClient(create_app(
        Dispatcher(load_settings(workspaces_dir=str(tmp_path)), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(tmp_path)), meeting_owner_lookup=_owner))


def test_the_canvas_reads_an_empty_map_before_anybody_highlights(client):
    r = client.get(f"/api/meeting/terms?meeting_id={ROW}", headers={"X-User-Id": JANE})
    assert r.status_code == 200
    assert r.json() == {"meeting": ROW, "cursor": "", "terms": []}


def test_publish_then_read_is_the_reload(client):
    """The act publishes; a browser that knows nothing about the turn asks and gets the same map."""
    pub = client.post("/api/meeting/terms", headers={"X-User-Id": JANE},
                      json={"meeting_id": ROW, "cursor": "c9", "terms": [known("Kaar Tech")]})
    assert pub.status_code == 200 and pub.json()["cursor"] == "c9"
    got = client.get(f"/api/meeting/terms?meeting_id={ROW}", headers={"X-User-Id": JANE}).json()
    assert [t["term"] for t in got["terms"]] == ["Kaar Tech"]
    assert got["terms"][0]["known"]["path"] == "kg/entities/company/kaar-tech.md"


def test_a_second_publish_through_the_route_adds(client):
    client.post("/api/meeting/terms", headers={"X-User-Id": JANE},
                json={"meeting_id": ROW, "cursor": "c9", "terms": [unknown("Kaar Tech")]})
    client.post("/api/meeting/terms", headers={"X-User-Id": JANE},
                json={"meeting_id": ROW, "cursor": "c12", "terms": [unknown("Blue Light Card")]})
    got = client.get(f"/api/meeting/terms?meeting_id={ROW}", headers={"X-User-Id": JANE}).json()
    assert [t["term"] for t in got["terms"]] == ["Kaar Tech", "Blue Light Card"]


def test_another_tenant_can_neither_read_nor_write_this_meetings_map(client):
    client.post("/api/meeting/terms", headers={"X-User-Id": JANE},
                json={"meeting_id": ROW, "cursor": "c9", "terms": [unknown("Kaar Tech")]})
    assert client.get(f"/api/meeting/terms?meeting_id={ROW}",
                      headers={"X-User-Id": "u_mallory"}).status_code == 403
    assert client.post("/api/meeting/terms", headers={"X-User-Id": "u_mallory"},
                       json={"meeting_id": ROW, "terms": [unknown("Acme")]}).status_code == 403


def test_a_publish_without_terms_is_refused_rather_than_guessed(client):
    r = client.post("/api/meeting/terms", headers={"X-User-Id": JANE},
                    json={"meeting_id": ROW, "cursor": "c9"})
    assert r.status_code == 422


def test_the_map_is_a_file_beside_the_meeting_and_not_the_transcript(client, tmp_path):
    client.post("/api/meeting/terms", headers={"X-User-Id": JANE},
                json={"meeting_id": ROW, "cursor": "c9", "terms": [known("Kaar Tech")]})
    written = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    # The desk gained the map and nothing else — no transcript, no page, nothing a person opens.
    # Both files are under the machinery dot-dir (`.vexa/workspace.json` is the desk's own identity).
    assert written == [f"{JANE}/{meeting_terms.TERMS_DIR}/{ROW}.json", f"{JANE}/.vexa/workspace.json"]
    doc = json.loads((tmp_path / JANE / meeting_terms.TERMS_DIR / f"{ROW}.json").read_text())
    assert set(doc) == {"meeting", "cursor", "terms"}
