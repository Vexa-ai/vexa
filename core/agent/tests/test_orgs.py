"""The org in agent-api (architecture A1): policy resolves through the org, workspaces carry one.

Two claims, both about today being unchanged and tomorrow being a data change:

- ZERO CHANGE NOW. With no org named, `system_mounts.global_root` answers exactly what the install
  answered before orgs (both `_global` modes: in-store, and an out-of-store configured path), and
  every access answer is the old one.
- A SECOND ORG IS UNREACHABLE FROM THE FIRST (prepared for later). Its policy tier is its own folder
  and never the default org's `_global`; an org the install does not know resolves to nothing; a
  viewer who names their org reads no workspace of another org — not a desk, not its `_global`, not
  a group they are on the roster of.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from control_plane import orgs
from control_plane import system_mounts
from control_plane import workspace_ids as ids

OTHER = "org-b"


@pytest.fixture(autouse=True)
def _only_the_default_org():
    yield
    orgs.forget_org(OTHER)


def _settings(root: Path, configured: str = ""):
    return SimpleNamespace(workspaces_dir=str(root), global_system_workspace_path=configured)


# ── zero change now ──────────────────────────────────────────────────────────────────────────────

def test_the_default_org_resolves_to_the_install_global_in_store(tmp_path):
    s = _settings(tmp_path)
    assert orgs.policy_source() == orgs.policy_source(orgs.DEFAULT_ORG_ID) == "_global"
    assert system_mounts.global_root(s) == tmp_path / "_global"
    assert system_mounts.global_root(s, tmp_path) == system_mounts.install_global_root(s, tmp_path)
    assert system_mounts.global_root(s, tmp_path, orgs.DEFAULT_ORG_ID) == tmp_path / "_global"


def test_the_default_org_resolves_to_a_configured_out_of_store_global(tmp_path):
    out = tmp_path / "elsewhere" / "_global"
    out.mkdir(parents=True)
    (tmp_path / "store" / "_global").mkdir(parents=True)          # a stale in-store one beside it
    s = _settings(tmp_path / "store", str(out))
    assert system_mounts.global_root(s) == out
    assert system_mounts.global_root(s, tmp_path / "store") == system_mounts.install_global_root(s, tmp_path / "store")


def test_a_workspace_with_no_org_is_in_the_default_org(tmp_path):
    reg = ids.WorkspaceRegistry()
    (tmp_path / "126").mkdir()
    rec = ids.sync_workspace(tmp_path, "126", registry=reg, owner="126")
    assert rec["org"] == orgs.DEFAULT_ORG_ID
    assert orgs.workspace_org({"slug": "x"}) == orgs.DEFAULT_ORG_ID
    assert orgs.same_org(None, rec) and orgs.same_org(orgs.DEFAULT_ORG_ID, rec)


def test_naming_the_default_org_changes_no_access_answer(tmp_path):
    reg = ids.WorkspaceRegistry()
    for n in ("_global", "126", "127"):
        (tmp_path / n).mkdir()
    ids.migrate(tmp_path, reg)
    for rec in (reg.by_slug("_global"), reg.by_slug("126"), reg.by_slug("127")):
        for subject in ("126", "127", ""):
            assert ids.access_for(rec, subject, viewer_org=orgs.DEFAULT_ORG_ID) == ids.access_for(rec, subject)
            assert ids.writable_for(rec, subject, viewer_org=orgs.DEFAULT_ORG_ID) == ids.writable_for(rec, subject)


# ── a second org is unreachable from the first (prepared for later) ─────────────────────────────

def test_an_unknown_org_resolves_to_nothing_never_to_global(tmp_path):
    with pytest.raises(orgs.UnknownOrg):
        system_mounts.global_root(_settings(tmp_path), tmp_path, OTHER)
    with pytest.raises(orgs.UnknownOrg):
        orgs.policy_source("no-such-org")


def test_a_second_org_has_its_own_policy_tier(tmp_path):
    orgs.set_policy_source(OTHER, f"_orgs/{OTHER}")
    s = _settings(tmp_path)
    theirs = system_mounts.global_root(s, tmp_path, OTHER)
    assert theirs == tmp_path / "_orgs" / OTHER
    assert theirs != system_mounts.global_root(s, tmp_path)


@pytest.mark.parametrize("source", ["_orgs/default", "_orgs/other", "../_global", "/etc", "_orgs/org-b/..", ""])
def test_an_org_cannot_be_pointed_at_another_orgs_tier(source):
    with pytest.raises(ValueError):
        orgs.set_policy_source(OTHER, source)


@pytest.mark.parametrize("bad", ["", "Org", "../x", "a/b", "x" * 65])
def test_an_org_id_is_a_plain_slug(bad):
    with pytest.raises(ValueError):
        orgs.set_policy_source(bad, "_global")


def test_another_orgs_workspaces_are_not_yours_whatever_their_kind(tmp_path):
    reg = ids.WorkspaceRegistry()
    for n in ("_global", "126", "127"):
        (tmp_path / n).mkdir()
    (tmp_path / "grp" / "policy").mkdir(parents=True)
    (tmp_path / "grp" / "policy" / "members.json").write_text('[{"subject": "owner1", "role": "owner"}]')
    ids.migrate(tmp_path, reg)
    member = lambda root, slug, subject: "contributor"            # noqa: E731 — on every roster
    theirs = [{**reg.by_slug(s), "org": OTHER} for s in ("_global", "126", "127", "grp")]
    for rec in theirs:
        # Readable to the default org's people today (no org named) — the old answer…
        assert ids.access_for(rec, "126", root=tmp_path, is_member=member) == ids.ACCESS_READABLE
        # …and not theirs the moment the viewer's org is named.
        assert ids.access_for(rec, "126", root=tmp_path, is_member=member,
                              viewer_org=orgs.DEFAULT_ORG_ID) == ids.ACCESS_NOT_YOURS
        assert ids.writable_for(rec, "126", root=tmp_path, is_member=member,
                                viewer_org=orgs.DEFAULT_ORG_ID) is False
        assert ids.access_for(rec, "126", root=tmp_path, is_member=member,
                              viewer_org=OTHER) == ids.ACCESS_READABLE
    # The owner of a desk in another org does not reach it from the default org either.
    assert ids.access_for(theirs[1], "126", viewer_org=orgs.DEFAULT_ORG_ID) == ids.ACCESS_NOT_YOURS


# ── the boundary ENFORCES: every caller gets the viewer's org from identity ──────────────────────

class _Identity:
    def __init__(self, orgs_by_subject):
        self.orgs = orgs_by_subject
        self.asked = []

    def __call__(self, subject):
        self.asked.append(subject)
        return {"org": {"id": self.orgs.get(subject, orgs.DEFAULT_ORG_ID)}}


@pytest.fixture()
def directory():
    ident = _Identity({"200": "p-200"})                       # 200 is an individual account
    orgs.configure_directory(orgs.OrgDirectory("http://identity", "s", fetch=ident))
    yield ident
    orgs.configure_directory(None)


def _install(tmp_path):
    reg = ids.WorkspaceRegistry()
    for n in ("_global", "126", "127", "200"):
        (tmp_path / n).mkdir()
    (tmp_path / "grp" / "policy").mkdir(parents=True)
    (tmp_path / "grp" / "policy" / "members.json").write_text('[{"subject": "owner1", "role": "owner"}]')
    ids.migrate(tmp_path, reg)
    return reg


def test_with_one_org_every_answer_is_unchanged(tmp_path, directory):
    reg = _install(tmp_path)
    member = lambda root, slug, subject: "contributor"            # noqa: E731
    for slug in ("_global", "126", "127", "grp"):
        rec = reg.by_slug(slug)
        for subject in ("126", "127"):
            orgs.configure_directory(None)
            before = (ids.access_for(rec, subject, root=tmp_path, is_member=member),
                      ids.writable_for(rec, subject, root=tmp_path, is_member=member))
            orgs.configure_directory(orgs.OrgDirectory("http://identity", "s", fetch=directory))
            assert (ids.access_for(rec, subject, root=tmp_path, is_member=member),
                    ids.writable_for(rec, subject, root=tmp_path, is_member=member)) == before


def test_a_second_orgs_workspaces_are_unreachable_through_every_caller(tmp_path, directory):
    """No caller names an org: the boundary still holds, because access_for asks identity."""
    reg = _install(tmp_path)
    member = lambda root, slug, subject: "contributor"            # noqa: E731 — on every roster
    company = [reg.by_slug(s) for s in ("126", "127", "grp")]
    for rec in company:                                            # an individual reads no company tree
        assert ids.access_for(rec, "200", root=tmp_path, is_member=member) == ids.ACCESS_NOT_YOURS
        assert ids.writable_for(rec, "200", root=tmp_path, is_member=member) is False
    own = reg.by_slug("200")                                       # …but their own desk is theirs
    assert ids.access_for(own, "200", root=tmp_path) == ids.ACCESS_READABLE
    assert ids.writable_for(own, "200", root=tmp_path) is True
    assert ids.access_for(own, "126", root=tmp_path) == ids.ACCESS_NOT_YOURS   # the company cannot read it
    # the install's own _global stays every org's policy layer (A1)
    assert ids.access_for(reg.by_slug("_global"), "200", root=tmp_path) == ids.ACCESS_READABLE
    # a group stamped with the individual's org is theirs, and not the company's
    theirs = {**reg.by_slug("grp"), "org": "p-200"}
    assert ids.access_for(theirs, "200", root=tmp_path, is_member=member) == ids.ACCESS_READABLE
    assert ids.access_for(theirs, "126", root=tmp_path, is_member=member) == ids.ACCESS_NOT_YOURS


def test_the_directory_caches_and_falls_back_to_the_last_answer(directory):
    t = [0.0]
    calls = {"n": 0}

    def fetch(subject):
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError("identity down")
        return {"org": {"id": "p-200"}}
    d = orgs.OrgDirectory("http://identity", "s", fetch=fetch, ttl=10, clock=lambda: t[0])
    assert d.org_of("200") == "p-200"
    assert d.org_of("200") == "p-200" and calls["n"] == 1        # cached
    t[0] = 20
    assert d.org_of("200") == "p-200"                             # identity down: last answer kept


def test_non_person_subjects_are_never_looked_up(directory):
    assert orgs.viewer_org("") is None and orgs.viewer_org("\x00anyone") is None
    assert directory.asked == []
