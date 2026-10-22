"""s1a-policy (PRD 0.13.4 D5 + D7 stub + D26): the policy store outside every tree, its migration
from the legacy in-tree files, fail-closed reads, invite rank and caps, the policy evaluator, and the
external-remote sync refusal (deny test 28)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from control_plane import policy_decide as pd
from control_plane import room_principal as rp
from control_plane import workspace_membership as m
from workspaces.shared import workspace_paths as wpaths

from tests.test_r1_rooms import world  # noqa: F401  (fixture: `team` owned by alice, bob contributor)


def _ws(root: Path, slug: str) -> Path:
    ws = root / slug
    ws.mkdir(parents=True)
    return ws


def _legacy(ws: Path, name: str, value) -> None:
    (ws / "policy").mkdir(exist_ok=True)
    (ws / "policy" / f"{name}.json").write_text(json.dumps(value))


ROSTER = [{"subject": "alice", "role": "owner", "added_by": "alice", "added_at": "t"},
          {"subject": "bob", "role": "contributor", "added_by": "alice", "added_at": "t"}]


# ── migration: legacy in-tree files are adopted once, then the tree is never read ────────────────

def test_legacy_roster_is_read_and_adopted_into_the_store(tmp_path):
    ws = _ws(tmp_path, "team")
    _legacy(ws, "members", ROSTER)
    _legacy(ws, "visibility", {"visibility": "open"})
    _legacy(ws, "connections", [{"workspace": "handbook", "added_by": "alice"}])
    assert not m.policy_in_store(tmp_path, "team")
    assert m.is_member(tmp_path, "team", "bob") == "contributor"          # read-compatible
    assert m.policy_in_store(tmp_path, "team")                            # …and adopted, all three
    for name in ("members", "visibility", "connections"):
        assert (tmp_path / m.POLICY_STORE_DIR / "team" / f"{name}.json").is_file()
    assert rp.visibility(tmp_path, "team") == "open"
    assert [c["workspace"] for c in rp.connections(tmp_path, "team")] == ["handbook"]


def test_after_adoption_a_tree_edit_confers_nothing(tmp_path):
    ws = _ws(tmp_path, "team")
    _legacy(ws, "members", ROSTER)
    m.migrate_policy_store(tmp_path)
    _legacy(ws, "members", [{**r, "role": "owner"} for r in ROSTER])     # bob forges the mirror
    _legacy(ws, "visibility", {"visibility": "open"})                   # …and opens the workspace
    assert m.is_member(tmp_path, "team", "bob") == "contributor"
    with pytest.raises(m.MembershipError):
        m.require_role(tmp_path, "team", "bob", "owner")
    assert rp.visibility(tmp_path, "team") == "closed"


def test_boot_migration_adopts_every_tree_and_is_idempotent(tmp_path):
    for slug in ("a", "b"):
        _legacy(_ws(tmp_path, slug), "members", ROSTER)
    _ws(tmp_path, "desk")                                                # no policy: nothing adopted
    assert sorted(m.migrate_policy_store(tmp_path)) == ["a", "b"]
    assert m.migrate_policy_store(tmp_path) == []
    assert not m.policy_in_store(tmp_path, "desk")


# ── M3 (gate 0.13.4 final): the boot sweep is the one reader of the trees ───────────────────────

def _restart() -> None:
    """A new agent-api process: nothing it swept is remembered in memory."""
    getattr(m, "_SWEPT", set()).clear()
    m.set_policy_store(m.policy_store())


def test_after_the_boot_sweep_a_tree_planted_in_a_new_workspace_confers_nothing(tmp_path):
    _legacy(_ws(tmp_path, "team"), "members", ROSTER)
    assert m.migrate_policy_store(tmp_path) == ["team"]
    # later, a tree nobody shared arrives with a roster in it (a restored backup, an attached clone)
    _legacy(_ws(tmp_path, "planted"), "members", ROSTER)
    _legacy(tmp_path / "planted", "visibility", {"visibility": "open"})
    assert m.is_member(tmp_path, "planted", "bob") is None
    assert not m.policy_in_store(tmp_path, "planted")
    assert rp.visibility(tmp_path, "planted") == "closed"
    assert "planted" not in {r["workspace_id"] for r in m.list_memberships(tmp_path, "bob")}
    assert m.is_member(tmp_path, "team", "bob") == "contributor"            # the swept one still answers


def test_once_the_adoption_is_recorded_no_later_boot_reads_a_tree(tmp_path, monkeypatch):
    _legacy(_ws(tmp_path, "team"), "members", ROSTER)
    m.migrate_policy_store(tmp_path)
    assert m.adoption_recorded(tmp_path)
    _legacy(_ws(tmp_path, "planted"), "members", ROSTER)
    _restart()
    read: list = []
    real = m._legacy_sources
    monkeypatch.setattr(m, "_legacy_sources", lambda *a, **k: read.append(k) or real(*a, **k))
    assert m.migrate_policy_store(tmp_path) == []
    assert m.is_member(tmp_path, "planted", "bob") is None
    assert not any(k.get("tree") for k in read), read
    assert m.is_member(tmp_path, "team", "bob") == "contributor"


def test_upgrade_from_0133_adopts_rosters_and_tree_invites_once(tmp_path):
    """A 0.13.3 instance on the file backend: rosters, visibility and invites in the trees, no store."""
    ws = _ws(tmp_path, "team")
    _legacy(ws, "members", ROSTER)
    _legacy(ws, "visibility", {"visibility": "open"})
    minted = m.hash_token("tok-0133")
    (ws / "policy" / "invites.json").write_text(json.dumps(
        [{"id": "i1", "hash": minted, "role": "viewer", "uses": 0, "max_uses": 5, "revoked": False}]))
    assert not m.adoption_recorded(tmp_path)
    assert m.migrate_policy_store(tmp_path) == ["team"]                    # the first boot on 0.13.4
    assert m.adoption_recorded(tmp_path)
    assert m.is_member(tmp_path, "team", "bob") == "contributor"
    assert rp.visibility(tmp_path, "team") == "open"
    assert m.find_invite(tmp_path, "tok-0133")[0] == "team"                 # the invite still redeems
    # after the boot the tree's invite file is nobody's: a forged one finds nothing
    forged = m.hash_token("tok-forged")
    (ws / "policy" / "invites.json").write_text(json.dumps([{"id": "i9", "hash": forged, "role": "owner"}]))
    _restart()
    m.migrate_policy_store(tmp_path)                                        # a second boot
    assert m.find_invite(tmp_path, "tok-forged") is None
    assert m.find_invite(tmp_path, "tok-0133")[0] == "team"


def test_a_sweep_that_could_not_adopt_records_nothing_and_the_next_boot_does(tmp_path, registry):
    _legacy(_ws(tmp_path, "team"), "members", ROSTER)
    registry.down = True
    assert m.migrate_policy_store(tmp_path) == []
    assert not m.adoption_recorded(tmp_path)
    with pytest.raises(m.MembershipError):
        m.is_member(tmp_path, "team", "bob")                                 # refused, never the tree's word
    registry.down = False
    assert m.is_member(tmp_path, "team", "bob") is None                     # this process swept: no tree read
    _restart()
    assert m.migrate_policy_store(tmp_path) == ["team"]                      # the next boot adopts
    assert m.adoption_recorded(tmp_path)
    assert m.is_member(tmp_path, "team", "bob") == "contributor"


def test_writing_one_file_migrates_the_others_first(tmp_path):
    """A role change on a legacy workspace must not strand its visibility in the tree (it would
    silently close the workspace)."""
    ws = _ws(tmp_path, "team")
    _legacy(ws, "members", ROSTER)
    _legacy(ws, "visibility", {"visibility": "open"})
    m.set_role(tmp_path, "team", "bob", "viewer", changed_by="alice", index=m.InMemoryMembershipIndex())
    assert m.is_member(tmp_path, "team", "bob") == "viewer"
    assert rp.visibility(tmp_path, "team") == "open"


def test_writes_keep_the_tree_mirror(tmp_path):
    ws = _ws(tmp_path, "team")
    m.ensure_owner(tmp_path, "team", "alice", index=m.InMemoryMembershipIndex())
    mirror = json.loads((ws / m.MEMBERS_FILE).read_text())
    assert [r["subject"] for r in mirror] == ["alice"]
    assert m.read_members(tmp_path, "team") == mirror


def test_legacy_read_off_ignores_unmigrated_trees(tmp_path, monkeypatch):
    ws = _ws(tmp_path, "team")
    _legacy(ws, "members", ROSTER)
    monkeypatch.setenv(m.LEGACY_READ_ENV, "0")
    assert m.is_member(tmp_path, "team", "bob") is None
    assert m.migrate_policy_store(tmp_path) == []


def test_rules_stay_in_git_and_are_never_store_records(tmp_path):
    ws = _ws(tmp_path, "team")
    _legacy(ws, "members", ROSTER)
    _legacy(ws, "rules", [{"id": "ws.r", "effect": "deny", "action": "x"}])
    m.migrate_policy_store(tmp_path)
    assert not (tmp_path / m.POLICY_STORE_DIR / "team" / "rules.json").exists()
    assert m.read_rules(tmp_path, "team")[0]["id"] == "ws.r"


def test_unshare_drops_the_store_and_a_reused_id_starts_empty(tmp_path):
    _ws(tmp_path, "team")
    m.ensure_owner(tmp_path, "team", "alice", index=m.InMemoryMembershipIndex())
    wpaths.remove_tree(tmp_path / "team")                # un-share moves the tree away…
    assert m.drop_policy_store(tmp_path, "team") is True  # …and the route drops the store
    assert not m.policy_in_store(tmp_path, "team")
    (tmp_path / "team").mkdir()                           # a later workspace with the same id
    assert m.read_members(tmp_path, "team") == []


# ── fail closed (FD3) ────────────────────────────────────────────────────────────────────────────

def test_an_unreadable_store_refuses_instead_of_answering_empty(tmp_path):
    _ws(tmp_path, "team")
    m.ensure_owner(tmp_path, "team", "alice", index=m.InMemoryMembershipIndex())
    (tmp_path / m.POLICY_STORE_DIR / "team" / "members.json").write_text("{not json")
    with pytest.raises(m.MembershipError) as e:
        m.is_member(tmp_path, "team", "alice")
    assert e.value.status == 503 and m.POLICY_UNREADABLE in str(e.value)
    assert m.list_memberships(tmp_path, "alice") == []                    # not listed, not granted


def test_an_unreadable_visibility_is_closed(tmp_path):
    _ws(tmp_path, "team")
    rp.set_visibility(tmp_path, "team", "open")
    (tmp_path / m.POLICY_STORE_DIR / "team" / "visibility.json").write_text("[[[")
    assert rp.visibility(tmp_path, "team") == "closed"


def test_policy_paths_are_reserved_for_callers():
    for rel in ("policy/members.json", "policy/visibility.json", "a/../policy/x"):
        with pytest.raises(wpaths.PathRefused):
            wpaths.relative_parts(rel)
    assert wpaths.is_platform_policy_path("policy/members.json")
    assert not wpaths.is_platform_policy_path(".git/config")
    assert not wpaths.is_platform_policy_path("notes/policy.md")


def test_a_reserved_directory_spelt_in_another_case_is_reserved_too():
    """On APFS or NTFS `Policy/` and `.GIT/` are the same directories as `policy/` and `.git/`."""
    for rel in ("Policy/members.json", "POLICY/visibility.json", ".GIT/config", ".Git/hooks/pre-commit",
                ".VEXA/workspace.json", "x/.Claude/settings.json"):
        with pytest.raises(wpaths.PathRefused):
            wpaths.relative_parts(rel)
    assert wpaths.relative_parts("notes/Policy.md") == ["notes", "Policy.md"]


# ── invites: role ≤ the sender's, owner only by the owner act, caps ──────────────────────────────

@pytest.fixture
def team(tmp_path):
    _ws(tmp_path, "team")
    idx = m.InMemoryMembershipIndex()
    m.ensure_owner(tmp_path, "team", "alice", index=idx)
    m.grant_membership(tmp_path, "team", "bob", "contributor", added_by="alice", index=idx)
    m.grant_membership(tmp_path, "team", "vic", "viewer", added_by="alice", index=idx)
    return tmp_path, idx


def test_an_invite_never_outranks_its_sender(team):
    root, _ = team
    m.mint_invite(root, "team", role="contributor", created_by="bob")
    with pytest.raises(m.MembershipError) as e:
        m.mint_invite(root, "team", role="contributor", created_by="vic")
    assert e.value.status == 403
    with pytest.raises(m.MembershipError):
        m.mint_invite(root, "team", role="viewer", created_by="mallory")     # not a member at all


def test_owner_only_through_the_owner_act(team):
    root, _ = team
    with pytest.raises(m.MembershipError):
        m.mint_invite(root, "team", role="owner", created_by="alice")        # a link never makes one
    with pytest.raises(m.MembershipError):
        m.mint_invite(root, "team", role="owner", created_by="bob", owner_act=True)
    m.mint_invite(root, "team", role="owner", created_by="alice", owner_act=True,
                  mode="restricted", allowed_emails=["c@x.test"])


@pytest.mark.parametrize("kw", [{"max_uses": m.MAX_INVITE_USES + 1},
                                {"expires_in_sec": m.MAX_INVITE_EXPIRES_IN_SEC + 1}])
def test_invite_caps(team, kw):
    root, _ = team
    with pytest.raises(m.MembershipError) as e:
        m.mint_invite(root, "team", role="viewer", created_by="alice", **kw)
    assert e.value.status == 400


def test_a_legacy_owner_invite_from_a_contributor_does_not_redeem(team):
    """An invite minted before the caps (written straight into the store, as old code did) grants
    nothing above what its sender holds today."""
    root, idx = team
    tok = "legacy-token"
    m._write_invites(root, "team", [{"id": "old", "hash": m.hash_token(tok), "role": "owner",
                                     "mode": "open", "allowed_emails": [], "expires_at": 4102444800,
                                     "max_uses": 5, "uses": 0, "created_by": "bob",
                                     "created_at": "t", "revoked": False}])
    with pytest.raises(m.MembershipError) as e:
        m.accept_invite(root, "team", token=tok, subject="eve", index=idx)
    assert e.value.status == 410
    assert m.is_member(root, "team", "eve") is None


def test_a_demoted_senders_link_stops_granting_above_them(team):
    root, idx = team
    minted = m.mint_invite(root, "team", role="contributor", created_by="bob", max_uses=5)
    m.set_role(root, "team", "bob", "viewer", changed_by="alice", index=idx)
    with pytest.raises(m.MembershipError):
        m.accept_invite(root, "team", token=minted.token, subject="eve", index=idx)


def test_invites_go_through_the_one_store_interface(team):
    root, _ = team
    m.mint_invite(root, "team", role="viewer", created_by="alice")
    rows = m.store_get(root, "team", "invites")
    assert rows and rows[0]["role"] == "viewer"
    assert m.invites_path(root, "team").is_file()


# ── the evaluator stub (D7) ──────────────────────────────────────────────────────────────────────

def test_owner_session_may_change_policy_and_nobody_else():
    ok = pd.decide(pd.Ask("alice", "policy.change", role="owner", credential=pd.SESSION), workspace_rules=[])
    assert ok.allowed and ok.rule.startswith("interim.owner-session-changes-policy@")
    for ask in (pd.Ask("bob", "policy.change", role="contributor", credential=pd.SESSION),
                pd.Ask("alice", "policy.change", role="owner", credential=pd.DELEGATED),
                pd.Ask("alice", "policy.change", role="owner", credential=pd.KEY),
                pd.Ask("ws:team", "policy.change", role="owner", principal_kind="ws")):
        assert pd.decide(ask, workspace_rules=[]).decision == pd.DENY, ask


def test_deny_beats_allow_across_levels():
    ws_rules = [{"id": "ws.allow-all", "effect": "allow", "action": "*"},
                {"id": "ws.no-delegated", "effect": "deny", "action": "crm.read", "credential": [pd.DELEGATED]}]
    assert pd.decide(pd.Ask("a", "crm.read", credential=pd.DELEGATED), workspace_rules=ws_rules).rule \
        .startswith("ws.no-delegated@")
    assert pd.decide(pd.Ask("a", "crm.read", credential=pd.SESSION), workspace_rules=ws_rules).allowed
    # a workspace allow never widens the platform floor
    out = pd.decide(pd.Ask("a", "policy.change", role="owner", credential=pd.DELEGATED), workspace_rules=ws_rules)
    assert out.decision == pd.DENY and out.rule.startswith("floor.")


def test_nothing_allowing_is_a_deny():
    out = pd.decide(pd.Ask("a", "something.new"), workspace_rules=[])
    assert out.decision == pd.DENY and out.rule.startswith("no_rule@")


def test_an_unreadable_rule_set_refuses(tmp_path):
    _legacy(_ws(tmp_path, "team"), "members", ROSTER)
    (tmp_path / "team" / "policy" / "rules.json").write_text("nope{")
    out = pd.decide(pd.Ask("alice", "policy.change", workspace="team", role="owner"), root=tmp_path)
    assert out.decision == pd.REFUSE and out.reason == pd.POLICY_UNREADABLE


def test_rule_sha_names_the_rule_set():
    a = pd.rules_sha([{"id": "x"}])
    assert a == pd.rules_sha([{"id": "x"}]) and a != pd.rules_sha([{"id": "y"}]) and len(a) == 12


def test_credential_class_of_a_request():
    """D13: a session only when identity said so; scopes prove nothing; delegated beats everything."""
    assert pd.credential_of({"x-user-credential-class": "session"}) == pd.SESSION
    assert pd.credential_of({"x-user-credential-class": "session", "x-user-scopes": "bot"}) == pd.SESSION
    assert pd.credential_of({}) == pd.KEY
    assert pd.credential_of({"x-user-scopes": "browser"}) == pd.KEY
    assert pd.credential_of({"x-user-scopes": "bot,tx,browser", "x-user-credential-class": "key"}) == pd.KEY
    assert pd.credential_of({"x-user-scopes": "bot,tx"}) == pd.KEY
    assert pd.credential_of({"x-user-credential-class": "SeSsIoN-ish"}) == pd.KEY
    assert pd.credential_of({"x-user-regime": "human", "x-user-delegation-workspaces": "team"}) == pd.DELEGATED
    assert pd.credential_of({"x-user-regime": "human", "x-user-delegation-workspaces": "team",
                             "x-user-credential-class": "session"}) == pd.DELEGATED


# ── deny test 28: an external-remote sync that touches policy/ is refused ────────────────────────

def _run(cwd: Path, *a: str) -> str:
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def test_deny_28_a_pull_that_changes_policy_is_refused(tmp_path, monkeypatch):
    from control_plane.repo_ref import LOCAL_ROOTS_ENV
    from control_plane.workspace_git_sync import RemoteSyncError, pull_origin
    monkeypatch.setenv(LOCAL_ROOTS_ENV, str(tmp_path))
    bare = tmp_path / "remote.git"
    bare.mkdir()
    _run(bare, "init", "-q", "--bare", "-b", "main")
    clones = []
    for name in ("a", "b"):
        subprocess.run(["git", "clone", "-q", str(bare), str(tmp_path / name)], check=True, capture_output=True)
        _run(tmp_path / name, "config", "user.email", "t@t")
        _run(tmp_path / name, "config", "user.name", "t")
        clones.append(tmp_path / name)
        if name == "a":
            (tmp_path / "a" / "README.md").write_text("v0\n")
            _run(tmp_path / "a", "add", "-A")
            _run(tmp_path / "a", "commit", "-q", "-m", "init")
            _run(tmp_path / "a", "push", "-q", "origin", "main")
    a, b = clones
    _run(b, "pull", "-q", "origin", "main")
    (b / "policy").mkdir()
    (b / "policy" / "members.json").write_text('[{"subject":"mallory","role":"owner"}]')
    _run(b, "add", "-A")
    _run(b, "commit", "-q", "-m", "make me owner")
    _run(b, "push", "-q", "origin", "main")
    head = _run(a, "rev-parse", "HEAD")
    with pytest.raises(RemoteSyncError) as e:
        pull_origin(a)
    assert "policy/" in str(e.value)
    assert _run(a, "rev-parse", "HEAD") == head and not (a / "policy").exists()


# ── the registry backend (identity DB, admin-api the one writer): migration into it ──────────────

class _Registry:
    """A PolicyStore with the identity registry's semantics (rows keyed by workspace, root ignored)."""

    def __init__(self):
        self.ws, self.inv, self.rooms, self.down = {}, {}, {}, False

    def room_get(self, root, rid):
        self._up()
        return list(self.rooms[rid]) if rid in self.rooms else None

    def room_put(self, root, rid, rows):
        self._up()
        self.rooms[rid] = list(rows)

    def room_drop(self, root, rid):
        self._up()
        return self.rooms.pop(rid, None) is not None

    def _up(self):
        if self.down:
            raise ConnectionError("registry down")

    def has(self, root, wid):
        self._up()
        return wid in self.ws

    def get(self, root, wid, name):
        self._up()
        if name == "invites":
            return list(self.inv.get(wid, [])) if wid in self.inv or wid in self.ws else None
        return (self.ws.get(wid) or {}).get(name)

    def put(self, root, wid, name, value):
        self._up()
        if name == "invites":
            self.inv[wid] = list(value)
        else:
            self.ws.setdefault(wid, {})[name] = value

    def drop(self, root, wid):
        self._up()
        return bool(self.ws.pop(wid, None) is not None) | bool(self.inv.pop(wid, None))

    def member_workspaces(self, root, subject):
        self._up()
        return [{"workspace_id": w, "role": r["role"], "added_at": r.get("added_at")}
                for w, d in self.ws.items() for r in d.get("members", []) if r["subject"] == subject]

    def invite_by_hash(self, root, h):
        self._up()
        return next(((w, r) for w, rows in self.inv.items() for r in rows if r.get("hash") == h), None)


