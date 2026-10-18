"""A workspace's image (UI decision 15f, Vexa-ai/vexa#1783).

The image travels with the workspace: `.vexa/icon.svg|png|webp`, named by README front matter
`icon:`, at most ~200 KB, sanitised. Default when there is none: the bound company's logo through the
media proxy; else 404, and the client draws the monogram. An agent's change in a shared workspace is
a proposal the owner accepts.
"""
from __future__ import annotations

import base64
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import workspace_icon as wi
from control_plane import workspace_membership as m
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from control_plane.identity_token import DELEGATION_HEADERS
from shared.config import load_settings

from tests.test_api import _FakeIdentity, _FakeRuntime

JANE, BOB = "u_jane", "u_bob"
SHARED = "studio-c1"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
MARK = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<rect width="24" height="24" rx="6" fill="#2f6f5e"/><path d="M6 17 12 6l6 11z" fill="#fff"/></svg>')
EVIL = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        'viewBox="0 0 24 24" onload="alert(1)">'
        '<script>fetch("/api/auth/me")</script>'
        '<foreignObject width="10" height="10"><iframe xmlns="http://www.w3.org/1999/xhtml" '
        'src="https://evil.example/"></iframe></foreignObject>'
        '<image xlink:href="https://evil.example/beacon.png" width="4" height="4"/>'
        '<use href="https://evil.example/sprite.svg#a"/>'
        '<rect width="4" height="4" style="fill:url(https://evil.example/x)" onclick="steal()"/>'
        '<a href="javascript:alert(1)"><circle r="3" fill="url(#g)"/></a>'
        '<image href="data:text/html;base64,PHNjcmlwdD4=" width="1" height="1"/>'
        '<image href="data:image/png;base64,iVBORw0KGgo=" width="1" height="1"/>'
        '</svg>')


# ── the pure half ──────────────────────────────────────────────────────────────────────────────

def test_a_malicious_svg_is_scrubbed_of_everything_that_can_run_or_reach_out():
    out = wi.sanitize_svg(EVIL.encode()).decode()
    low = out.lower()
    assert "<script" not in low and "foreignobject" not in low and "iframe" not in low
    assert "onload" not in low and "onclick" not in low and "javascript:" not in low
    assert "evil.example" not in low                      # no external href / xlink:href / url()
    assert "data:text/html" not in low                     # data: beyond images refused
    assert "data:image/png;base64" in low                  # an embedded raster stays
    assert 'fill="url(#g)"' in out                         # a local reference stays
    assert out.lstrip().startswith("<svg")


def test_a_clean_mark_survives_sanitising_intact():
    out = wi.sanitize_svg(MARK.encode()).decode()
    assert "<rect" in out and "<path" in out and 'viewBox="0 0 24 24"' in out


@pytest.mark.parametrize("bad", [
    b"not xml at all <",
    b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x "y">]><svg xmlns="http://www.w3.org/2000/svg">&x;</svg>',
    b'<html xmlns="http://www.w3.org/1999/xhtml"><body/></html>',
])
def test_what_is_not_an_svg_is_refused(bad):
    with pytest.raises(wi.IconRefused):
        wi.sanitize_svg(bad)


def test_oversize_is_refused():
    with pytest.raises(wi.IconRefused) as e:
        wi.checked(PNG + b"\x00" * (wi.MAX_BYTES + 1), "png")
    assert "200" in str(e.value)


def test_bytes_must_be_what_the_extension_says():
    with pytest.raises(wi.IconRefused):
        wi.checked(b"<svg/>", "png")
    with pytest.raises(wi.IconRefused):
        wi.checked(PNG, "gif")
    assert wi.checked(PNG, "png") == (PNG, "png")
    assert wi.checked(WEBP, "webp") == (WEBP, "webp")


def test_a_jpeg_or_icon_is_kept_inside_an_svg_rather_than_refused():
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 40
    body, ext = wi.from_fetched(jpeg, "image/jpeg")
    assert ext == "svg" and b"data:image/jpeg;base64," in body


