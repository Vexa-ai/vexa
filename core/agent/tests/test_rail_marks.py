"""Pin and hide on the rail — per person, on the server, only for rows the person can see.

`GET /api/chat/marks` and `POST /api/chat/marks` in `control_plane/routers/chats.py`, stored by
`_Sessions.mark_row` under `agent:rail-marks:{subject}`. Both backings run every time: the in-memory
one is what a developer's laptop uses and the Redis hash is what production uses."""
from collections import defaultdict

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane.api_shared import _Sessions
from control_plane.routers.chats import build

# meeting 42 belongs to `a` and is shared with `b`; `c` can read nothing
_READERS = {"42": {"a", "b"}}


def _lookup(subject, meeting_id, workspaces=None):
    return {"id": int(meeting_id)} if subject in _READERS.get(str(meeting_id), set()) else None


def _backings():
    return [_Sessions(), _Sessions(fakeredis.FakeRedis(decode_responses=True))]


def _client(sess):
    deps = defaultdict(lambda: None)
    deps.update(sess=sess, subject_of=lambda req: req.headers["x-user-id"], _meeting_owner_lookup=_lookup)
    app = FastAPI()
    app.include_router(build(**{k: deps[k] for k in [
        '_global_root', '_meeting_note_recorder', '_meeting_owner_lookup', '_read_target',
        '_resolve_room', '_scaffold_recipient_is', '_scaffold_view', '_schedule_source', 'dispatcher',
        'invocations_url', 'mindex', 'redis_url', 'scaffolds', 'scheduler', 'sess', 'settings',
        'stream_reader', 'subject_of', 'workspace_registry', 'wsr']}))
    return TestClient(app)


@pytest.fixture(params=["memory", "redis"])
def stack(request):
    sess = _backings()[0 if request.param == "memory" else 1]
    sess.upsert("a", "pchat-1", title="Review email inbox", touched=True)
    sess.upsert("a", "pchat-2", title="Calendar", touched=True)
    return sess, _client(sess)


def _as(who):
    return {"X-User-Id": who}


def test_a_person_pins_hides_and_undoes_their_own_chat(stack):
    _, client = stack
    r = client.post("/api/chat/marks", headers=_as("a"), json={"row": "c:pchat-1", "pinned": True})
    assert r.status_code == 200, r.text
    assert r.json() == {"pinned": ["c:pchat-1"], "hidden": []}
    r = client.post("/api/chat/marks", headers=_as("a"), json={"row": "c:pchat-2", "hidden": True})
    assert r.json() == {"pinned": ["c:pchat-1"], "hidden": ["c:pchat-2"]}
    # read back, as a reload or a second device would
    assert client.get("/api/chat/marks", headers=_as("a")).json() == {"pinned": ["c:pchat-1"], "hidden": ["c:pchat-2"]}
    client.post("/api/chat/marks", headers=_as("a"), json={"row": "c:pchat-1", "pinned": False})
    client.post("/api/chat/marks", headers=_as("a"), json={"row": "c:pchat-2", "hidden": False})
    assert client.get("/api/chat/marks", headers=_as("a")).json() == {"pinned": [], "hidden": []}


def test_pin_and_hide_are_independent_flags_on_one_row(stack):
    _, client = stack
    client.post("/api/chat/marks", headers=_as("a"), json={"row": "c:pchat-1", "pinned": True})
    client.post("/api/chat/marks", headers=_as("a"), json={"row": "c:pchat-1", "hidden": True})
    assert client.get("/api/chat/marks", headers=_as("a")).json() == {"pinned": ["c:pchat-1"], "hidden": ["c:pchat-1"]}
    client.post("/api/chat/marks", headers=_as("a"), json={"row": "c:pchat-1", "hidden": False})
    assert client.get("/api/chat/marks", headers=_as("a")).json() == {"pinned": ["c:pchat-1"], "hidden": []}


def test_another_persons_chat_cannot_be_pinned_or_hidden(stack):
    _, client = stack
    for flag in ("pinned", "hidden"):
        r = client.post("/api/chat/marks", headers=_as("b"), json={"row": "c:pchat-1", flag: True})
        assert r.status_code == 404, r.text
    assert client.get("/api/chat/marks", headers=_as("b")).json() == {"pinned": [], "hidden": []}
    assert client.get("/api/chat/marks", headers=_as("a")).json() == {"pinned": [], "hidden": []}


