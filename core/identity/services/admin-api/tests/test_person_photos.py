"""A person's photo (Vexa-ai/vexa#1783, UI decision 15g): what identity keeps, which one wins, that
no metadata survives, and when — the only time — it looks anything up.

Offline: an in-memory store and a fetch that records every URL it is asked for. No Postgres, no
network, no S3. The doors and the DB/object-store adapter are in `test_person_photo_doors.py`.
"""
from __future__ import annotations

import asyncio
import io
import json

import pytest
from PIL import Image

from admin_api.app import person_photos as pp
from photo_samples import GIF, SVG, has_metadata, jpeg, png, webp


class MemStore:
    def __init__(self):
        self.meta: dict = {}
        self.bytes: dict = {}

    async def rows(self, user_id):
        return {k: dict(v) for k, v in self.meta.get(user_id, {}).items()}

    async def put(self, user_id, source, meta, body):
        self.meta.setdefault(user_id, {})[source] = dict(meta)
        if body is None:
            self.bytes.pop((user_id, source), None)
        else:
            self.bytes[(user_id, source)] = bytes(body)

    async def drop(self, user_id, source):
        self.meta.get(user_id, {}).pop(source, None)
        self.bytes.pop((user_id, source), None)

    async def read(self, user_id, source, meta):
        return self.bytes.get((user_id, source))


class Net:
    """Every outbound URL (and the headers sent), recorded; answers from a table (missing = None)."""

    def __init__(self, answers=None):
        self.asked: list = []
        self.headers: list = []
        self.answers = dict(answers or {})

    async def __call__(self, url, headers=None):
        self.asked.append(url)
        self.headers.append(dict(headers or {}))
        for prefix, body in sorted(self.answers.items(), key=lambda kv: -len(kv[0])):
            if url.startswith(prefix):
                return body(headers) if callable(body) else body
        return None


def run(coro):
    return asyncio.run(coro)


GOOGLE = "https://lh3.googleusercontent.com/a/photo-of-the-person=s96-c"
GRAV = "https://gravatar.com/avatar/"
OID = "0f0f0f0f-1111-2222-3333-444444444444"
BOUND = {"microsoft": f"microsoft:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee:{OID}"}


# ── what an image is ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("body,ctype", [(png(), "image/png"), (jpeg(), "image/jpeg"), (webp(), "image/webp")])
def test_png_jpeg_and_webp_are_photos(body, ctype):
    assert pp.sniff(body) == ctype
    assert pp.clean(body)[1] == ctype


@pytest.mark.parametrize("body", [SVG, GIF, b"", b"hello", png()[:20], b"\x89PNG\r\n\x1a\n" + b"\x00" * 40])
def test_anything_else_is_refused(body):
    with pytest.raises(pp.Refused):
        pp.clean(body)


def test_a_photo_over_the_size_cap_is_refused():
    with pytest.raises(pp.Refused):
        pp.sniff(png() + b"\x00" * pp.MAX_BYTES)


# ── no metadata survives (the review's blocker) ──────────────────────────────────────────────────
def test_a_jpeg_with_gps_exif_comes_back_without_any_exif_xmp_or_comment():
    dirty = jpeg(gps=True)
    assert "exif" in has_metadata(dirty) and "PhoneMaker" in has_metadata(dirty)
    data, ctype = pp.clean(dirty)
    assert ctype == "image/jpeg"
    assert has_metadata(data) == []


def test_png_text_chunks_exif_and_xmp_are_gone():
    dirty = png(text=True)
    assert "png-text" in has_metadata(dirty)
    data, _ = pp.clean(dirty)
    assert has_metadata(data) == []


def test_webp_exif_and_xmp_are_gone():
    dirty = webp(meta=True)
    assert "EXIF" in has_metadata(dirty) and "XMP " in has_metadata(dirty)
    data, ctype = pp.clean(dirty)
    assert ctype == "image/webp" and has_metadata(data) == []


def test_the_pixels_survive_cleaning():
    data, _ = pp.clean(png(8, 8))
    with Image.open(io.BytesIO(data)) as im:
        assert im.size == (8, 8) and im.convert("RGB").getpixel((0, 0)) == (10, 200, 30)


