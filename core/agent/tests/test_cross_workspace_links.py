"""Links that cross workspaces, resolved for the reader (#1783; founder: "cross workspace links do
not work" — a contact page on a desk linking strategy nodes that live in another workspace).

Forms: `<slug>:<path>` and `vexa://ws/<slug>/<path>` (qualified), and a bare slug matched across
every workspace the reader can READ — case- and dash-insensitive, `.md` optional. A target in a
workspace the reader cannot read answers `not-yours` with a title derived from the ref, never read
from the page."""
from __future__ import annotations

import pytest

from control_plane import link_resolver, workspace_ids as ids
from workspaces.shared.entities import upsert_entity

NODE = "graph/sg/Oss-Led-Vendor-Pull-Growth.md"


@pytest.fixture()
def world(tmp_path):
    """A desk (126), `biz` (126 a member, 127 not) holding a strategy node, `grp` (126 a member)."""
    (tmp_path / "126" / "kg" / "entities").mkdir(parents=True)
    upsert_entity(tmp_path / "126", "person", "Pat Mason", ["Met at the summit."], "the meeting")
    for g in ("biz", "grp"):
        (tmp_path / g / "policy").mkdir(parents=True)
        (tmp_path / g / "policy" / "members.json").write_text('[{"subject":"126","role":"owner"}]')
    (tmp_path / "biz" / "graph" / "sg").mkdir(parents=True)
    (tmp_path / "biz" / NODE).write_text("---\ntitle: Secret Growth Thesis\n---\nbody\n")
    (tmp_path / "biz" / "graph" / "sg" / "Enterprise-Adoption.md").write_text("---\ntitle: Enterprise adoption\n---\n")
    (tmp_path / "grp" / "notes").mkdir(parents=True)
    (tmp_path / "grp" / "notes" / "enterprise-adoption.md").write_text("---\ntitle: Grp adoption notes\n---\n")
    reg = ids.WorkspaceRegistry()
    ids.migrate(tmp_path, reg)
    member = lambda root, slug, subject: "owner" if (slug in ("biz", "grp") and subject == "126") else None  # noqa: E731
    here = reg.by_slug("126")
    return tmp_path, reg, member, here


def _r(world, ref, subject="126"):
    root, reg, member, here = world
    return link_resolver.resolve(ref, subject=subject, root=root, registry=reg, here=here, is_member=member)


@pytest.mark.parametrize("ref", [
    "biz:graph/sg/Oss-Led-Vendor-Pull-Growth",
    "biz:graph/sg/oss_led_vendor_pull_growth.md",
    "biz:Graph/SG/oss-led-vendor-pull-growth",
    "vexa://ws/biz/graph/sg/Oss-Led-Vendor-Pull-Growth.md",
])
def test_a_qualified_link_opens_the_page_in_its_workspace(world, ref):
    r = _r(world, ref)
    assert r["access"] == ids.ACCESS_READABLE
    assert (r["slug"], r["path"]) == ("biz", NODE)
    assert r["title"] == "Secret Growth Thesis"
    assert r["url"].endswith("/" + NODE)


def test_a_bare_slug_is_found_in_the_one_readable_workspace_that_has_it(world):
    r = _r(world, "oss-led-vendor-pull-growth")
    assert r["access"] == ids.ACCESS_READABLE and (r["slug"], r["path"]) == ("biz", NODE)


def test_the_page_here_still_wins(world):
    r = _r(world, "Pat Mason")
    assert r["access"] == ids.ACCESS_READABLE and r.get("path") == "kg/entities/person/pat-mason.md"


def test_two_readable_matches_are_ambiguous_and_list_both(world):
    r = _r(world, "enterprise-adoption")
    assert r.get("ambiguous") is True and r["url"] is None
    assert sorted((c["slug"], c["path"]) for c in r["candidates"]) == [
        ("biz", "graph/sg/Enterprise-Adoption.md"), ("grp", "notes/enterprise-adoption.md")]