def test_default_is_the_bound_company_logo_and_is_deterministic(tmp_path):
    ws = _ws(tmp_path, "acme", readme="---\ntitle: Acme\ndomain: acme-widgets.example.com\n---\n# Acme\n")
    assert wi.company_logo_key(ws) == "d:acme-widgets.example.com"
    assert wi.company_logo_key(ws) == wi.company_logo_key(ws)
    plain = _ws(tmp_path, "notes")
    assert wi.company_logo_key(plain) is None


def test_set_writes_the_file_and_names_it_in_the_readme(tmp_path):
    ws = _ws(tmp_path, "acme", readme="---\ntitle: Acme\n---\n# Acme\n\nBody.\n")
    wi.write_icon(ws, MARK.encode(), "svg")
    assert (ws / ".vexa/icon.svg").is_file()
    readme = (ws / "README.md").read_text()
    assert "icon: .vexa/icon.svg" in readme and "title: Acme" in readme and "Body." in readme
    got = wi.read_icon(ws)
    assert got and got[1] == "image/svg+xml"
    wi.write_icon(ws, PNG, "png")                         # replacing removes the old file
    assert not (ws / ".vexa/icon.svg").exists() and (ws / ".vexa/icon.png").is_file()
    wi.reset_icon(ws)
    assert wi.read_icon(ws) is None and "icon:" not in (ws / "README.md").read_text()


def test_a_readme_icon_pointing_outside_vexa_is_ignored(tmp_path):
    ws = _ws(tmp_path, "x", readme="---\nicon: ../../etc/passwd\n---\n")
    assert wi.read_icon(ws) is None


def test_site_logo_discovery_prefers_touch_icon_then_page_links(tmp_path):
    html = (b'<html><head><link rel="icon" href="/static/fav.png">'
            b'<meta property="og:image" content="https://cdn.example.org/og.png"></head></html>')
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith("/apple-touch-icon.png"):
            raise RuntimeError("404")
        if url == "https://site.example.org/":
            return html, "text/html", url
        if url.endswith("/static/fav.png"):
            return PNG, "image/png", url
        raise RuntimeError("404")
    got = wi.find_site_logo("site.example.org", fetch=fetch)
    assert got == (PNG, "png", "https://site.example.org/static/fav.png")
    assert calls[0] == "https://site.example.org/apple-touch-icon.png"


# ── the routes ─────────────────────────────────────────────────────────────────────────────────

def _git(work: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(work), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _ws(root: Path, slug: str, readme: str = "hi\n") -> Path:
    ws = root / slug
    ws.mkdir(parents=True, exist_ok=True)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    (ws / "README.md").write_text(readme)
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "seed")
    return ws


@pytest.fixture(autouse=True)
def _media_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_HOME_MEDIA_DIR", str(tmp_path / "_media"))
    from shared import asset_source

    def _net(url, *a, **k):
        if _MEDIA["fetch"] is not None:
            return _MEDIA["fetch"](url)
        raise asset_source.AssetFetchError("offline", kind="unreachable", url=url)
    _MEDIA["fetch"] = None
    monkeypatch.setattr(asset_source, "fetch_asset", _net)


_MEDIA: dict = {"fetch": None}


def _world(root: Path, *, media=None):
    _MEDIA["fetch"] = media
    _ws(root, JANE)
    _ws(root, BOB)
    idx = m.InMemoryMembershipIndex()
    _ws(root, SHARED, readme="---\ntitle: Studio\ndomain: studio.example.com\n---\n# Studio\n")
    m.ensure_owner(root, SHARED, JANE, index=idx)
    m.grant_membership(root, SHARED, BOB, "contributor", added_by=JANE, index=idx)
    m.grant_membership(root, SHARED, "u_rita", "viewer", added_by=JANE, index=idx)
    settings = load_settings(workspaces_dir=str(root),
                             global_system_workspace_path=str(root / "_global"),
                             internal_api_secret="s", ui_url="https://app.example.test", redis_url="")
    app = create_app(Dispatcher(settings, _FakeRuntime(), _FakeIdentity()),
                     reader=WorkspaceReader(str(root)), membership_index=idx)
    return TestClient(app)


def _h(sub: str, *, agent: bool = False, ceiling: str = "*") -> dict:
    h = {"X-User-Id": sub}
    if agent:
        h[DELEGATION_HEADERS["workspaces"]] = ceiling
        h[DELEGATION_HEADERS["regime"]] = "human"
    return h


