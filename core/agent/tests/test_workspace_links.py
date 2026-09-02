"""The link grammar, the rewrite on write, and the three access states a reader can get.

PRD decision 26.2 + 26.3. The claim under test, in one sentence: **a link between workspaces
survives a rename, and a link into a workspace you do not have is not an error.**
"""
from __future__ import annotations

from pathlib import Path

import pytest

from control_plane import link_resolver, workspace_ids as ids
from shared import links
from shared.entities import upsert_entity
from shared.workspace_id import write_workspace_json


# ── the grammar ──────────────────────────────────────────────────────────────────────────────────

def test_the_in_workspace_form_is_unchanged():
    r = links.parse_ref("Olga Avramenko")
    assert (r.workspace, r.target, r.form) == (None, "Olga Avramenko", "title")


def test_the_cross_workspace_entity_form():
    r = links.parse_ref("ws:k4m5x2q7bd/olga-avramenko")
    assert (r.workspace, r.target, r.form) == ("k4m5x2q7bd", "olga-avramenko", "entity")


def test_the_cross_workspace_path_form():
    r = links.parse_ref("ws:k4m5x2q7bd/kg/notes/2026-03-02.md")
    assert (r.workspace, r.target, r.form) == ("k4m5x2q7bd", "kg/notes/2026-03-02.md", "path")
    assert links.parse_ref("ws:k4m5x2q7bd/README.md").form == "path"


def test_a_malformed_ws_ref_is_not_downgraded_to_a_title_search():
    """`[[ws:oops/x]]` must not quietly look up an entity called "ws:oops/x" — that renders a
    not-found chip and hides the typo."""
    r = links.parse_ref("ws:oops/x")
    assert r.workspace is None and r.form == "path" and r.target == "ws:oops/x"


def test_format_carries_no_display_name():
    """Decision 26.3 resolves the title at READ time; a name baked into the link is the stale copy
    the id exists to remove."""
    assert links.format_ref("k4m5x2q7bd", "olga-avramenko") == "[[ws:k4m5x2q7bd/olga-avramenko]]"


def test_the_canonical_url_round_trips_and_ignores_mail_client_noise():
    u = links.canonical_url("k4m5x2q7bd", "kg/entities/person/olga-avramenko.md")
    assert u == "/w/k4m5x2q7bd/kg/entities/person/olga-avramenko.md"
    assert links.parse_canonical_url(u) == ("k4m5x2q7bd", "kg/entities/person/olga-avramenko.md")
    assert links.parse_canonical_url(u + "?utm=mail#top") == links.parse_canonical_url(u)
    assert links.parse_canonical_url("/w/k4m5x2q7bd") == ("k4m5x2q7bd", "")
    assert links.parse_canonical_url("/workspaces/x/README.md") is None
    assert links.parse_canonical_url("/w/NOTANID/x") is None


def test_refs_in_reads_a_document():
    text = "Spoke to [[Olga Avramenko]] about [[ws:k4m5x2q7bd/dna-tsc]] and `kg/x.md`."
    assert [r.target for r in links.refs_in(text)] == ["Olga Avramenko", "dna-tsc"]
    assert [r.workspace for r in links.cross_workspace_refs(text)] == ["k4m5x2q7bd"]


# ── the rewrite ──────────────────────────────────────────────────────────────────────────────────

def test_rewrite_only_touches_names_that_live_elsewhere():
    text = "[[Olga Avramenko]] and [[Cottalango Leon]] and [[Nobody Here]]"
    out, rewrites = links.rewrite_cross_workspace(
        text, here={"olga-avramenko"}, elsewhere={"cottalango-leon": "k4m5x2q7bd"})
    assert out == "[[Olga Avramenko]] and [[ws:k4m5x2q7bd/cottalango-leon]] and [[Nobody Here]]"
    assert rewrites == [("Cottalango Leon", "[[ws:k4m5x2q7bd/cottalango-leon]]")]


