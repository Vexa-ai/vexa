"""Pinned pages are the PERSON's (#1783; founder: "pinned pages are in every session")."""
from collections import defaultdict

from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane.api_shared import _Sessions, pinned_pages_preamble
from control_plane.routers.chats import build


def _client(sess):
    deps = defaultdict(lambda: None)
    deps.update(sess=sess, subject_of=lambda req: req.headers["x-user-id"])
    app = FastAPI()
    app.include_router(build(**{k: deps[k] for k in [
        '_global_root', '_meeting_note_recorder', '_meeting_owner_lookup', '_read_target',
        '_resolve_room', '_scaffold_recipient_is', '_scaffold_view', '_schedule_source', 'dispatcher',
        'invocations_url', 'mindex', 'redis_url', 'scaffolds', 'scheduler', 'sess', 'settings',
        'stream_reader', 'subject_of', 'workspace_registry', 'wsr']}))
    return TestClient(app)


def test_one_list_per_person_in_order_and_isolated():
    c = _client(_Sessions())
    pages = [{"slug": None, "path": "inbox/unanswered.md", "label": "Unanswered"},
             {"slug": "biz", "path": "graph/sg/Growth.md", "label": "Growth"}]
    assert c.put("/api/user/pinned-pages", headers={"X-User-Id": "a"}, json={"pages": pages}).status_code == 200
    assert c.get("/api/user/pinned-pages", headers={"X-User-Id": "a"}).json()["pages"] == pages
    assert c.get("/api/user/pinned-pages", headers={"X-User-Id": "b"}).json()["pages"] == []


def test_a_path_outside_the_workspace_is_refused():
    c = _client(_Sessions())
    for bad in ("../secret.md", "/etc/passwd", "a//b.md"):
        r = c.put("/api/user/pinned-pages", headers={"X-User-Id": "a"}, json={"pages": [{"path": bad}]})
        assert r.status_code == 422, bad


def test_the_turn_names_pinned_pages_by_title_and_address_never_content():
    text = pinned_pages_preamble([{"slug": None, "path": "inbox/unanswered.md", "label": "Unanswered"},
                                  {"slug": "biz", "path": "graph/sg/Growth.md", "label": "Growth"}])
    assert "Unanswered — `inbox/unanswered.md`" in text
    assert "Growth — `biz:graph/sg/Growth.md`" in text
    assert pinned_pages_preamble([]) == ""