def test_no_icon_and_no_company_is_a_404_so_the_client_draws_the_monogram(tmp_path):
    c = _world(tmp_path)
    assert c.get("/api/workspaces/personal/identity/icon", headers=_h(JANE)).status_code == 404


def test_default_is_the_company_logo_through_the_media_proxy(tmp_path):
    seen = []

    def media(url):
        seen.append(url)
        return PNG, "image/png", url
    c = _world(tmp_path, media=media)
    r = c.get(f"/api/workspaces/{SHARED}/identity/icon", headers=_h(BOB))
    assert r.status_code == 200 and r.content == PNG
    assert seen and "studio.example.com" in seen[0]
    st = c.get(f"/api/workspaces/{SHARED}/identity/state", headers=_h(BOB)).json()
    assert st["source"] == "company"


def test_a_person_sets_a_mark_and_every_member_sees_it(tmp_path):
    c = _world(tmp_path)
    r = c.put(f"/api/workspaces/{SHARED}/identity/icon", headers=_h(JANE), json={"svg": EVIL})
    assert r.status_code == 200 and r.json()["set"] is True
    got = c.get(f"/api/workspaces/{SHARED}/identity/icon", headers=_h(BOB))
    assert got.status_code == 200 and got.headers["content-type"].startswith("image/svg+xml")
    assert b"<script" not in got.content and b"evil.example" not in got.content
    assert "sandbox" in got.headers["content-security-policy"]
    assert got.headers["x-content-type-options"] == "nosniff"
    # committed: it travels with the workspace
    assert ".vexa/icon.svg" in _git(tmp_path / SHARED, "log", "--name-only", "-1", "--format=")


def test_a_non_member_cannot_read_the_icon(tmp_path):
    c = _world(tmp_path)
    c.put(f"/api/workspaces/{SHARED}/identity/icon", headers=_h(JANE), json={"svg": MARK})
    assert c.get(f"/api/workspaces/{SHARED}/identity/icon", headers=_h("u_stranger")).status_code in (403, 404)


def test_oversize_upload_is_refused_at_the_route(tmp_path):
    c = _world(tmp_path)
    big = base64.b64encode(PNG + b"\x00" * (wi.MAX_BYTES + 10)).decode()
    r = c.put("/api/workspaces/personal/identity/icon", headers=_h(JANE), json={"data_b64": big, "type": "png"})
    assert r.status_code == 413


def test_an_agents_change_in_a_shared_workspace_is_a_proposal_the_owner_accepts(tmp_path):
    c = _world(tmp_path)
    r = c.put(f"/api/workspaces/{SHARED}/identity/icon", headers=_h(BOB, agent=True), json={"svg": MARK})
    assert r.status_code == 202 and r.json()["proposed"] is True
    assert c.get(f"/api/workspaces/{SHARED}/identity/state", headers=_h(JANE)).json()["proposal"]["by"] == BOB
    assert not (tmp_path / SHARED / ".vexa/icon.svg").exists()            # nothing changed yet
    # the proposal is viewable before accepting
    assert c.get(f"/api/workspaces/{SHARED}/identity/icon", params={"proposal": 1}, headers=_h(JANE)).status_code == 200
    # a contributor (not the owner) cannot accept, nor can an agent acting for the owner
    assert c.post(f"/api/workspaces/{SHARED}/identity/proposal", headers=_h(BOB), json={"accept": True}).status_code == 403
    assert c.post(f"/api/workspaces/{SHARED}/identity/proposal", headers=_h(JANE, agent=True),
                  json={"accept": True}).status_code == 403
    ok = c.post(f"/api/workspaces/{SHARED}/identity/proposal", headers=_h(JANE), json={"accept": True})
    assert ok.status_code == 200 and ok.json()["set"] is True
    assert (tmp_path / SHARED / ".vexa/icon.svg").is_file()
    assert c.get(f"/api/workspaces/{SHARED}/identity/state", headers=_h(BOB)).json()["proposal"] is None


def test_an_agent_in_a_private_workspace_sets_it_directly(tmp_path):
    c = _world(tmp_path)
    r = c.put("/api/workspaces/personal/identity/icon", headers=_h(JANE, agent=True), json={"svg": MARK})
    assert r.status_code == 200 and r.json()["set"] is True


