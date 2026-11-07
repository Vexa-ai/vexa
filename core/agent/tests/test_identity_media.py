"""A person's photo, served to the people who know them (Vexa-ai/vexa#1783, UI decision 15g).

    GET /api/people/{subject}/photo

Identity keeps the photo; agent-api decides who may see it — the person themself, and anyone who
shares a workspace or a chat (a call is held in a chat) with them — and asks identity for the bytes
only after it said yes. Everyone else gets the same 404 as a person with no photo, so the route does
not tell a stranger whether an account exists.

And never a face from the web for anybody: a contact in someone's CRM, a meeting attendee who is not
a user here, has initials and their company's logo — no Gravatar, no lookup of any kind.

L2: the people + identity-media routers over a sqlite session store; identity is a fake transport.
"""
from __future__ import annotations

import hashlib
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane import home_media, home_sources, identity_media
from control_plane.routers import identity_media as media_router
from control_plane.routers import people as people_router
from control_plane.session_store import SessionStore
from tests._session_fakes import MemBlobs

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
ME, COLLEAGUE, CHATMATE, STRANGER = "11", "12", "13", "14"


class Identity:
    """The internal tier, faked: who has a photo, and every request it was asked."""

    def __init__(self):
        self.photos = {COLLEAGUE: PNG, CHATMATE: PNG, ME: PNG, STRANGER: PNG}
        self.asked: list = []
        self.down = False

    def __call__(self, path):
        self.asked.append(path)
        if self.down:
            return 0, b"", {}
        uid = path.split("/")[3]
        body = self.photos.get(uid)
        if body is None:
            return 404, b"", {}
        etag = '"%s"' % hashlib.sha256(body).hexdigest()[:32]
        return 200, body, {"content-type": "image/png", "etag": etag, "x-photo-source": "upload"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    ident = Identity()
    monkeypatch.setattr(identity_media, "_transport", ident)
    identity_media.reset()
    colleagues = {ME: [{"subject": COLLEAGUE, "name": "Bo Stone", "email": "bo@corp.example", "workspaces": ["sales"]}],
                  COLLEAGUE: [{"subject": ME, "name": "Me", "email": "me@corp.example", "workspaces": ["sales"]}]}
    deps = dict(subject_of=lambda r: r.headers["x-user-id"], session_store=store,
                people_colleagues=lambda uid: colleagues.get(uid, []), people_pages=lambda uid: [],
                people_self_email=lambda uid: "me@corp.example", _schedule_source=lambda uid: [],
                home_queue_source=lambda uid: [], home_clock=time.time)
    app = FastAPI()
    app.include_router(people_router.build(**deps))
    app.include_router(media_router.build(**deps))
    yield {"c": TestClient(app), "store": store, "ident": ident}
    home_sources.SOURCES.pop("people", None)
    store.close()


def _get(env, subject, viewer=ME):
    return env["c"].get(f"/api/people/{subject}/photo", headers={"x-user-id": viewer})


def test_a_stranger_gets_404_and_identity_is_never_asked(env):
    r = _get(env, STRANGER)
    assert r.status_code == 404
    assert env["ident"].asked == [], "the bytes are fetched only after the viewer was allowed"


def test_a_workspace_co_member_gets_the_photo_privately(env):
    r = _get(env, COLLEAGUE)
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"].startswith("private")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in r.headers["content-security-policy"]


def test_a_chat_co_participant_gets_the_photo(env):
    s = env["store"].create(ME, title="a chat")
    env["store"].add_participant(s["id"], ME, CHATMATE)
    assert _get(env, CHATMATE).status_code == 200
    assert _get(env, ME, viewer=CHATMATE).status_code == 200


def test_the_person_sees_their_own_photo(env):
    assert _get(env, ME).status_code == 200


def test_a_person_with_no_photo_is_a_404_like_a_stranger(env):
    env["ident"].photos.pop(COLLEAGUE)
    assert _get(env, COLLEAGUE).status_code == 404


def test_identity_unreachable_is_a_404_never_a_guess(env):
    env["ident"].down = True
    assert _get(env, COLLEAGUE).status_code == 404


@pytest.mark.parametrize("subject", ["..", "a/b", "x" * 200, "%2e%2e"])
def test_a_malformed_subject_is_refused_before_anything(env, subject):
    assert _get(env, subject).status_code in (404, 422)
    assert env["ident"].asked == []


# ── never a face from the web ────────────────────────────────────────────────────────────────────
def test_a_crm_person_who_is_not_a_user_has_initials_and_a_logo_never_a_face(env):
    """The People strip: an external attendee comes back with initials and their company's logo
    key, and NO avatar — so nothing will ever fetch a face for them."""
    import control_plane.people as people_mod
    d = people_mod.Directory(ME, "me@corp.example", [])
    p = d.attendee({"name": "Erin Vale", "email": "erin@acme.example"})
    row = d.row(p, time.time())
    assert "avatar" not in row and "photo" not in row
    assert row["initials"] == "EV" and row.get("logo") == "d:acme.example"


def test_a_colleague_row_points_at_the_photo_route_not_at_gravatar():
    row = home_sources.person_of(COLLEAGUE, "Bo Stone", {"email": "bo@corp.example"})
    assert row["photo"] == f"/api/people/{COLLEAGUE}/photo"
    assert "avatar" not in row


def test_the_media_proxy_no_longer_fetches_a_gravatar_for_anyone(tmp_path):
    """`g:` keys were Gravatar lookups by address for anyone the home named — a face looked up on the
    web for people who never chose one here. They are refused, and nothing is fetched."""
    asked = []

    def fetch(url):
        asked.append(url)
        return PNG, "image/png", url

    key = "g:" + "0" * 32
    assert not home_media.valid_key(key)
    assert home_media.get(key, fetch=fetch, root=tmp_path) is None
    assert home_media.avatar_key("someone@acme.example") is None
    assert asked == []


def test_the_home_keeps_only_the_photo_route_of_the_account_a_person_names():
    from control_plane import home
    ok = home._person({"name": "Bo Stone", "account_id": COLLEAGUE, "photo": f"/api/people/{COLLEAGUE}/photo"})
    assert ok["photo"] == f"/api/people/{COLLEAGUE}/photo"
    for bad in ("https://tracker.example/face.png", f"/api/people/{STRANGER}/photo", "/api/home/media/g:0"):
        assert "photo" not in home._person({"name": "Bo Stone", "account_id": COLLEAGUE, "photo": bad})


def test_a_new_version_skips_the_cached_photo(env):
    """The person who just changed their photo asks with the new version and sees it at once."""
    assert _get(env, ME).status_code == 200
    env["ident"].photos[ME] = PNG + b"new"
    assert env["c"].get(f"/api/people/{ME}/photo", headers={"x-user-id": ME}).content == PNG   # cached
    calls = len(env["ident"].asked)
    new = hashlib.sha256(PNG + b"new").hexdigest()[:12]
    r = env["c"].get(f"/api/people/{ME}/photo?v={new}", headers={"x-user-id": ME})
    assert len(env["ident"].asked) == calls + 1 and r.content == PNG + b"new"


# ── how long a face can outlive the reason to see it (review nit) ────────────────────────────────
def test_a_lost_shared_chat_hides_the_photo_on_the_very_next_request(env):
    s = env["store"].create(ME, title="a chat")
    env["store"].add_participant(s["id"], ME, CHATMATE)
    assert _get(env, CHATMATE).status_code == 200          # the bytes are cached now
    env["store"]._x("DELETE FROM agent_session_participant WHERE session_id = ? AND principal = ?", (s["id"], CHATMATE))
    assert _get(env, CHATMATE).status_code == 404          # visibility is never cached


def test_a_face_is_cached_at_most_a_minute_end_to_end(env):
    r = _get(env, COLLEAGUE)
    max_age = int(r.headers["cache-control"].split("max-age=")[1].split(",")[0])
    assert r.headers["cache-control"].startswith("private") and max_age + identity_media.TTL_S <= 60