def test_a_target_the_reader_cannot_read_is_restricted_and_never_named(world):
    r = _r(world, "biz:graph/sg/Oss-Led-Vendor-Pull-Growth", subject="127")
    assert r["access"] == ids.ACCESS_NOT_YOURS and r["url"] is None
    assert r["workspace"] is None
    assert "Secret" not in str(r)
    assert r["title"] == "Oss Led Vendor Pull Growth"     # from the ref, not from the page


def test_a_bare_slug_never_searches_a_workspace_the_reader_cannot_read(world):
    root, reg, member, _ = world
    here = reg.by_slug("126")
    r = link_resolver.resolve("oss-led-vendor-pull-growth", subject="127", root=root, registry=reg,
                              here=here, is_member=member)
    assert "Secret" not in str(r) and not r.get("candidates") and r.get("slug") != "biz"


def test_an_unknown_qualifier_is_an_ordinary_title(world):
    r = _r(world, "Note: call back")
    assert r["access"] == ids.ACCESS_READABLE and r.get("missing") is True


def test_a_qualified_path_cannot_climb_out(world):
    r = _r(world, "biz:../126/kg/entities/person/pat-mason.md")
    assert r.get("path") is None


def test_mentioned_in_lists_readable_pages_that_link_here_and_never_an_unreadable_one(world):
    root, reg, member, _ = world
    (root / "126" / "kg" / "notes").mkdir(parents=True, exist_ok=True)
    (root / "126" / "kg" / "notes" / "summit.md").write_text("Met [[Pat Mason]] about [[biz:graph/sg/Oss-Led-Vendor-Pull-Growth]].\n")
    (root / "biz" / "graph" / "sg" / "people.md").write_text("Champion: [[pat-mason]].\n")
    got = link_resolver.backlinks("126", "kg/entities/person/pat-mason.md", subject="126", root=root,
                                  registry=reg, is_member=member)
    assert sorted((g["slug"], g["path"]) for g in got) == [("126", "kg/notes/summit.md"), ("biz", "graph/sg/people.md")]
    outsider = link_resolver.backlinks("126", "kg/entities/person/pat-mason.md", subject="127", root=root,
                                       registry=reg, is_member=member)
    assert [g["slug"] for g in outsider] == ["126"]          # the biz page is not readable to 127
    node = link_resolver.backlinks("biz", NODE, subject="126", root=root, registry=reg, is_member=member)
    assert ("126", "kg/notes/summit.md") in [(g["slug"], g["path"]) for g in node]


def test_a_chip_resolves_to_one_readable_page_and_never_lists_someone_elses_desk(world):
    """Founder, live: a "Qonto 3" chip listed "Qonto · dmitry@vexa.ai" twice and "Qonto · test@vexa.ai".
    Another person's desk only answers when nothing else has the page; a workspace is never named by
    an address."""
    root, reg, member, here = world
    (root / "127" / "kg" / "entities" / "company").mkdir(parents=True)
    (root / "127" / "kg" / "entities" / "company" / "qonto.md").write_text("---\ntitle: Qonto\n---\n")
    (root / "grp" / "kg").mkdir(parents=True, exist_ok=True)
    (root / "grp" / "kg" / "qonto.md").write_text("---\ntitle: Qonto\n---\n")
    reg2 = ids.WorkspaceRegistry()
    ids.migrate(root, reg2)
    r = link_resolver.resolve("qonto", subject="126", root=root, registry=reg2, here=reg2.by_slug("biz"), is_member=member)
    assert not r.get("ambiguous") and r["slug"] == "grp"                 # the shared page, not 127's desk
    assert "@" not in str(r.get("workspace") or "")
    (root / "grp" / "kg" / "qonto.md").unlink()
    only_other = link_resolver.resolve("qonto", subject="126", root=root, registry=reg2, here=reg2.by_slug("biz"), is_member=member)
    assert only_other["slug"] == "127" and "@" not in str(only_other.get("workspace") or "")


