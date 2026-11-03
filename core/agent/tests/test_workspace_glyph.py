"""A workspace's STANDARD ICON (founder 2026-10-18: "let's pick icons from a large selection of
standard icons at creation unless changed").

README front matter `glyph:` / `glyph_by:` on the workspace; picked at creation by a deterministic
keyword matcher (the client's `platform/workspaceGlyphs`, case for case), a small model when nothing
matched, else a neutral icon; served by the image route when there is no image of the workspace's own.
"""
from __future__ import annotations

import pytest

from control_plane import workspace_glyph as wg
from control_plane import workspace_icon as wi

from tests.test_workspace_icon import JANE, BOB, SHARED, MARK, PNG, _git, _h, _media_cache, _world  # noqa: F401 — the autouse media fixture

# The same cases as clients/terminal/src/platform/__tests__/workspaceGlyphs.test.ts.
CASES = [
    ("Product planning", "", "map"),
    ("Company handbook", "", "book-open"),
    ("Garden club", "", "sprout"),
    ("Growth", "", "chart-line"),
    ("OeNB pilot", "", "landmark"),
    ("Northbank pilot", "Pilot with the central bank", "landmark"),
    ("Hiring", "", "user-plus"),
    ("Book club", "", "book-marked"),
    ("Weekly sync", "", "calendar-clock"),
    ("Gardening", "", "sprout"),
    ("Café crew", "", "coffee"),
    ("Untitled", "", None),
]


@pytest.mark.parametrize("name,purpose,want", CASES)
def test_the_matcher_picks_what_the_name_and_purpose_say(name, purpose, want):
    assert wg.match(name, purpose) == want


def test_the_list_is_large_and_every_icon_draws():
    assert len(wg.names()) > 200
    for n in wg.names():
        assert b"data-vexa-glyph" in wg.svg(n)
    assert set(wg._doc()["generic"]) <= set(wg.names())


def test_stems_and_the_neutral_pick_are_stable():
    assert wg.stem("Gardening") == "garden" and wg.stem("libraries") == "library" and wg.stem("boxes") == "box"
    assert wg.generic("kestrel-5pe") == wg.generic("kestrel-5pe") and wg.generic("x") in wg.names()


def test_nothing_matched_asks_the_small_model_and_takes_only_a_listed_name():
    asked = []

    def complete(system, user):
        asked.append(user)
        return {"icon": "telescope"}
    assert wg.choose("Kestrel", "", "kestrel-5pe", complete=complete) == ("telescope", "model")
    assert "Kestrel" in asked[0] and "sprout" in asked[0]          # the list goes with the question
    assert wg.choose("Kestrel", "", "k", complete=lambda s, u: {"icon": "not-an-icon"})[1] == "auto"
    assert wg.choose("Kestrel", "", "k", complete=lambda s, u: (_ for _ in ()).throw(RuntimeError()))[1] == "auto"
    # a keyword hit never spends a model call
    assert wg.choose("Garden club", "", "g", complete=lambda s, u: pytest.fail("asked")) == ("sprout", "auto")


def test_the_readme_names_the_workspace_and_says_what_it_is_for():
    assert wg.name_and_purpose("# Garden club\n\nAllotment rota\n\n{{status}}\n", {}, "x") == ("Garden club", "Allotment rota")
    assert wg.name_and_purpose("hi\n", {}, "product-planning")[0] == "product planning"
    assert wg.name_and_purpose("hi\n", {}, "growth-4mf")[0] == "growth"


def test_a_workspace_made_before_this_shows_the_icon_its_readme_matches(tmp_path):
    c = _world(tmp_path)
    (tmp_path / SHARED / "README.md").write_text("# Garden club\n\nSeed swaps\n")
    r = c.get(f"/api/workspaces/{SHARED}/identity/icon", headers=_h(BOB))
    assert r.status_code == 200 and b'data-vexa-glyph="sprout"' in r.content
    st = c.get(f"/api/workspaces/{SHARED}/identity/state", headers=_h(BOB)).json()
    assert st["source"] == "glyph" and st["glyph"] == "sprout" and st["glyph_by"] == "derived"