@pytest.fixture
def registry():
    reg = _Registry()
    m.set_policy_store(reg)
    yield reg
    m.set_policy_store(None)


def test_boot_moves_trees_interim_files_and_invites_into_the_registry(tmp_path, registry):
    # a workspace still on its tree, with a legacy in-tree invite
    a = _ws(tmp_path, "a")
    _legacy(a, "members", ROSTER)
    _legacy(a, "visibility", {"visibility": "open"})
    (a / "policy" / "invites.json").write_text(json.dumps([{"id": "i1", "hash": "h1", "role": "viewer"}]))
    # a workspace on the interim file store (s1a's first cut), with an .invites file
    _ws(tmp_path, "b")
    m.FilePolicyStore().put(tmp_path, "b", "members", ROSTER)
    m.FilePolicyStore().put(tmp_path, "b", "connections", [{"workspace": "a"}])
    m.FilePolicyStore().put(tmp_path, "b", "invites", [{"id": "i2", "hash": "h2", "role": "viewer"}])
    assert sorted(m.migrate_policy_store(tmp_path)) == ["a", "b"]
    assert registry.ws["a"]["visibility"] == {"visibility": "open"}
    assert registry.inv["a"][0]["id"] == "i1" and registry.inv["b"][0]["id"] == "i2"
    assert registry.ws["b"]["connections"] == [{"workspace": "a"}]
    assert m.is_member(tmp_path, "b", "bob") == "contributor"
    assert rp.visibility(tmp_path, "a") == "open"
    assert {r["workspace_id"] for r in m.list_memberships(tmp_path, "bob")} == {"a", "b"}