def test_mentioned_in_is_pages_that_link_on_purpose_once_each(world):
    """Founder, live: a "Mentioned in" wall of README ×40 (dmitry@vexa.ai), CLAUDE (admin@vexa.ai),
    AGENTS, Index. System and generated files never count, one file is one mention, a personal
    desk is never named by its owner's address, and a workspace's front page lists no mentions from
    inside itself."""
    root, reg, member, _ = world
    for rel, text in [("README.md", "[[Pat Mason]]"), ("CLAUDE.md", "[[pat-mason]]"), ("AGENTS.md", "[[pat-mason]]"),
                      ("kg/index.md", "- [[pat-mason]]"), ("_system/list.md", "[[pat-mason]]"),
                      ("kg/notes/call.md", "Call with [[Pat Mason]] on pricing.")]:
        (root / "126" / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / "126" / rel).write_text(text)
    got = link_resolver.backlinks("126", "kg/entities/person/pat-mason.md", subject="126", root=root, registry=reg, is_member=member)
    paths = [g["path"] for g in got if g["slug"] == "126"]
    assert paths == ["kg/notes/call.md"]
    assert all("@" not in str(g.get("workspace") or "") for g in got)
    (root / "126" / "kg" / "notes" / "front.md").write_text("[[README]]")
    assert link_resolver.backlinks("126", "README.md", subject="126", root=root, registry=reg, is_member=member) == []


def test_mentioned_in_is_links_that_resolve_here_never_a_shared_name_or_a_working_file(world):
    """Founder, live: a wall of ~40 chips — Analysis, Cadence, Company Page, Profile Diff, FIRST
    FIRE, "20260615 112823 C576", "2026 05 25 Pipeline Decisions Log", "2026 08 04 T 279 Website
    Claim Audit" — pages that named a namesake, and dated drafts and audits from a repo copy. Only a
    link that RESOLVES to this page counts; a working file counts only when it links with a path."""
    root, reg, member, _ = world
    files = {
        # the biz copy has its own pat-mason: its bare [[pat-mason]] links resolve THERE, not here
        ("biz", "graph/people/pat-mason.md"): "# Pat Mason (biz copy)",
        ("biz", "surfaces/linkedin/company-page.md"): "Owner: [[pat-mason]].",
        ("biz", "drafts/2026-05-25-pipeline-decisions-log.md"): "Spoke to [[pat-mason]].",
        ("biz", "chats/20260615-112823-c576.md"): "Pat Mason said hello.",
        ("biz", "drafts/2026-08-04-T-279-website-claim-audit.md"): "See [[126:kg/entities/person/pat-mason.md]].",
        # the reader's workspace: a real link by name, a plain-text mention, a dated note by name,
        # and a markdown link by relative path from a dated note
        ("126", "kg/notes/pricing.md"): "Call with [[Pat Mason]] on pricing.",
        ("126", "kg/notes/plain.md"): "Pat Mason was there too.",
        ("126", "kg/notes/2026-10-01-standup.md"): "[[pat-mason]] again.",
        ("126", "kg/notes/2026-10-02-sync.md"): "[Pat](../entities/person/pat-mason.md)",
    }
    for (ws, rel), text in files.items():
        (root / ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / ws / rel).write_text(text)
    got = link_resolver.backlinks("126", "kg/entities/person/pat-mason.md", subject="126", root=root, registry=reg, is_member=member)
    pairs = sorted((g["slug"], g["path"]) for g in got)
    assert ("126", "kg/notes/pricing.md") in pairs                                 # a link by name
    assert ("126", "kg/notes/2026-10-02-sync.md") in pairs                         # a working file, by path
    assert ("biz", "drafts/2026-08-04-T-279-website-claim-audit.md") in pairs      # ...or qualified
    for gone in [("126", "kg/notes/plain.md"), ("126", "kg/notes/2026-10-01-standup.md"),
                 ("biz", "surfaces/linkedin/company-page.md"), ("biz", "drafts/2026-05-25-pipeline-decisions-log.md"),
                 ("biz", "chats/20260615-112823-c576.md")]:
        assert gone not in pairs, gone
