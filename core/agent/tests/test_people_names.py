"""V4 (founder 2026-10-18): People showed raw handles — "2280905", "evgeniagrani", "marvin.hanke" —
because the directory fell back to the subject or the address local part. A person is named by the
claimed CRM Person (Resolution's profile door, per viewer); without one, the roster name only when it
is a name, else the address read as words. Never a raw id or handle."""
from __future__ import annotations

from control_plane import people_directory as pd
from control_plane import person_resolution

from tests.test_person_resolution import ALICE, JOE, _app, _h  # noqa: F401


ROWS = [
    {"subject": "2280905", "name": "2280905", "email": "", "workspaces": ["acme"]},
    {"subject": "u-evg", "name": "evgeniagrani", "email": "evgeniagrani@gmail.com", "workspaces": ["acme"]},
    {"subject": "u-mh", "name": "", "email": "marvin.hanke@example.com", "workspaces": ["acme"]},
    {"subject": "u-ok", "name": "Ana Lima", "email": "ana@example.com", "workspaces": ["acme"]},
]


def test_without_a_person_record_no_handle_is_shown_as_a_name():
    out = pd.with_person_names(ROWS, None)
    assert [r["name"] for r in out] == ["", "Evgeniagrani", "Marvin Hanke", "Ana Lima"]


def test_the_claimed_person_name_wins_through_resolution():
    calls = []

    def post(path, body):
        calls.append(path)
        return {"profiles": {"2280905": {"display_name": "Evgenia Grani"}, "u-mh": {"display_name": "Marvin Hanke"},
                             "u-evg": {"display_name": "Evgenia Grani"}}}

    out = pd.with_person_names(ROWS, person_resolution.Resolver(base="http://crm", post=post))
    assert calls == ["/internal/profiles"]
    assert [r["name"] for r in out] == ["Evgenia Grani", "Evgenia Grani", "Marvin Hanke", "Ana Lima"]


def test_a_resolution_outage_still_never_shows_a_handle():
    def post(path, body):
        raise RuntimeError("crm down")

    out = pd.with_person_names(ROWS, person_resolution.Resolver(base="http://crm", post=post))
    assert [r["name"] for r in out] == ["", "Evgeniagrani", "Marvin Hanke", "Ana Lima"]


def test_the_people_list_names_people_by_their_person_record(tmp_path, monkeypatch):
    def post(path, body):
        if path == "/internal/profiles":
            return {"profiles": {a["subject"]: {"display_name": "Joe Person"} for a in body["accounts"]}}
        return {"persons": {}}

    client, *_ = _app(tmp_path, monkeypatch, post)
    people = client.get("/api/people/peers", headers=_h(ALICE)).json()["people"]
    joe = next(p for p in people if p["subject"] == JOE)
    assert joe["name"] == "Joe Person"
