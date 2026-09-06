"""L2: IMAGES ON PAGES — the asset door (Vexa-ai/vexa#1612).

Founder, 2026-09-06, on a customer workspace README the agent had written: *"we want to be able
images"*. The page rendered `![OeNB logo](…)` as alt text and a broken-image icon. The rule that
answers it is about WHERE THE BYTES LIVE, so that is what these tests are about:

* **a page's picture comes through the page's own door** — the same owner- and membership-scoped
  read `GET /api/workspace/file` is, answering BYTES with the media type the extension names. A
  file another subject can read as text and not as an image, or as an image and not as text, would
  be two doors with one name;
* **the media type is the extension's, never the bytes'** — it is what the browser executes the
  file AS, so a `.png` full of `<svg onload=…>` is served as a png;
* **the traversal guard is the file route's**, because an asset route that could be talked out of
  the workspace root is a filesystem read with a friendly name;
* **a fetch stores the source** — an asset nobody can trace is a fact with no citation, which is
  the one thing `kg/` prose is never allowed to be;
* **the outbound guard refuses this deployment's own network**, on the first URL and on every
  redirect hop;
* **an attached image lands where the agent's fetched one does**, so a reference written by a
  person and a reference written by the agent are the same reference.

And one test with no user-visible claim at all: the SSRF rule is written twice in this domain (once
for the worker image, once for the control-plane image, which ships no `llm/`), and a comment
asserting the copies agree would be worth nothing.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared import asset_source as assets
from shared.config import load_settings

from tests.test_api import _FakeIdentity, _FakeRuntime

# a one-pixel PNG — real bytes, so "served verbatim" is a claim about bytes and not about a string
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100"
    "05fe02fea7d4b3f00000000049454e44ae426082"
)


def _app(tmp_path):
    return TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(tmp_path)),
    ))


def _write(tmp_path, subject: str, rel: str, content: bytes) -> None:
    f = tmp_path / subject / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(content)


def _public(_host):
    return ["93.184.216.34"]


# ── reading one ─────────────────────────────────────────────────────────────────────────────────

def test_a_relative_image_is_served_as_bytes_with_the_media_type_its_name_claims(tmp_path):
    _write(tmp_path, "u_jane", "assets/oenb-logo.png", PNG)
    r = _app(tmp_path).get("/api/workspace/asset",
                           params={"subject": "u_jane", "path": "assets/oenb-logo.png"})
    assert r.status_code == 200
    assert r.content == PNG                       # verbatim: not re-encoded, not JSON-wrapped
    assert r.headers["content-type"].startswith("image/png")
    assert r.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("name,expected", [
    ("assets/a.svg", "image/svg+xml"),
    ("assets/a.jpeg", "image/jpeg"),
    ("assets/a.webp", "image/webp"),
    ("assets/chart.pdf", "application/pdf"),
    ("assets/mystery.qqq", "application/octet-stream"),
])
def test_the_media_type_comes_from_the_extension_never_from_the_bytes(tmp_path, name, expected):
    # every one of these holds the SAME bytes, and they are script-shaped on purpose
    _write(tmp_path, "u_jane", name, b"<svg onload=\"alert(1)\"></svg>")
    r = _app(tmp_path).get("/api/workspace/asset", params={"subject": "u_jane", "path": name})
    assert r.status_code == 200 and r.headers["content-type"].split(";")[0] == expected
    # an svg served same-origin is a picture, not a script host
    assert "default-src 'none'" in r.headers["content-security-policy"]


def test_the_asset_route_is_scoped_exactly_like_the_file_route(tmp_path):
    """The subject is the IDENTITY HEADER, never a query field — so this is the one test here that
    sets `X-User-Id` by hand. `?subject=` is decorative (the L2 harness pins a fallback subject in
    conftest), and a scoping claim asserted through a decorative parameter would pass on any code
    at all, including code with no scoping in it."""
    _write(tmp_path, "u_jane", "assets/private.png", PNG)
    c = _app(tmp_path)
    mine = c.get("/api/workspace/asset", params={"path": "assets/private.png"},
                 headers={"X-User-Id": "u_jane"})
    assert mine.status_code == 200 and mine.content == PNG
    other = c.get("/api/workspace/asset", params={"path": "assets/private.png"},
                  headers={"X-User-Id": "u_bob"})
    assert other.status_code in (403, 404)
    assert PNG not in other.content
    # and the same file through the same door as TEXT answers the same way, for the same caller
    assert c.get("/api/workspace/file", params={"path": "assets/private.png"},
                 headers={"X-User-Id": "u_bob"}).status_code in (403, 404)


def test_traversal_and_absence_answer_the_same_way_the_file_route_does(tmp_path):
    _write(tmp_path, "u_jane", "assets/a.png", PNG)
    c = _app(tmp_path)
    assert c.get("/api/workspace/asset",
                 params={"subject": "u_jane", "path": "../../etc/passwd"}).status_code == 400
    assert c.get("/api/workspace/asset",
                 params={"subject": "u_jane", "path": "assets/nope.png"}).status_code == 404


def test_an_unchanged_asset_revalidates_instead_of_downloading_again(tmp_path):
    _write(tmp_path, "u_jane", "assets/a.png", PNG)
    c = _app(tmp_path)
    first = c.get("/api/workspace/asset", params={"subject": "u_jane", "path": "assets/a.png"})
    etag = first.headers["etag"]
    again = c.get("/api/workspace/asset", params={"subject": "u_jane", "path": "assets/a.png"},
                  headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""


# ── fetching one in ─────────────────────────────────────────────────────────────────────────────

def test_a_fetched_asset_is_stored_under_assets_with_its_source_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "fetch_asset",
                        lambda url, **_kw: (PNG, "image/png", "https://oenb.at/logo.png"))
    r = _app(tmp_path).post("/api/workspace/asset",
                            params={"subject": "u_jane"},
                            json={"url": "https://oenb.at/logo.png"})
    assert r.status_code == 200
    stored = r.json()["path"]
    assert stored == "assets/logo.png"
    assert (tmp_path / "u_jane" / stored).read_bytes() == PNG
    index = (tmp_path / "u_jane" / assets.SOURCES_INDEX).read_text()
    assert "assets/logo.png" in index and "https://oenb.at/logo.png" in index


def test_a_fetch_names_the_path_it_was_asked_for_and_puts_a_bare_name_in_assets(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "fetch_asset",
                        lambda url, **_kw: (PNG, "image/png", "https://x.example/pic"))
    r = _app(tmp_path).post("/api/workspace/asset", params={"subject": "u_jane"},
                            json={"url": "https://x.example/pic", "path": "oenb-logo.png"})
    assert r.json()["path"] == "assets/oenb-logo.png"


def test_a_refused_url_is_a_named_400_not_a_silent_empty_asset(tmp_path):
    r = _app(tmp_path).post("/api/workspace/asset", params={"subject": "u_jane"},
                            json={"url": "http://169.254.169.254/latest/meta-data/"})
    assert r.status_code == 400
    assert "169.254.169.254" in r.json()["detail"]
    assert not (tmp_path / "u_jane" / "assets").exists()


def test_an_uploaded_image_lands_where_a_fetched_one_does(tmp_path):
    r = _app(tmp_path).put("/api/workspace/asset", params={"subject": "u_jane"},
                           files={"file": ("Board photo.PNG", PNG, "image/png")})
    assert r.status_code == 200
    assert r.json()["path"] == "assets/Board_photo.PNG"
    assert (tmp_path / "u_jane" / "assets" / "Board_photo.PNG").read_bytes() == PNG
    assert "uploaded here" in (tmp_path / "u_jane" / assets.SOURCES_INDEX).read_text()


def test_an_attached_image_goes_to_assets_and_an_attached_document_keeps_its_drawer(tmp_path):
    r = _app(tmp_path).post("/api/workspace/upload", data={"subject": "u_jane"}, files=[
        ("files", ("chart.png", PNG, "image/png")),
        ("files", ("notes.txt", b"plain", "text/plain")),
    ])
    assert r.status_code == 200
    paths = [f["path"] for f in r.json()["files"]]
    assert paths[0].startswith("assets/") and paths[0].endswith("-chart.png")
    assert paths[1].startswith("uploads/")


# ── the fetch itself, offline ───────────────────────────────────────────────────────────────────

def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_fetch_asset_returns_the_bytes_and_the_type_the_server_sent():
    def handler(_req):
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
    content, ctype, final = assets.fetch_asset("https://example.com/logo.png",
                                               client=_client(handler), resolve=_public)
    assert content == PNG and ctype == "image/png" and final.endswith("/logo.png")


def test_every_redirect_hop_is_re_checked_against_the_guard():
    def handler(req):
        if req.url.host == "example.com":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/x"})
        return httpx.Response(200, content=PNG)
    with pytest.raises(assets.AssetFetchError) as e:
        assets.fetch_asset("https://example.com/logo.png", client=_client(handler), resolve=_public)
    assert "169.254.169.254" in str(e.value)


def test_an_enormous_remote_costs_the_call_and_not_the_workspace():
    big = b"x" * (assets.MAX_ASSET_BYTES + 1)

    def handler(_req):
        return httpx.Response(200, content=big, headers={"content-type": "image/png"})
    with pytest.raises(assets.AssetFetchError) as e:
        assets.fetch_asset("https://example.com/big.png", client=_client(handler), resolve=_public)
    assert "larger than" in str(e.value)


@pytest.mark.parametrize("url", [
    "http://localhost:8100/x.png", "http://redis/x.png", "http://127.0.0.1/x.png",
    "http://10.0.0.5/x.png", "ftp://example.com/x.png", "",
])
def test_the_deployments_own_network_is_never_an_asset_source(url):
    assert assets.fetch_refusal(url, resolve=_public) is not None


def test_a_public_https_url_is_allowed():
    assert assets.fetch_refusal("https://example.com/logo.png", resolve=_public) is None


def test_the_two_outbound_guards_agree(monkeypatch):
    """The SSRF rule is stated twice in this domain — `shared/asset_source.py` for the control-plane
    image and `llm/web_tools.py` for the worker image, which ship different trees (see either
    module's docstring). Neither can import the other, so this is the only thing that can hold them
    together: same verdict, same table of URLs."""
    from llm import web_tools
    monkeypatch.setattr(web_tools, "_resolve", _public)
    for url in ["https://example.com/a.png", "http://127.0.0.1/a", "http://redis/a",
                "http://169.254.169.254/", "http://10.1.2.3/a", "ftp://example.com/a",
                "https://sub.example.co.uk/a.svg", "", "not a url"]:
        assert bool(assets.fetch_refusal(url, resolve=_public)) == bool(web_tools.fetch_refusal(url)), url


# ── the source index ────────────────────────────────────────────────────────────────────────────

def test_a_re_fetch_replaces_the_row_rather_than_adding_a_second_answer():
    one = assets.record_source("", "assets/logo.png", "https://a.example/1.png")
    two = assets.record_source(one, "assets/logo.png", "https://b.example/2.png")
    assert two.count("`assets/logo.png`") == 1
    assert "b.example" in two and "a.example" not in two
    three = assets.record_source(two, "assets/other.png", "https://c.example/3.png")
    assert three.count("`assets/logo.png`") == 1 and three.count("`assets/other.png`") == 1


def test_the_index_survives_a_page_somebody_hand_edited():
    hand = "# Asset sources\n\nDmitry's note: the logo came off their press kit.\n"
    out = assets.record_source(hand, "assets/logo.png", "https://a.example/1.png")
    assert "Dmitry's note" in out and "`assets/logo.png`" in out