@pytest.mark.parametrize("source", ["upload", "provider", "gravatar"])
def test_every_stored_image_is_cleaned_whatever_its_source(source):
    s = MemStore()
    net = Net({GOOGLE: jpeg(gps=True), GRAV: jpeg(gps=True)})
    if source == "upload":
        run(pp.upload(s, 1, jpeg(gps=True)))
    elif source == "provider":
        run(pp.at_signin(s, 1, email="a@example.com", provider="google", url=GOOGLE, fetch=net))
    else:
        run(pp.at_signin(s, 1, email="a@example.com", fetch=net))
    stored = s.bytes[(1, source)]
    assert has_metadata(stored) == []


# ── precedence: upload → provider → gravatar → none ──────────────────────────────────────────────
def test_precedence_is_upload_then_provider_then_gravatar_then_none():
    s = MemStore()
    assert pp.pick(run(s.rows(1))) is None
    run(s.put(1, "gravatar", {"content_type": "image/png", "sha256": "g"}, png()))
    assert pp.pick(run(s.rows(1)))[0] == "gravatar"
    run(s.put(1, "provider", {"content_type": "image/jpeg", "sha256": "p", "provider": "google"}, jpeg()))
    assert pp.pick(run(s.rows(1)))[0] == "provider"
    run(pp.upload(s, 1, webp()))
    assert pp.pick(run(s.rows(1)))[0] == "upload"
    assert run(pp.remove_upload(s, 1))["photo"]["source"] == "provider"


def test_a_remembered_gravatar_miss_is_not_a_photo():
    s = MemStore()
    run(s.put(1, "gravatar", {"content_type": "", "sha256": ""}, None))
    assert pp.pick(run(s.rows(1))) is None
    assert pp.describe(run(s.rows(1))) == {"photo": None, "sources": []}


def test_an_svg_upload_is_refused_and_nothing_is_stored():
    s = MemStore()
    with pytest.raises(pp.Refused):
        run(pp.upload(s, 1, SVG))
    assert run(s.rows(1)) == {} and s.bytes == {}


# ── the provider photo: fetched BY IDENTITY at sign-in, re-checked at sign-in ────────────────────
def test_a_google_picture_is_fetched_once_at_signin_and_stored():
    s, net = MemStore(), Net({GOOGLE: jpeg()})
    got = run(pp.at_signin(s, 1, email="a@example.com", provider="google", url=GOOGLE, fetch=net, now=1000))
    assert got["provider"] == "stored" and net.asked == [GOOGLE]
    again = run(pp.at_signin(s, 1, email="a@example.com", provider="google", url=GOOGLE, fetch=net, now=2000))
    assert again["provider"] == "same" and net.asked == [GOOGLE]


def test_a_google_url_off_googles_picture_host_is_never_fetched():
    s, net = MemStore(), Net()
    run(pp.at_signin(s, 1, email="a@example.com", provider="google", url="https://tracker.example/x.jpg",
                     fetch=net, gravatar=False))
    assert net.asked == []


def test_a_changed_picture_is_fetched_again_at_the_next_signin():
    s, net = MemStore(), Net({GOOGLE: jpeg(), GOOGLE + "&new": png()})
    run(pp.at_signin(s, 1, email="a@example.com", provider="google", url=GOOGLE, fetch=net, now=1000))
    got = run(pp.at_signin(s, 1, email="a@example.com", provider="google", url=GOOGLE + "&new", fetch=net, now=2000))
    assert got["provider"] == "stored" and run(s.rows(1))["provider"]["content_type"] == "image/png"


def test_an_old_provider_photo_is_rechecked_after_the_interval():
    s, net = MemStore(), Net({GOOGLE: jpeg()})
    run(pp.at_signin(s, 1, email="a@example.com", provider="google", url=GOOGLE, fetch=net, now=1000))
    run(pp.at_signin(s, 1, email="a@example.com", provider="google", url=GOOGLE, fetch=net,
                     now=1000 + pp.PROVIDER_RECHECK_S + 1))
    assert net.asked == [GOOGLE, GOOGLE]


def test_a_provider_that_no_longer_has_a_picture_takes_its_photo_away():
    s, net = MemStore(), Net({GOOGLE: jpeg()})
    run(pp.at_signin(s, 1, email="a@example.com", provider="google", url=GOOGLE, fetch=net, gravatar=False, now=1000))
    got = run(pp.at_signin(s, 1, email="a@example.com", provider="google", fetch=net, gravatar=False, now=2000))
    assert got["provider"] == "removed" and "provider" not in run(s.rows(1))


def test_another_providers_signin_without_a_picture_keeps_the_photo():
    s, net = MemStore(), Net({GOOGLE: jpeg()})
    run(pp.at_signin(s, 1, email="a@example.com", provider="google", url=GOOGLE, fetch=net, now=1000))
    assert run(pp.at_signin(s, 1, email="a@example.com", provider="oidc", fetch=net, now=2000))["provider"] == "kept"