def test_reset_and_use_company_logo(tmp_path):
    c = _world(tmp_path, media=lambda url: (PNG, "image/png", url))
    c.put(f"/api/workspaces/{SHARED}/identity/icon", headers=_h(JANE), json={"svg": MARK})
    assert c.get(f"/api/workspaces/{SHARED}/identity/state", headers=_h(JANE)).json()["source"] == "workspace"
    r = c.put(f"/api/workspaces/{SHARED}/identity/icon", headers=_h(JANE), json={"use": "company_logo"})
    assert r.status_code == 200 and (tmp_path / SHARED / ".vexa/icon.png").is_file()
    r = c.put(f"/api/workspaces/{SHARED}/identity/icon", headers=_h(JANE), json={"reset": True})
    assert r.status_code == 200
    assert c.get(f"/api/workspaces/{SHARED}/identity/state", headers=_h(JANE)).json()["source"] == "company"


def test_a_reader_cannot_set_it(tmp_path):
    c = _world(tmp_path)
    idx_reader = "u_rita"
    r = c.put(f"/api/workspaces/{SHARED}/identity/icon", headers=_h(idx_reader), json={"svg": MARK})
    assert r.status_code == 403


def test_external_images_off_stops_every_logo_fetch(tmp_path):
    """`external_images: off` in _global/POLICIES.md stops EVERY outbound image fetch, as the policy
    promises: the company fallback, `url=` and `site=` alike. A designed SVG still works."""
    seen = []

    def media(url):
        seen.append(url)
        return PNG, "image/png", url
    c = _world(tmp_path, media=media)
    (tmp_path / "_global").mkdir(exist_ok=True)
    (tmp_path / "_global" / "POLICIES.md").write_text("---\nexternal_images: off\n---\n# Policies\n")
    for body in ({"url": "https://studio.example.com/logo.png"}, {"site": "studio.example.com"}):
        r = c.put("/api/workspaces/personal/identity/icon", headers=_h(JANE), json=body)
        assert r.status_code == 403 and "external_images" in r.text
    assert c.get(f"/api/workspaces/{SHARED}/identity/icon", headers=_h(BOB)).status_code == 404
    assert seen == []
    assert c.put("/api/workspaces/personal/identity/icon", headers=_h(JANE), json={"svg": MARK}).status_code == 200


# ── a new workspace's identity is PROPOSED and accepted on its card (R3 3.5, mock flow 12) ───────

def test_propose_stores_a_proposal_even_in_a_fresh_workspace_of_one(tmp_path):
    c = _world(tmp_path)
    r = c.put(f"/api/workspaces/{SHARED}/identity/icon", headers=_h(JANE, agent=True),
              json={"svg": MARK, "propose": True})
    assert r.status_code == 202 and r.json()["proposed"] is True
    assert not (tmp_path / SHARED / ".vexa/icon.svg").exists()


def test_a_person_may_propose_too_and_nothing_changes_until_accepted(tmp_path):
    c = _world(tmp_path)
    r = c.put(f"/api/workspaces/{SHARED}/identity/icon", headers=_h(JANE), json={"svg": MARK, "propose": True})
    assert r.status_code == 202
    assert c.get(f"/api/workspaces/{SHARED}/identity/state", headers=_h(JANE)).json()["proposal"]
    assert not (tmp_path / SHARED / ".vexa/icon.svg").exists()


def test_the_owner_of_a_private_workspace_answers_its_proposal(tmp_path):
    c = _world(tmp_path)
    r = c.put("/api/workspaces/personal/identity/icon", headers=_h(JANE, agent=True), json={"svg": MARK, "propose": True})
    assert r.status_code == 202
    st = c.get("/api/workspaces/personal/identity/state", headers=_h(JANE)).json()
    assert st["proposal"] and st["owner"] is True
    # the agent never accepts its own proposal, even in a private workspace
    assert c.post("/api/workspaces/personal/identity/proposal", headers=_h(JANE, agent=True),
                  json={"accept": True}).status_code == 403
    ok = c.post("/api/workspaces/personal/identity/proposal", headers=_h(JANE), json={"accept": True})
    assert ok.status_code == 200 and ok.json()["set"] is True