def test_a_meeting_is_markable_only_by_someone_who_can_read_it(stack):
    _, client = stack
    assert client.post("/api/chat/marks", headers=_as("c"), json={"row": "m:42", "pinned": True}).status_code == 404
    assert client.post("/api/chat/marks", headers=_as("c"), json={"row": "c:meet-42", "hidden": True}).status_code == 404
    assert client.post("/api/chat/marks", headers=_as("a"), json={"row": "m:7", "pinned": True}).status_code == 404
    assert client.post("/api/chat/marks", headers=_as("a"), json={"row": "m:42", "pinned": True}).status_code == 200


def test_a_shared_meeting_is_marked_for_the_viewer_and_never_for_the_owner(stack):
    _, client = stack
    assert client.post("/api/chat/marks", headers=_as("b"), json={"row": "m:42", "hidden": True}).status_code == 200
    # the meeting's own chat, before its first turn reached b's index
    assert client.post("/api/chat/marks", headers=_as("b"), json={"row": "c:meet-42", "pinned": True}).status_code == 200
    assert client.get("/api/chat/marks", headers=_as("b")).json() == {"pinned": ["c:meet-42"], "hidden": ["m:42"]}
    assert client.get("/api/chat/marks", headers=_as("a")).json() == {"pinned": [], "hidden": []}


def test_clearing_a_mark_needs_no_visible_row(stack):
    """A row can go (a meeting deleted, a share withdrawn) while its mark stays. Clearing it only
    touches the caller's own mark, so it is allowed; setting one is not."""
    sess, client = stack
    sess.mark_row("c", "m:42", pinned=True)          # left over from a share that has ended
    r = client.post("/api/chat/marks", headers=_as("c"), json={"row": "m:42", "pinned": False})
    assert r.status_code == 200 and r.json() == {"pinned": [], "hidden": []}


def test_a_bad_mark_is_refused(stack):
    _, client = stack
    h = _as("a")
    assert client.post("/api/chat/marks", headers=h, json={"row": "c:pchat-1"}).status_code == 422
    for bad in ("x:pchat-1", "c:../etc", "c:", "c:a/b"):
        assert client.post("/api/chat/marks", headers=h, json={"row": bad, "pinned": True}).status_code == 422
    assert client.post("/api/chat/marks", headers=h,
                       json={"row": "c:pchat-1", "pinned": True, "subject": "b"}).status_code == 422


def test_a_bare_session_id_is_the_same_row_as_its_chat_key(stack):
    _, client = stack
    client.post("/api/chat/marks", headers=_as("a"), json={"row": "pchat-1", "pinned": True})
    assert client.get("/api/chat/marks", headers=_as("a")).json()["pinned"] == ["c:pchat-1"]


def test_the_marks_are_bounded(stack, monkeypatch):
    sess, client = stack
    monkeypatch.setattr(_Sessions, "RAIL_MARKS_CAP", 2)
    assert client.post("/api/chat/marks", headers=_as("a"), json={"row": "c:pchat-1", "pinned": True, "hidden": True}).status_code == 200
    # re-setting what is already set adds nothing and is not refused
    assert client.post("/api/chat/marks", headers=_as("a"), json={"row": "c:pchat-1", "pinned": True}).status_code == 200
    r = client.post("/api/chat/marks", headers=_as("a"), json={"row": "c:pchat-2", "pinned": True})
    assert r.status_code == 422 and r.json()["detail"] == "Too many marked rows"


def test_a_failing_meeting_lookup_denies():
    sess = _Sessions()
    deps = defaultdict(lambda: None)

    def broken(*a, **k):
        raise RuntimeError("meeting-api down")

    deps.update(sess=sess, subject_of=lambda req: req.headers["x-user-id"], _meeting_owner_lookup=broken)
    app = FastAPI()
    app.include_router(build(**{k: deps[k] for k in [
        '_global_root', '_meeting_note_recorder', '_meeting_owner_lookup', '_read_target',
        '_resolve_room', '_scaffold_recipient_is', '_scaffold_view', '_schedule_source', 'dispatcher',
        'invocations_url', 'mindex', 'redis_url', 'scaffolds', 'scheduler', 'sess', 'settings',
        'stream_reader', 'subject_of', 'workspace_registry', 'wsr']}))
    client = TestClient(app)
    assert client.post("/api/chat/marks", headers=_as("a"), json={"row": "m:42", "pinned": True}).status_code == 404


def test_the_mark_body_is_a_named_model(stack):
    _, client = stack
    spec = client.get("/openapi.json").json()
    ref = spec["paths"]["/api/chat/marks"]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert set(spec["components"]["schemas"][ref.rsplit("/", 1)[-1]]["properties"]) == {"row", "pinned", "hidden"}