def test_boot_moves_a_room_of_twos_hidden_workspace_as_hidden(tmp_path, registry):
    """A pair workspace (FD4) on the interim file store keeps its kind in the registry: it stays out
    of every listing, never surfacing as an ordinary shared workspace after the store switch."""
    _ws(tmp_path, "pair-ab")
    pair = [{"subject": "alice", "role": "owner", "added_by": "alice", "added_at": "t"},
            {"subject": "bob", "role": "owner", "added_by": "alice", "added_at": "t"}]
    m.FilePolicyStore().put(tmp_path, "pair-ab", "members", pair)
    m.FilePolicyStore().put(tmp_path, "pair-ab", "kind", {"kind": "pair", "people": ["alice", "bob"]})
    assert m.migrate_policy_store(tmp_path) == ["pair-ab"]
    assert registry.ws["pair-ab"]["kind"]["kind"] == "pair"
    assert m.is_pair(tmp_path, "pair-ab")
    assert m.is_member(tmp_path, "pair-ab", "bob") == "owner"
    assert m.list_memberships(tmp_path, "bob") == []


def test_a_closed_rooms_interim_rows_are_adopted_before_the_projection(tmp_path, registry):
    """A closed room's members decided on the file store (``.rooms/<id>.json``) are what the registry
    adopts — not the participant projection, which still lists someone the owner removed."""
    from control_plane import room_membership as rm

    rows = [{"subject": "alice", "role": "owner", "kind": "human", "added_by": "alice", "added_at": "t"},
            {"subject": "carol", "role": "member", "kind": "human", "added_by": "alice", "added_at": "t"}]
    m.FilePolicyStore().room_put(tmp_path, "s-room1", rows)

    class _Projection:
        def participants(self, sid):
            return [{"principal": p, "role": "member", "kind": "human"} for p in ("alice", "bob", "carol")]

    got = rm.explicit_rows(_Projection(), tmp_path, "s-room1")
    assert {r["subject"] for r in got} == {"alice", "carol"}
    assert registry.rooms["s-room1"] == rows
    rm.forget(tmp_path, "s-room1")                  # an opened room: no rows anywhere to re-adopt
    assert "s-room1" not in registry.rooms and m.FilePolicyStore().room_get(tmp_path, "s-room1") is None


