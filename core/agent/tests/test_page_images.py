"""L2: AN IMAGE ADDRESS AN AGENT DID NOT CHECK NEVER REACHES THE PAGE (Vexa-ai/vexa#1624).

Founder, 2026-09-06, on the OeNB workspace README: the page carried `![OeNB logo](https://
upload.wikimedia.org/wikipedia/commons/8/8c/%C3%96NB_Logo.svg)`, an address the agent invented.
Pressing *Fetch into the workspace* answered 404 — nobody had ever requested it.

The claims, in the order they matter:

* **a dead address is refused and a live image is accepted** — 2xx AND an image content type, on
  the resource and not on our luck: a 200 that is an HTML error page is a broken picture too;
* **a page written with a dead image address comes out without it, and keeps its sentence** — this
  is the whole product decision. The prose an agent wrote around a picture is usually right; the
  address is what it made up, so that is what goes;
* **the friction is filed, naming the guessed URL** — by the door, because the agent cannot report
  what it never noticed. That is the same argument `worker/friction.py` §3 makes for the harness
  filing what the model failed to;
* **the outbound guard is the fetch's, not a second one** — checking an address is still fetching
  an address, and every redirect hop is re-guarded;
* **it costs nothing when there is nothing to check** — a page with no external image reference
  never opens a socket, which is almost every write.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from control_plane import publish as publish_mod
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared import page_images
from shared.config import load_settings

from tests.test_api import _FakeIdentity, _FakeRuntime

DEAD = "https://upload.wikimedia.org/wikipedia/commons/8/8c/%C3%96NB_Logo.svg"
LIVE = "https://www.oenb.at/logo.svg"


def _app(tmp_path):
    return TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(tmp_path)),
    ))


def _public(_host):
    return ["93.184.216.34"]


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def _verify(url: str) -> str | None:
    """The verdict every route test runs on: the founder's address is dead, everything else lives."""
    return f"{url} answered 404" if url == DEAD else None


# ── what the check actually asks the host ───────────────────────────────────────────────────────

def test_a_404_is_refused_and_the_reason_names_the_status():
    def handler(_req):
        return httpx.Response(404, text="Not found")
    reason = page_images.image_refusal(DEAD, client=_client(handler), resolve=_public)
    assert reason and "404" in reason and DEAD in reason


def test_a_200_image_is_accepted():
    def handler(req):
        assert req.method == "HEAD"          # the cheapest question, and the only one needed
        return httpx.Response(200, headers={"content-type": "image/svg+xml"})
    assert page_images.image_refusal(LIVE, client=_client(handler), resolve=_public) is None


def test_a_200_that_is_not_an_image_is_refused_with_the_type_it_did_send():
    def handler(_req):
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"})
    reason = page_images.image_refusal(LIVE, client=_client(handler), resolve=_public)
    assert reason and "text/html" in reason and "not an image" in reason


def test_a_host_that_refuses_the_method_is_asked_again_properly():
    """405/501/403 is an answer about HEAD, not about the file — the ranged GET settles it."""
    seen = []

    def handler(req):
        seen.append(req.method)
        if req.method == "HEAD":
            return httpx.Response(405)
        assert req.headers.get("range") == "bytes=0-0"   # one byte, not the file
        return httpx.Response(206, headers={"content-type": "image/png"})
    assert page_images.image_refusal(LIVE, client=_client(handler), resolve=_public) is None
    assert seen == ["HEAD", "GET"]


def test_a_redirect_into_this_deployments_own_network_is_refused_on_the_hop():
    def handler(req):
        if req.url.host == "www.oenb.at":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/x.png"})
        return httpx.Response(200, headers={"content-type": "image/png"})
    reason = page_images.image_refusal(LIVE, client=_client(handler), resolve=_public)
    assert reason and "169.254.169.254" in reason


def test_the_guard_is_the_fetchs_own_and_never_opens_a_socket_for_a_refused_url():
    def handler(_req):                                     # pragma: no cover - must not be reached
        raise AssertionError("a refused address must never be requested")
    reason = page_images.image_refusal("http://redis/logo.png", client=_client(handler),
                                       resolve=_public)
    assert reason and "redis" in reason


# ── finding them in a page ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,url", [
    (f"![OeNB logo]({DEAD})", DEAD),
    (f'![OeNB logo]({DEAD} "the logo")', DEAD),
    (f'<img src="{DEAD}" alt="OeNB logo">', DEAD),
    (f"<img alt='OeNB logo' src='{DEAD}' />", DEAD),
])
def test_every_shape_an_address_arrives_in_is_found(text, url):
    refs = page_images.external_image_refs(text)
    assert [r.url for r in refs] == [url]
    assert refs[0].alt == "OeNB logo"


def test_a_workspace_path_and_a_data_url_are_not_external_references():
    text = "![logo](assets/oenb-logo.svg) ![dot](data:image/png;base64,AAA) [link](https://oenb.at)"
    assert page_images.external_image_refs(text) == []