def test_the_home_workspace_always_wins():
    """A name with a page HERE is the page the reader meant, even when a group also holds one."""
    out, rewrites = links.rewrite_cross_workspace(
        "[[Olga Avramenko]]", here={"olga-avramenko"}, elsewhere={"olga-avramenko": "k4m5x2q7bd"})
    assert out == "[[Olga Avramenko]]" and rewrites == []


def test_the_rewrite_is_idempotent():
    once, _ = links.rewrite_cross_workspace("[[Cottalango Leon]]", here=set(),
                                            elsewhere={"cottalango-leon": "k4m5x2q7bd"})
    twice, again = links.rewrite_cross_workspace(once, here=set(),
                                                 elsewhere={"cottalango-leon": "k4m5x2q7bd"})
    assert twice == once and again == []


# ── entity_upsert does it on the way to disk ─────────────────────────────────────────────────────

def _workspace(root: Path, name: str, wid: str) -> Path:
    d = root / name
    (d / "kg" / "entities").mkdir(parents=True, exist_ok=True)
    write_workspace_json(d, id=wid, kind="group", created="2026-03-02")
    return d


def test_entity_upsert_rewrites_a_cross_workspace_link(tmp_path):
    desk = _workspace(tmp_path, "desk", "aaaaaaaaaa")
    group = _workspace(tmp_path, "group", "bbbbbbbbbb")
    upsert_entity(group, "person", "Cottalango Leon", ["Chairs the TSC."], "the 2026-03-02 meeting")

    out = upsert_entity(desk, "meeting", "DNA TSC 2026-03-02",
                        ["[[Cottalango Leon]] chaired it."], "the transcript",
                        mounts=[{"path": str(desk)}, {"path": str(group)}])

    page = (desk / out["path"]).read_text()
    assert "[[ws:bbbbbbbbbb/cottalango-leon]]" in page
    assert out["links_rewritten"] == [("Cottalango Leon", "[[ws:bbbbbbbbbb/cottalango-leon]]")]
    assert out["links_resolved"] == ["Cottalango Leon"] and out["links_missing"] == []


def test_a_name_with_a_page_here_is_left_local(tmp_path):
    desk = _workspace(tmp_path, "desk", "aaaaaaaaaa")
    group = _workspace(tmp_path, "group", "bbbbbbbbbb")
    for root in (desk, group):
        upsert_entity(root, "person", "Olga Avramenko", ["Attends."], "the meeting")
    out = upsert_entity(desk, "meeting", "M", ["[[Olga Avramenko]] spoke."], "the transcript",
                        mounts=[{"path": str(desk)}, {"path": str(group)}])
    assert out["links_rewritten"] == []
    assert "[[Olga Avramenko]]" in (desk / out["path"]).read_text()


def test_a_restated_fact_is_still_idempotent_after_a_rewrite(tmp_path):
    """The rewrite runs BEFORE the duplicate test — otherwise the same sentence, re-stated next
    turn, appends a second time because its stored form no longer matches its written form."""
    desk = _workspace(tmp_path, "desk", "aaaaaaaaaa")
    group = _workspace(tmp_path, "group", "bbbbbbbbbb")
    upsert_entity(group, "person", "Cottalango Leon", ["Chairs the TSC."], "the meeting")
    mounts = [{"path": str(desk)}, {"path": str(group)}]
    first = upsert_entity(desk, "meeting", "M", ["[[Cottalango Leon]] chaired it."], "s", mounts=mounts)
    again = upsert_entity(desk, "meeting", "M", ["[[Cottalango Leon]] chaired it."], "s", mounts=mounts)
    assert first["changed"] is True and again["changed"] is False


def test_no_mounts_is_the_old_behaviour_byte_for_byte(tmp_path):
    a = _workspace(tmp_path, "a", "aaaaaaaaaa")
    b = _workspace(tmp_path, "b", "bbbbbbbbbb")
    upsert_entity(b, "person", "X Y", ["f"], "s")
    out = upsert_entity(a, "meeting", "M", ["[[X Y]] came."], "s")
    assert "[[X Y]]" in (a / out["path"]).read_text() and out["links_rewritten"] == []