def test_registry_writes_and_finds_invites(tmp_path, registry):
    _ws(tmp_path, "team")
    m.ensure_owner(tmp_path, "team", "alice", index=m.InMemoryMembershipIndex())
    minted = m.mint_invite(tmp_path, "team", role="viewer", created_by="alice")
    assert m.find_invite(tmp_path, minted.token)[0] == "team"
    m.accept_invite(tmp_path, "team", token=minted.token, subject="dave", index=m.InMemoryMembershipIndex())
    assert m.is_member(tmp_path, "team", "dave") == "viewer"
    assert registry.inv["team"][0]["uses"] == 1


def test_a_down_registry_refuses(tmp_path, registry):
    _ws(tmp_path, "team")
    m.ensure_owner(tmp_path, "team", "alice", index=m.InMemoryMembershipIndex())
    m.set_policy_store(registry)                 # forget what this process already knows
    registry.down = True
    with pytest.raises(m.MembershipError) as e:
        m.is_member(tmp_path, "team", "alice")
    assert e.value.status == 503
    assert rp.visibility(tmp_path, "team") == "closed"


def test_drop_forgets_the_registry_row_and_the_interim_files(tmp_path, registry):
    _ws(tmp_path, "team")
    m.FilePolicyStore().put(tmp_path, "team", "members", ROSTER)
    m.migrate_policy_store(tmp_path)
    assert m.drop_policy_store(tmp_path, "team")
    assert "team" not in registry.ws and not m.FilePolicyStore().has(tmp_path, "team")