def test_a_page_with_nothing_external_is_returned_untouched_and_unasked():
    def never(_url):                                       # pragma: no cover - must not be reached
        raise AssertionError("nothing to check")
    body = "# Note\n\n![logo](assets/l.svg)\n\nPlain prose.\n"
    assert page_images.screen_text(body, verify=never) == (body, [])


def test_the_sentence_stays_and_the_image_goes():
    body = ("# OeNB\n\nThe bank's logo is below.\n\n"
            f"![OeNB logo]({DEAD})\n\nIts head office is in Vienna.\n")
    out, dropped = page_images.screen_text(body, verify=_verify)
    assert DEAD not in out
    assert "The bank's logo is below." in out and "Its head office is in Vienna." in out
    assert [d.url for d in dropped] == [DEAD] and dropped[0].alt == "OeNB logo"
    assert "\n\n\n" not in out          # the hole is closed, nothing else is reflowed


def test_a_live_address_beside_a_dead_one_survives():
    body = f"![live]({LIVE})\n\n![dead]({DEAD})\n"
    out, dropped = page_images.screen_text(body, verify=_verify)
    assert LIVE in out and DEAD not in out
    assert [d.url for d in dropped] == [DEAD]


def test_one_address_named_in_two_fields_is_asked_about_once():
    asked = []

    def counting(url):
        asked.append(url)
        return _verify(url)
    vals, dropped = page_images.screen_values(
        [f"summary with ![logo]({DEAD})", f"a fact with ![logo]({DEAD})"], verify=counting)
    assert asked == [DEAD]
    assert all(DEAD not in v for v in vals) and len(dropped) == 2


# ── the two page-writing doors ──────────────────────────────────────────────────────────────────

def test_a_page_written_with_a_dead_image_address_comes_out_without_it(tmp_path, monkeypatch):
    monkeypatch.setattr(page_images, "image_refusal", _verify)
    body = f"# OeNB\n\nThe logo:\n\n![OeNB logo]({DEAD})\n\nFounded in 1816.\n"
    r = _app(tmp_path).put("/api/workspace/file", params={"subject": "u_jane"},
                           json={"path": "kg/oenb.md", "content": body})
    assert r.status_code == 200
    written = (tmp_path / "u_jane" / "kg" / "oenb.md").read_text()
    assert DEAD not in written
    assert "Founded in 1816." in written and "The logo:" in written


def test_an_entity_card_gets_the_same_door(tmp_path, monkeypatch):
    monkeypatch.setattr(page_images, "image_refusal", _verify)
    r = _app(tmp_path).post("/api/workspace/entity", params={"subject": "u_jane"}, json={
        "kind": "company", "name": "OeNB", "source": "their site",
        "summary": f"Austria's central bank. ![OeNB logo]({DEAD})",
        "facts": [f"1816 — founded. ![seal]({DEAD})"],
    })
    assert r.status_code == 200
    page = (tmp_path / "u_jane" / r.json()["path"]).read_text()
    assert DEAD not in page
    assert "Austria's central bank." in page and "founded" in page


def test_the_friction_is_filed_and_it_names_the_guessed_url(tmp_path, monkeypatch):
    monkeypatch.setattr(page_images, "image_refusal", _verify)
    filed = []
    monkeypatch.setattr(publish_mod, "post_friction",
                        lambda rec, **kw: (filed.append(rec), (True, {"id": "f1"}))[1])
    _app(tmp_path).put("/api/workspace/file", params={"subject": "u_jane"},
                       json={"path": "kg/oenb.md", "content": f"![OeNB logo]({DEAD})"})
    assert len(filed) == 1
    rec = filed[0]
    assert DEAD in rec["tried"] and "404" in rec["tried"]
    assert rec["reporter"] == "agent" and rec["subject"] == "u_jane"
    assert rec["context"]["path"] == "kg/oenb.md" and rec["context"]["tool"] == "workspace_write"


def test_a_clean_page_files_nothing_and_is_written_verbatim(tmp_path, monkeypatch):
    monkeypatch.setattr(page_images, "image_refusal", _verify)
    monkeypatch.setattr(publish_mod, "post_friction",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("nothing to report")))
    body = f"# OeNB\n\n![OeNB logo]({LIVE})\n"
    r = _app(tmp_path).put("/api/workspace/file", params={"subject": "u_jane"},
                           json={"path": "kg/oenb.md", "content": body})
    assert r.status_code == 200
    assert (tmp_path / "u_jane" / "kg" / "oenb.md").read_text() == body


def test_a_write_is_never_failed_by_a_friction_report_that_could_not_be_filed(tmp_path, monkeypatch):
    """The report is about the page; losing it must not lose the page."""
    monkeypatch.setattr(page_images, "image_refusal", _verify)
    monkeypatch.setattr(publish_mod, "post_friction",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("flows is down")))
    r = _app(tmp_path).put("/api/workspace/file", params={"subject": "u_jane"},
                           json={"path": "kg/oenb.md", "content": f"prose ![logo]({DEAD}) prose"})
    assert r.status_code == 200
    assert DEAD not in (tmp_path / "u_jane" / "kg" / "oenb.md").read_text()