def test_the_company_logo_still_wins_over_an_automatic_icon(tmp_path):
    c = _world(tmp_path, media=lambda url: (PNG, "image/png", url))
    r = c.get(f"/api/workspaces/{SHARED}/identity/icon", headers=_h(BOB))
    assert r.content == PNG


def test_a_person_picks_an_icon_it_is_committed_and_shows_for_every_member(tmp_path):
    c = _world(tmp_path, media=lambda url: (PNG, "image/png", url))
    c.put(f"/api/workspaces/{SHARED}/identity/icon", headers=_h(JANE), json={"svg": MARK})
    r = c.put(f"/api/workspaces/{SHARED}/identity/icon", headers=_h(JANE), json={"glyph": "Rocket"})
    assert r.status_code == 200 and r.json() == {"set": True, "glyph": "rocket", "glyph_by": "person"}
    got = c.get(f"/api/workspaces/{SHARED}/identity/icon", headers=_h(BOB))
    assert b'data-vexa-glyph="rocket"' in got.content            # over the company logo, and the upload is gone
    assert not (tmp_path / SHARED / ".vexa/icon.svg").exists()
    readme = (tmp_path / SHARED / "README.md").read_text()
    assert "glyph: rocket" in readme and "glyph_by: person" in readme and "domain: studio.example.com" in readme
    assert "README.md" in _git(tmp_path / SHARED, "log", "--name-only", "-1", "--format=")
    st = c.get(f"/api/workspaces/{SHARED}/identity/state", headers=_h(BOB)).json()
    assert st["source"] == "glyph" and st["glyph"] == "rocket" and st["glyph_by"] == "person"


def test_auto_picks_from_the_readme_and_an_unknown_name_is_refused(tmp_path):
    c = _world(tmp_path)
    (tmp_path / JANE / "README.md").write_text("# Company handbook\n\nHow we work\n")
    r = c.put("/api/workspaces/personal/identity/icon", headers=_h(JANE), json={"glyph": "auto"})
    assert r.status_code == 200 and r.json()["glyph"] == "book-open" and r.json()["glyph_by"] == "auto"
    assert c.put("/api/workspaces/personal/identity/icon", headers=_h(JANE),
                 json={"glyph": "../../etc/passwd"}).status_code == 400


def test_a_reader_cannot_pick_and_an_agent_in_a_shared_workspace_does_not_either(tmp_path):
    c = _world(tmp_path)
    assert c.put(f"/api/workspaces/{SHARED}/identity/icon", headers=_h("u_rita"), json={"glyph": "map"}).status_code == 403
    assert c.put(f"/api/workspaces/{SHARED}/identity/icon", headers=_h(BOB, agent=True),
                 json={"glyph": "map"}).status_code == 403
    assert c.put("/api/workspaces/personal/identity/icon", headers=_h(JANE, agent=True),
                 json={"glyph": "map"}).status_code == 200


def test_assign_at_creation_stores_and_commits_it(tmp_path):
    from tests.test_workspace_icon import _ws
    ws = _ws(tmp_path, "garden-club-9xa")
    assert wi.assign_glyph(ws, "Garden club", "garden-club-9xa") == "sprout"
    assert wi.glyph(ws) == ("sprout", "auto")
    assert "README.md" in _git(ws, "log", "--name-only", "-1", "--format=")
    assert wi.assign_glyph(ws, "Kestrel", "kestrel-5pe") in wg._doc()["generic"]


def test_a_new_workspace_gets_its_icon_at_birth(tmp_path):
    c = _world(tmp_path)
    r = c.post("/api/workspace/new", headers=_h(JANE), json={"name": "Garden club"})
    assert r.status_code == 201, r.text
    slug = r.json()["slug"]
    got = c.get(f"/api/workspaces/{slug}/identity/state", headers=_h(JANE)).json()
    assert got["glyph"] == "sprout" and got["glyph_by"] == "auto"