def _graph(oid):
    return {pp.GRAPH_ME: json.dumps({"id": oid}).encode(), pp.GRAPH_PHOTO: jpeg(gps=True)}


def test_microsofts_photo_is_fetched_by_identity_with_the_signin_token_for_the_bound_account():
    s, net = MemStore(), Net(_graph(OID))
    got = run(pp.at_signin(s, 1, email="a@example.com", provider="microsoft", access_token="tok",
                           bound_subjects=BOUND, fetch=net))
    assert got["provider"] == "stored"
    assert net.asked == [pp.GRAPH_ME, pp.GRAPH_PHOTO]
    assert all(h.get("Authorization") == "Bearer tok" for h in net.headers)
    assert has_metadata(s.bytes[(1, "provider")]) == []


def test_a_token_of_another_microsoft_account_sets_nothing():
    """Somebody else's token (a caller holding the internal secret and its own Microsoft token)
    cannot put its picture on this account: Graph says the token is not the bound identity."""
    s, net = MemStore(), Net(_graph("99999999-9999-9999-9999-999999999999"))
    got = run(pp.at_signin(s, 1, email="a@example.com", provider="microsoft", access_token="tok",
                           bound_subjects=BOUND, fetch=net, gravatar=False))
    assert got["provider"] == "refused" and run(s.rows(1)) == {}
    assert pp.GRAPH_PHOTO not in net.asked


def test_an_account_with_no_microsoft_binding_asks_graph_nothing():
    s, net = MemStore(), Net(_graph(OID))
    run(pp.at_signin(s, 1, email="a@example.com", provider="microsoft", access_token="tok",
                     bound_subjects={}, fetch=net, gravatar=False))
    assert net.asked == []


def test_a_provider_picture_that_is_not_an_image_is_refused():
    s, net = MemStore(), Net({GOOGLE: SVG})
    got = run(pp.at_signin(s, 1, email="a@example.com", provider="google", url=GOOGLE, fetch=net, gravatar=False))
    assert got["provider"] == "refused" and run(s.rows(1)) == {}


def test_a_plain_http_picture_is_never_fetched():
    s, net = MemStore(), Net()
    run(pp.at_signin(s, 1, email="a@example.com", provider="oidc", url="http://example.com/p.jpg",
                     fetch=net, gravatar=False))
    assert net.asked == []


def test_an_upload_survives_any_signin():
    s, net = MemStore(), Net({GOOGLE: jpeg()})
    run(pp.upload(s, 1, png()))
    run(pp.at_signin(s, 1, email="a@example.com", provider="google", url=GOOGLE, fetch=net))
    assert pp.pick(run(s.rows(1)))[0] == "upload"


# ── Gravatar: only if one exists, only for the account's own address, cached ─────────────────────
def test_gravatar_is_asked_with_d404_for_the_accounts_own_address_and_stored():
    s, net = MemStore(), Net({GRAV: png()})
    got = run(pp.at_signin(s, 1, email=" A@Example.com ", fetch=net, now=1000))
    assert got["gravatar"] == "stored"
    assert net.asked == [pp.gravatar_url("a@example.com")] and "d=404" in net.asked[0]


def test_a_gravatar_miss_is_remembered_and_not_asked_again_soon():
    s, net = MemStore(), Net()
    assert run(pp.at_signin(s, 1, email="a@example.com", fetch=net, now=1000))["gravatar"] == "miss"
    assert run(pp.at_signin(s, 1, email="a@example.com", fetch=net, now=2000))["gravatar"] == "miss"
    assert len(net.asked) == 1
    run(pp.at_signin(s, 1, email="a@example.com", fetch=net, now=1000 + pp.GRAVATAR_RECHECK_S + 1))
    assert len(net.asked) == 2


def test_gravatar_is_not_asked_when_the_person_has_a_better_photo():
    s, net = MemStore(), Net()
    run(pp.upload(s, 1, png()))
    run(pp.at_signin(s, 1, email="a@example.com", fetch=net))
    assert net.asked == []


def test_gravatar_switched_off_asks_nobody():
    s, net = MemStore(), Net()
    run(pp.at_signin(s, 1, email="a@example.com", fetch=net, gravatar=False))
    assert net.asked == []


def test_reading_photos_never_fetches_anything():
    s = MemStore()
    for uid in (1, 2, 999):
        assert pp.pick(run(s.rows(uid))) is None
