"""Settings → Appearance (founder 2026-10-15: "let's allow users to pick" an accent colour, and
"a normal font pick"): one choice per person, over the company default in `_global/POLICIES.md`."""
from collections import defaultdict

from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane.api_shared import _Sessions
from control_plane.routers.chats import build


def _client(sess, gdir=None):
    deps = defaultdict(lambda: None)
    deps.update(sess=sess, subject_of=lambda req: req.headers["x-user-id"], _global_root=(lambda: gdir))
    app = FastAPI()
    app.include_router(build(**{k: deps[k] for k in [
        '_global_root', '_meeting_note_recorder', '_meeting_owner_lookup', '_read_target',
        '_resolve_room', '_scaffold_recipient_is', '_scaffold_view', '_schedule_source', 'dispatcher',
        'invocations_url', 'mindex', 'redis_url', 'scaffolds', 'scheduler', 'sess', 'settings',
        'stream_reader', 'subject_of', 'workspace_registry', 'wsr']}))
    return TestClient(app)


def test_a_person_picks_over_the_company_default_and_null_goes_back(tmp_path):
    g = tmp_path / "_global"
    g.mkdir()
    (g / "POLICIES.md").write_text("---\nappearance_accent: teal\nappearance_font_ui: inter\nappearance_font_reading: source-serif\n---\n")
    c = _client(_Sessions(), g)
    a = {"X-User-Id": "a"}
    got = c.get("/api/user/appearance", headers=a).json()
    assert got["company"] == {"accent": "teal", "font_ui": "inter", "font_reading": "source-serif"}
    assert got["effective"]["accent"] == "teal" and got["user"] == {}
    got = c.put("/api/user/appearance", headers=a, json={"accent": "#3366CC", "density": "compact"}).json()
    assert got["user"] == {"accent": "#3366cc", "density": "compact"}
    assert got["effective"] == {"accent": "#3366cc", "font_ui": "inter", "font_reading": "source-serif", "density": "compact"}
    assert c.get("/api/user/appearance", headers={"X-User-Id": "b"}).json()["user"] == {}     # one per person
    got = c.put("/api/user/appearance", headers=a, json={"accent": None}).json()
    assert got["effective"]["accent"] == "teal" and "accent" not in got["user"]


def test_only_the_vocabulary_is_stored():
    c = _client(_Sessions())
    a = {"X-User-Id": "a"}
    for bad in ({"accent": "red; background:url(x)"}, {"font_ui": "Comic Sans"}, {"density": "tiny"}, {"accent": "#12345"}):
        assert c.put("/api/user/appearance", headers=a, json=bad).status_code == 422, bad
    assert c.put("/api/user/appearance", headers=a, json={"colour": "blue"}).status_code == 422
    assert c.get("/api/user/appearance", headers=a).json() == {"user": {}, "company": {}, "effective": {}}