def test_a_mount_with_no_identity_file_is_skipped_not_fatal(tmp_path):
    a = _workspace(tmp_path, "a", "aaaaaaaaaa")
    unmigrated = tmp_path / "b"
    (unmigrated / "kg" / "entities").mkdir(parents=True)
    upsert_entity(unmigrated, "person", "X Y", ["f"], "s")
    out = upsert_entity(a, "meeting", "M", ["[[X Y]] came."], "s",
                        mounts=[{"path": str(a)}, {"path": str(unmigrated)}])
    assert out["links_rewritten"] == [] and "[[X Y]]" in (a / out["path"]).read_text()


# ── resolution, per reader ───────────────────────────────────────────────────────────────────────

@pytest.fixture()
def world(tmp_path):
    """One desk (owned by 126) and one group (126 is a member, 127 is not)."""
    (tmp_path / "126" / "kg" / "entities").mkdir(parents=True)
    (tmp_path / "grp" / "policy").mkdir(parents=True)
    (tmp_path / "grp" / "policy" / "members.json").write_text('[{"subject":"126","role":"owner"}]')
    upsert_entity(tmp_path / "grp", "person", "Cottalango Leon", ["Chairs the TSC."], "the meeting")
    upsert_entity(tmp_path / "126", "person", "Olga Avramenko", ["Attends."], "the meeting")
    reg = ids.WorkspaceRegistry()
    ids.migrate(tmp_path, reg)
    member = lambda root, slug, subject: "owner" if (slug, subject) == ("grp", "126") else None  # noqa: E731
    return tmp_path, reg, member


def test_readable_gives_the_targets_title_and_a_canonical_url(world):
    root, reg, member = world
    gid = reg.by_slug("grp")["id"]
    r = link_resolver.resolve(f"ws:{gid}/cottalango-leon", subject="126", root=root,
                              registry=reg, is_member=member)
    assert r["access"] == ids.ACCESS_READABLE
    assert r["title"] == "Cottalango Leon"
    assert r["url"] == f"/w/{gid}/kg/entities/person/cottalango-leon.md"
    assert r["writable"] is True                # a member of the group writes it


def test_not_yours_gives_a_title_and_no_url_and_is_not_an_error(world):
    root, reg, member = world
    gid = reg.by_slug("grp")["id"]
    r = link_resolver.resolve(f"ws:{gid}/cottalango-leon", subject="127", root=root,
                              registry=reg, is_member=member)
    assert r["access"] == ids.ACCESS_NOT_YOURS
    assert r["title"] == "Cottalango Leon"      # derived from the ref, never read out of the tree
    assert r["url"] is None
    assert r["writable"] is False
    assert r["workspace"] == "grp"              # the name the greyed chip says you don't have


def test_a_colleagues_desk_is_readable_and_not_writable(world):
    """Founder ruling, 2026-09-02: a desk is readable by any signed-in member of this instance and
    writable by its owner. A link between colleagues must not render `not-yours` — that says the
    page is somebody's secret, when a desk is company knowledge held by one person."""
    root, reg, member = world
    did = reg.by_slug("126")["id"]
    colleague = link_resolver.resolve(f"ws:{did}/olga-avramenko", subject="127", root=root,
                                      registry=reg, is_member=member)
    assert colleague["access"] == ids.ACCESS_READABLE
    assert colleague["writable"] is False
    assert colleague["url"] == f"/w/{did}/kg/entities/person/olga-avramenko.md"

    owner = link_resolver.resolve(f"ws:{did}/olga-avramenko", subject="126", root=root,
                                  registry=reg, is_member=member)
    assert owner["access"] == ids.ACCESS_READABLE and owner["writable"] is True


def test_a_desk_is_not_yours_from_outside_the_instance(world):
    """The one case `not-yours` still covers for a desk: no subject at all — an unauthenticated
    edge, or the company-layer gate closed before a subject was resolved."""
    root, reg, member = world
    did = reg.by_slug("126")["id"]
    out = link_resolver.resolve(f"ws:{did}/olga-avramenko", subject="", root=root,
                                registry=reg, is_member=member)
    assert out["access"] == ids.ACCESS_NOT_YOURS and out["url"] is None and out["writable"] is False