def test_the_http_port_maps_the_registry_doors(monkeypatch):
    """AdminApiPolicyStore speaks admin-api's /internal/registry doors with the internal secret."""
    import http.server
    import threading
    from shared.adapters import AdminApiPolicyStore
    seen = []
    state = {"members": [{"subject": "alice", "role": "owner"}], "visibility": "open", "connections": []}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body):
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            seen.append(("GET", self.path, self.headers.get("X-Internal-Secret")))
            if self.path == "/internal/registry/workspaces/team":
                return self._send(200, {"workspace": {"visibility": state["visibility"],
                                                      "connections": state["connections"]},
                                        "members": state["members"]})
            if self.path == "/internal/registry/principals/alice/memberships":
                return self._send(200, {"memberships": [{"workspace_id": "team", "role": "owner"}]})
            return self._send(404, {"detail": "no"})

        def do_PUT(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(("PUT", self.path, body))
            if "visibility" in body:
                state["visibility"] = body["visibility"]
            return self._send(200, {})

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = AdminApiPolicyStore(f"http://127.0.0.1:{srv.server_address[1]}", "s3cret", ttl=0)
        assert port.has(None, "team") and not port.has(None, "ghost")
        assert port.get(None, "team", "members")[0]["subject"] == "alice"
        assert port.get(None, "team", "visibility") == {"visibility": "open"}
        port.put(None, "team", "visibility", {"visibility": "closed", "at": "t"})
        assert ("PUT", "/internal/registry/workspaces/team", {"visibility": "closed"}) in seen
        assert port.member_workspaces(None, "alice") == [{"workspace_id": "team", "role": "owner"}]
        assert port.invite_by_hash(None, "nohash") is None
        assert all(s[2] == "s3cret" for s in seen if s[0] == "GET")
    finally:
        srv.shutdown()


def test_decide_door_over_http(world):
    client, store, calls, spawned, root, _d = world
    from tests.test_r1_rooms import SECRET, _h
    body = {"principal": "alice", "action": "policy.change", "workspace": "team"}
    assert client.post("/internal/policy/decide", json=body, headers=_h("alice")).status_code == 403
    r = client.post("/internal/policy/decide", json=body, headers={"x-internal-secret": SECRET})
    assert r.status_code == 200 and r.json()["decision"] == "deny"           # delegated by default
    r = client.post("/internal/policy/decide", json={**body, "credential": "session"},
                    headers={"x-internal-secret": SECRET})
    assert r.json()["decision"] == "allow" and r.json()["rule"].startswith("interim.owner-session")
    r = client.post("/internal/policy/decide", json={**body, "principal": "bob", "credential": "session"},
                    headers={"x-internal-secret": SECRET})
    assert r.json()["decision"] == "deny"


# ── agents change policy WITH PERMISSION: propose → owner approves from a session (D13) → apply ─

def _agent(who):
    """``who``'s own chat worker: delegated, a person watching (human regime)."""
    return {"X-User-Id": who, "x-user-regime": "human", "x-user-delegation-workspaces": "team",
            "x-user-delegation-target": "team"}


def _chat(store, owner):
    return store.create(owner, title="chat")["id"]


def _propose(client, store, who, change, value, reason="because"):
    sid = _chat(store, who)
    return client.post("/api/workspace/policy/proposals", headers=_agent(who),
                       json={"workspace": "team", "session": sid, "change": change, "value": value,
                             "reason": reason})


def test_an_agent_proposal_changes_nothing_until_the_owner_approves(world):
    client, store, calls, spawned, root, _d = world
    from tests.test_r1_rooms import _h
    r = _propose(client, store, "bob", "visibility", "open", "the team wants other rooms to read it")
    assert r.status_code == 201, r.text
    pid = r.json()["proposal"]["id"]
    assert r.json()["decides"] == "alice" and rp.visibility(root, "team") == "closed"
    waiting = client.get("/api/workspace/policy/proposals", headers=_h("alice")).json()["proposals"]
    assert [p["id"] for p in waiting] == [pid]
    r = client.post(f"/api/workspace/policy/proposals/{pid}/approve", headers=_h("alice"))
    assert r.status_code == 200, r.text
    assert rp.visibility(root, "team") == "open" and r.json()["proposal"]["status"] == "accepted"
    log = subprocess.run(["git", "-C", str(root / "team"), "log", "-1", "--format=%an|%B"],
                         capture_output=True, text=True, check=True).stdout
    assert log.startswith("alice|") and f"Approved-by: alice (proposal {pid})" in log
    assert "On-behalf-of: agent:bob" in log


def test_a_member_role_change_is_applied_to_the_store_on_approval(world):
    client, store, calls, spawned, root, _d = world
    from tests.test_r1_rooms import _h
    pid = _propose(client, store, "alice", "member_role", {"subject": "bob", "role": "owner"}).json()["proposal"]["id"]
    assert m.is_member(root, "team", "bob") == "contributor"
    assert client.post(f"/api/workspace/policy/proposals/{pid}/approve", headers=_h("alice")).status_code == 200
    assert m.is_member(root, "team", "bob") == "owner"


def test_rules_land_in_git_with_the_trailers(world):
    client, store, calls, spawned, root, _d = world
    from tests.test_r1_rooms import _h
    rules = [{"id": "ws.no-keys", "effect": "deny", "action": "crm.read", "credential": ["key"]}]
    pid = _propose(client, store, "bob", "rules", rules).json()["proposal"]["id"]
    assert m.read_rules(root, "team") == []
    client.post(f"/api/workspace/policy/proposals/{pid}/approve", headers=_h("alice"))
    assert m.read_rules(root, "team") == rules


def test_a_decline_changes_nothing(world):
    client, store, calls, spawned, root, _d = world
    from tests.test_r1_rooms import _h
    pid = _propose(client, store, "bob", "visibility", "open").json()["proposal"]["id"]
    r = client.post(f"/api/workspace/policy/proposals/{pid}/decline", headers=_h("alice"))
    assert r.status_code == 200 and r.json()["proposal"]["status"] == "rejected"
    assert rp.visibility(root, "team") == "closed"
    assert client.post(f"/api/workspace/policy/proposals/{pid}/approve", headers=_h("alice")).status_code == 409


def test_approval_by_a_non_owner_or_a_non_session_credential_is_refused(world):
    client, store, calls, spawned, root, _d = world
    from tests.test_r1_rooms import _h, _key
    pid = _propose(client, store, "bob", "visibility", "open").json()["proposal"]["id"]
    # not the owner (bob proposed it, and bob is a contributor)
    assert client.post(f"/api/workspace/policy/proposals/{pid}/approve", headers=_h("bob")).status_code == 404
    # the owner's own agent, watched
    assert client.post(f"/api/workspace/policy/proposals/{pid}/approve", headers=_agent("alice")).status_code == 403
    # the owner's API key without the browser scope
    assert client.post(f"/api/workspace/policy/proposals/{pid}/approve",
                       headers=_key("alice", "bot,tx")).status_code == 403
    # the owner's API key WITH the browser scope — still a key (D13), still refused
    assert client.post(f"/api/workspace/policy/proposals/{pid}/approve",
                       headers=_key("alice")).status_code == 403
    # the owner, naming no credential class at all (an older identity) — not a session
    assert client.post(f"/api/workspace/policy/proposals/{pid}/approve",
                       headers={"X-User-Id": "alice", "x-user-scopes": "browser"}).status_code == 403
    assert rp.visibility(root, "team") == "closed"
    # the owner, from a session credential
    assert client.post(f"/api/workspace/policy/proposals/{pid}/approve",
                       headers=_h("alice")).status_code == 200
    assert rp.visibility(root, "team") == "open"


def test_a_direct_agent_write_is_still_refused(world):
    client, store, calls, spawned, root, _d = world
    r = client.put("/api/workspace/file", headers=_agent("alice"),
                   json={"path": "policy/visibility.json", "content": '{"visibility":"open"}', "slug": "team"})
    assert r.status_code == 403
    r = client.put("/api/workspace-links/team/visibility", headers=_agent("alice"), json={"visibility": "open"})
    assert r.status_code == 403
    assert rp.visibility(root, "team") == "closed"


def test_a_bad_proposal_is_refused_and_a_reader_cannot_propose(world):
    client, store, calls, spawned, root, _d = world
    assert _propose(client, store, "bob", "visibility", "public").status_code == 422
    assert _propose(client, store, "bob", "make_me_god", None).status_code == 422
    m.grant_membership(root, "team", "vic", "viewer", added_by="alice", index=m.InMemoryMembershipIndex())
    assert _propose(client, store, "vic", "visibility", "open").status_code == 403


def test_the_http_port_remembers_an_absent_workspace_until_this_port_writes_it(monkeypatch):
    """perf (app.dev 2026-10-17): every enumeration asks about every directory on the host, and most
    are people's desks the registry has never heard of. An absence is cached for `absent_ttl` (not
    the 2 s of a present document), and a `put` through this port — the only way a workspace enters
    the registry — drops it, so the next read sees the new workspace."""
    from shared import adapters
    from shared.adapters import AdminApiPolicyStore
    port = AdminApiPolicyStore("http://registry.invalid", "s3cret", ttl=2.0, absent_ttl=30.0)
    present = {"team": {"workspace": {}, "members": [{"subject": "alice", "role": "owner"}]}}
    gets = []

    def fake_req(method, path, body=None):
        if method == "GET":
            gets.append(path)
            return present.get(path.rsplit("/", 1)[-1])
        present[path.rsplit("/", 1)[-1]] = {"workspace": {}, "members": list((body or {}).get("members") or [])}
        return {}
    monkeypatch.setattr(port, "_req", fake_req)
    clock = [1000.0]
    monkeypatch.setattr(adapters.time, "monotonic", lambda: clock[0])

    assert not port.has(None, "desk-7") and not port.has(None, "desk-7")
    clock[0] += 10                                       # past the present-doc TTL, inside the absent one
    assert not port.has(None, "desk-7") and gets.count("/workspaces/desk-7") == 1
    assert port.has(None, "team")
    clock[0] += 3
    assert port.has(None, "team") and gets.count("/workspaces/team") == 2   # a present doc keeps 2 s
    port.put(None, "desk-7", "members", [{"subject": "bob", "role": "owner"}])
    assert port.has(None, "desk-7") and gets.count("/workspaces/desk-7") == 2  # the write dropped the absence


# ── Low (gate 0.13.4 final, car 43): the registry is read on every access decision ──────────────

def _registry_port(monkeypatch, **kw):
    from shared import adapters
    from shared.adapters import AdminApiPolicyStore
    port = AdminApiPolicyStore("http://registry.invalid", "s3cret", **kw)
    docs = {"team": {"workspace": {}, "members": [{"subject": "alice", "role": "owner"},
                                                  {"subject": "bob", "role": "contributor"}]}}
    gets: list = []

    def fake_req(method, path, body=None):
        if method == "GET":
            gets.append(path)
            return docs.get(path.rsplit("/", 1)[-1])
        return {}
    monkeypatch.setattr(port, "_req", fake_req)
    clock = [1000.0]
    monkeypatch.setattr(adapters.time, "monotonic", lambda: clock[0])
    return port, docs, gets, clock


def test_a_member_removed_by_another_writer_is_refused_at_once(tmp_path, monkeypatch):
    """Another replica (or admin-api's own account merge) removes bob: this process's next access
    decision reads the registry, not a copy it kept."""
    port, docs, gets, clock = _registry_port(monkeypatch)
    m.set_policy_store(port)
    try:
        _ws(tmp_path, "team")
        assert m.is_member(tmp_path, "team", "bob") == "contributor"
        docs["team"]["members"] = [{"subject": "alice", "role": "owner"}]       # written elsewhere
        clock[0] += 0.5
        assert m.is_member(tmp_path, "team", "bob") is None
    finally:
        m.set_policy_store(None)


def test_one_read_scope_asks_the_registry_once_and_sees_its_own_write(tmp_path, monkeypatch):
    """Control (perf): inside a request's read scope repeated decisions are one GET; a write in the
    scope is read back."""
    port, docs, gets, clock = _registry_port(monkeypatch)
    m.set_policy_store(port)
    try:
        _ws(tmp_path, "team")
        with m.read_scope():
            assert m.is_member(tmp_path, "team", "bob") == "contributor"
            first = gets.count("/workspaces/team")
            assert m.is_member(tmp_path, "team", "alice") == "owner"
            assert m.is_member(tmp_path, "team", "carol") is None
            assert gets.count("/workspaces/team") == first                  # answered by the scope
            m.store_put(tmp_path, "team", "visibility", {"visibility": "open"})
            m.is_member(tmp_path, "team", "bob")
            assert gets.count("/workspaces/team") == first + 1                # its own write read back
    finally:
        m.set_policy_store(None)