def test_gone_keeps_the_last_known_title(world):
    import shutil

    root, reg, member = world
    gid = reg.by_slug("grp")["id"]
    shutil.rmtree(root / "grp")
    r = link_resolver.resolve(f"ws:{gid}/cottalango-leon", subject="126", root=root,
                              registry=reg, is_member=member)
    assert r["access"] == ids.ACCESS_GONE and r["url"] is None and r["title"] == "Cottalango Leon"


def test_an_unknown_workspace_id_is_gone_never_a_crash(world):
    root, reg, member = world
    r = link_resolver.resolve("ws:zzzzzzzzzz/whoever", subject="126", root=root,
                              registry=reg, is_member=member)
    assert r["access"] == ids.ACCESS_GONE


def test_a_path_ref_resolves_and_cannot_escape_the_workspace(world):
    root, reg, member = world
    gid = reg.by_slug("grp")["id"]
    ok = link_resolver.resolve(f"ws:{gid}/kg/INDEX.md", subject="126", root=root,
                               registry=reg, is_member=member)
    assert ok["url"] == f"/w/{gid}/kg/INDEX.md"
    escape = link_resolver.resolve(f"ws:{gid}/../126/kg/INDEX.md", subject="126", root=root,
                                   registry=reg, is_member=member)
    assert escape.get("missing") is True and "126/kg" not in (escape["url"] or "")


def test_readable_but_missing_still_opens(world):
    """The panel's own empty state beats a chip that refuses its own click — the rule docLinks
    already follows for a missing [[wikilink]]."""
    root, reg, member = world
    gid = reg.by_slug("grp")["id"]
    r = link_resolver.resolve(f"ws:{gid}/nobody-yet", subject="126", root=root,
                              registry=reg, is_member=member)
    assert r["access"] == ids.ACCESS_READABLE and r["missing"] is True and r["url"]


def test_resolve_many_dedupes_and_caps(world):
    root, reg, member = world
    gid = reg.by_slug("grp")["id"]
    refs = [f"ws:{gid}/cottalango-leon"] * 5 + [f"ws:{gid}/nobody-yet"]
    out = link_resolver.resolve_many(refs, subject="126", root=root, registry=reg, is_member=member)
    assert len(out) == 2


# ── the whole point: a rename must not break anything ────────────────────────────────────────────

def test_a_link_survives_the_group_being_renamed(world):
    root, reg, member = world
    gid = reg.by_slug("grp")["id"]
    before = link_resolver.resolve(f"ws:{gid}/cottalango-leon", subject="126", root=root,
                                   registry=reg, is_member=member)
    ids.rename(reg, gid, "Digital Naming Authority")
    after = link_resolver.resolve(f"ws:{gid}/cottalango-leon", subject="126", root=root,
                                  registry=reg, is_member=member)
    assert after["url"] == before["url"] and after["access"] == ids.ACCESS_READABLE
    assert after["workspace"] == "Digital Naming Authority"


def test_a_link_survives_the_group_directory_moving(world):
    """The harder half: the SLUG changes (an un-share, a promotion, a re-home) and the id does not."""
    import shutil

    root, reg, member = world
    gid = reg.by_slug("grp")["id"]
    shutil.move(str(root / "grp"), str(root / "dna-2026"))
    ids.sync_workspace(root, "dna-2026", registry=reg)
    moved = lambda r, slug, subject: "owner" if (slug, subject) == ("dna-2026", "126") else None  # noqa: E731
    r = link_resolver.resolve(f"ws:{gid}/cottalango-leon", subject="126", root=root,
                              registry=reg, is_member=moved)
    assert r["access"] == ids.ACCESS_READABLE
    assert r["url"] == f"/w/{gid}/kg/entities/person/cottalango-leon.md"
