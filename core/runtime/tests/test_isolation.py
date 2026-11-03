"""L2: the POSIX identity of every child of the PROCESS backend (lite) — runtime_kernel.isolation.

The plan is PURE (env → uid/dir plan) and fully offline-testable. The filesystem effects run for real
against tmp_path with the test's own uid standing in for root (``owner``): links are planted where a
tenant could plant them, and the tests prove root's operations never follow them. What only root can
do (switching to another uid) is proven in ``test_isolation_root.py``, which runs as root.
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from runtime_kernel import isolation as iso
from runtime_kernel.isolation import (
    GID_BASE, NUMERIC_SUBJECT_LIMIT, SUBJECT_UID_BASE, UID_BASE, ChildIdentity, IsolationRefused,
    ProcessIsolation, StagedFile, WorkloadUids, apply_process_isolation, chown_tree, make_home,
    open_trusted_dir, plan_process_isolation, preexec_for, remove_home, sweep_homes,
)

ME = os.getuid()


def _env(mounts, *, root="/workspaces", subject="17"):
    return {"VEXA_WORKSPACE_MOUNT_TARGET": root, "VEXA_OWNER": subject,
            "VEXA_MOUNTS": json.dumps(mounts)}


MOUNTS = [
    {"slug": "_global", "source": "/srv/g", "path": "/workspaces/_global", "role": "global", "write": False},
    {"slug": "seed", "path": "/workspaces/17", "role": "private", "write": True, "primary": True},
    {"slug": "_system", "path": "/workspaces/.system/17", "role": "system", "write": True},
    {"slug": "deal-9", "path": "/workspaces/deal-9", "role": "shared", "write": True},
]


@pytest.fixture
def store(tmp_path):
    """A store root the test owns, with real (symlink-free) ancestors."""
    root = tmp_path / "store"
    root.mkdir()
    os.chmod(root, 0o755)          # what the store root is in production, whatever the host's umask
    return root.resolve()


def _mode(path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


# ── the plan (pure) ───────────────────────────────────────────────────────────

def test_plan_maps_subject_to_uid_and_buckets_the_mounts():
    plan = plan_process_isolation(_env(MOUNTS), euid=0)
    assert plan is not None
    assert plan.uid == UID_BASE + 17 and plan.gid == plan.uid
    assert plan.private == ("/workspaces/17", "/workspaces/.system/17")   # private + system tiers
    assert plan.shared == (("/workspaces/deal-9", "deal-9"),)             # per-workspace group


@pytest.mark.parametrize("subject", [
    "017",                     # another spelling of 17 — must not share 17's uid
    "١٧",            # Arabic-Indic "17": str.isdigit() says yes, int() says 17
    "１７",            # full-width "17"
    str(NUMERIC_SUBJECT_LIMIT),  # an arithmetic uid here would be a shared-workspace gid
    "alice@example.com",
    "routine:nightly",
])
def test_a_subject_that_is_not_a_small_canonical_number_maps_through_the_registry(subject):
    if not iso._SUBJECT.fullmatch(subject):
        with pytest.raises(IsolationRefused):
            plan_process_isolation(_env(MOUNTS, subject=subject), euid=0)
        return
    plan = plan_process_isolation(_env(MOUNTS, subject=subject), euid=0)
    assert plan is not None and plan.uid is None and plan.subject == subject


def test_numeric_uids_stay_below_the_shared_workspace_gids():
    assert iso.numeric_uid(str(NUMERIC_SUBJECT_LIMIT - 1)) == GID_BASE - 1
    assert iso.numeric_uid(str(NUMERIC_SUBJECT_LIMIT)) is None


@pytest.mark.parametrize("subject", ["", "  ", "../17", "a/b", ".hidden", "x" * 300, "a\x00b", "a b", "-r"])
def test_a_root_runtime_refuses_a_subject_it_cannot_map(subject):
    """Under a root runtime a workspace dispatch whose subject cannot be mapped is REFUSED — never
    run as root, never degraded to shared trust."""
    with pytest.raises(IsolationRefused):
        plan_process_isolation(_env(MOUNTS, subject=subject), euid=0)


def test_a_root_runtime_refuses_workspaces_without_a_store_root():
    env = {"VEXA_OWNER": "17", "VEXA_MOUNTS": json.dumps(MOUNTS)}
    with pytest.raises(IsolationRefused):
        plan_process_isolation(env, euid=0)


def test_a_non_root_runtime_says_the_wall_is_unavailable(caplog):
    with caplog.at_level("WARNING"):
        assert plan_process_isolation(_env(MOUNTS), euid=501) is None
    assert "not root" in caplog.text


def test_plan_none_for_workspaceless_workloads(caplog):
    """Meeting bots carry no workspace env — planned as a workload of their own, no warning."""
    with caplog.at_level("WARNING"):
        assert plan_process_isolation({}, euid=0) is None
        assert plan_process_isolation({}, euid=501) is None
    assert not caplog.records


# ── the registries (real file I/O) ────────────────────────────────────────────

def test_ids_are_allocated_once_and_persisted(store):
    fd = open_trusted_dir(str(store), owner=ME)
    try:
        g1 = iso._allocate(fd, iso.GID_REGISTRY, "deal-9", GID_BASE, iso.GID_LIMIT, owner=ME)
        g2 = iso._allocate(fd, iso.GID_REGISTRY, "acme-1424e3", GID_BASE, iso.GID_LIMIT, owner=ME)
        u1 = iso._allocate(fd, iso.UID_REGISTRY, "alice", SUBJECT_UID_BASE, iso.SUBJECT_UID_LIMIT, owner=ME)
        assert (g1, g2, u1) == (GID_BASE, GID_BASE + 1, SUBJECT_UID_BASE)
        assert iso._allocate(fd, iso.GID_REGISTRY, "deal-9", GID_BASE, iso.GID_LIMIT, owner=ME) == g1
    finally:
        os.close(fd)
    assert json.loads((store / iso.GID_REGISTRY).read_text()) == {"deal-9": g1, "acme-1424e3": g2}
    assert _mode(store / iso.GID_REGISTRY) == 0o600


def test_a_registry_replaced_by_a_link_is_not_read_or_written_through(store, tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text('{"deal-9": 0}')
    (store / iso.GID_REGISTRY).symlink_to(outside)
    fd = open_trusted_dir(str(store), owner=ME)
    try:
        with pytest.raises(IsolationRefused):
            iso._allocate(fd, iso.GID_REGISTRY, "deal-9", GID_BASE, iso.GID_LIMIT, owner=ME)
    finally:
        os.close(fd)
    assert outside.read_text() == '{"deal-9": 0}'


@pytest.mark.parametrize("content,mode,hardlink", [
    ('{"deal-9": 200000}', 0o644, False),                # readable by others
    ('{"deal-9": 200000}', 0o600, True),                 # a second link to it exists
    ('{"deal-9": 0}', 0o600, False),                      # an id outside the range: root's gid
    ('{"deal-9": 1000000000}', 0o600, False),             # an id in the subject range
    ('{"deal-9": "200000"}', 0o600, False),               # not an int
    ('{"a": 200000, "b": 200000}', 0o600, False),         # one id for two names
    ('{"../x": 200001}', 0o600, False),                   # not a plain name
    ('[1, 2]', 0o600, False),
])
def test_a_registry_that_is_not_roots_private_file_or_holds_a_bad_id_refuses(store, tmp_path, content,
                                                                             mode, hardlink):
    reg = store / iso.GID_REGISTRY
    reg.write_text(content)
    os.chmod(reg, mode)
    if hardlink:
        os.link(reg, tmp_path / "second-link")
    fd = open_trusted_dir(str(store), owner=ME)
    try:
        with pytest.raises(IsolationRefused):
            iso._allocate(fd, iso.GID_REGISTRY, "deal-9", GID_BASE, iso.GID_LIMIT, owner=ME)
    finally:
        os.close(fd)


def test_a_registry_of_another_owner_refuses(store):
    reg = store / iso.UID_REGISTRY
    reg.write_text("{}")
    os.chmod(reg, 0o600)
    other = 1234 if ME == 0 else 0                # whoever the store's owner is not
    if ME == 0:
        os.chown(reg, other, other)
    fd = open_trusted_dir(str(store), owner=ME)
    try:
        with pytest.raises(IsolationRefused):
            iso._allocate(fd, iso.UID_REGISTRY, "alice", SUBJECT_UID_BASE, iso.SUBJECT_UID_LIMIT,
                          owner=ME if ME == 0 else other)
    finally:
        os.close(fd)


# ── the store (R4-3: root never follows a link a tenant planted) ──────────────

def _plan(store, *, subject="17", uid=None, private=(), shared=()):
    return ProcessIsolation(subject=subject, store_root=str(store), uid=ME if uid is None else uid,
                            private=tuple(str(store / p) for p in private),
                            shared=tuple((str(store / p), s) for p, s in shared))


def test_a_store_root_others_can_write_is_refused_not_fixed(store):
    os.chmod(store, 0o777)                               # the old entrypoint's chmod
    with pytest.raises(IsolationRefused):
        apply_process_isolation(_plan(store), owner=ME)
    assert _mode(store) == 0o777                         # refused, left for the deployment to set


@pytest.mark.parametrize("tier", [".attached", ".system"])
def test_a_tier_others_can_write_is_refused(store, tier):
    (store / tier).mkdir()
    os.chmod(store / tier, 0o777)
    with pytest.raises(IsolationRefused):
        apply_process_isolation(_plan(store), owner=ME)


def test_apply_seals_the_store_and_its_tenants(store):
    for d in ("17", "9", ".system/9", ".attached/9", "deal-9", "_global"):
        (store / d).mkdir(parents=True)
    os.chmod(store, 0o755)
    for d in ("17", "9", ".system/9", ".attached/9", "_global", ".attached", ".system"):
        os.chmod(store / d, 0o755)
    out = apply_process_isolation(_plan(store, private=("17",)), owner=ME)
    assert out.uid == ME
    assert _mode(store) == 0o755                          # writable by its owner only
    assert _mode(store / ".attached") == _mode(store / ".system") == 0o711
    assert _mode(store / "17") == 0o700                  # the subject's own, re-owned
    for d in ("9", ".system/9", ".attached/9"):
        assert _mode(store / d) == 0o700                 # never-dispatched tenants: sealed
    assert _mode(store / "_global") == 0o755             # special dir untouched by the sweep


def test_a_registry_subject_gets_its_uid_from_the_store(store, monkeypatch):
    plan = plan_process_isolation(_env([], root=str(store), subject="alice"), euid=0)
    out = apply_process_isolation(plan, owner=ME)
    again = apply_process_isolation(plan, owner=ME)
    other = apply_process_isolation(replace_subject(plan, "bob"), owner=ME)
    assert out.uid == again.uid == SUBJECT_UID_BASE and other.uid == SUBJECT_UID_BASE + 1


def replace_subject(plan, subject):
    from dataclasses import replace
    return replace(plan, subject=subject)


@pytest.mark.parametrize("planted", [".attached", ".system", "17", ".attached/17"])
def test_a_link_in_place_of_a_store_directory_refuses_the_dispatch(store, tmp_path, planted):
    """A tier or a tenant dir that is a link (planted while the store was world-writable) is never
    followed: the dispatch is refused and the link's target is untouched."""
    target = tmp_path / "target"
    target.mkdir()
    os.chmod(target, 0o755)
    (store / ".attached").mkdir()
    link = store / planted
    if link.exists():
        link.rmdir()
    link.symlink_to(target)
    with pytest.raises(IsolationRefused):
        apply_process_isolation(_plan(store, private=("17",)), owner=ME)
    assert _mode(target) == 0o755


def test_a_store_below_a_directory_others_can_write_is_refused(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    os.chmod(shared, 0o777)
    (shared / "store").mkdir()
    with pytest.raises(IsolationRefused):
        apply_process_isolation(_plan((shared / "store").resolve()), owner=ME)


def _other_gid(path) -> int:
    current = os.stat(path).st_gid
    for g in os.getgroups():
        if g != current:
            return g
    pytest.skip("the test user is in only one group")


def test_chown_tree_never_follows_a_link_or_a_hard_link(tmp_path):
    """The tenant's tree is re-owned (here: to another group of ours, which a non-root test can do)
    except through links: a link to a directory, a link to a file and a hard link to a file outside
    keep pointing at untouched targets."""
    outside_dir, outside_file = tmp_path / "outside", tmp_path / "secret"
    outside_dir.mkdir()
    (outside_dir / "inner").write_text("x")
    outside_file.write_text("x")
    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "own.txt").write_text("mine")
    (tree / "dirlink").symlink_to(outside_dir)
    (tree / "sub" / "filelink").symlink_to(outside_file)
    os.link(outside_file, tree / "hard")
    gid = _other_gid(tree)
    before = {p: os.stat(p).st_gid for p in (outside_dir, outside_dir / "inner", outside_file)}
    fd = os.open(tree, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        chown_tree(fd, ME, gid)
    finally:
        os.close(fd)
    assert {p: os.stat(p).st_gid for p in before} == before       # nothing outside changed
    assert os.stat(tree).st_gid == os.stat(tree / "sub").st_gid == gid
    assert os.stat(tree / "sub" / "own.txt").st_gid == gid


# ── private HOMEs ─────────────────────────────────────────────────────────────

def test_every_child_gets_a_fresh_private_home(tmp_path):
    homes = tmp_path / "homes"
    cred = tmp_path / "cred.json"
    cred.write_text('{"token": "t"}')
    staged = [StagedFile(str(cred), ".claude/.credentials.json"),
              StagedFile(str(tmp_path / "absent.json"), ".codex/auth.json")]   # absent: skipped
    home1, tmp1 = make_home(ME, os.getgid(), homes_root=str(homes), owner=ME, staged=staged)
    home2, _ = make_home(ME, os.getgid(), homes_root=str(homes), owner=ME)
    assert home1 != home2 and os.path.dirname(home1) == str(homes.resolve())
    assert tmp1 == os.path.join(home1, "tmp") and os.path.isdir(tmp1)
    assert _mode(homes) == 0o711 and _mode(home1) == 0o700 and _mode(tmp1) == 0o700
    staged_file = os.path.join(home1, ".claude", ".credentials.json")
    assert open(staged_file).read() == '{"token": "t"}' and _mode(staged_file) == 0o400
    assert not os.path.exists(os.path.join(home1, ".codex"))
    assert os.listdir(home2) == ["tmp"]                   # nothing carried from another child


@pytest.mark.parametrize("bad", ["/etc/x", "../x", "a/../../x", ""])
def test_a_staged_path_must_stay_in_the_home(tmp_path, bad):
    cred = tmp_path / "c"
    cred.write_text("x")
    with pytest.raises(IsolationRefused):
        make_home(ME, os.getgid(), homes_root=str(tmp_path / "homes"), owner=ME,
                  staged=[StagedFile(str(cred), bad)])


def test_a_link_in_the_homes_path_is_refused_not_resolved(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "via-link").symlink_to(real)
    with pytest.raises(IsolationRefused):
        make_home(ME, os.getgid(), homes_root=str(tmp_path / "via-link" / "homes"), owner=ME)
    assert not (real / "homes").exists()


def test_no_new_privs_unavailable_refuses_the_spawn(monkeypatch):
    import ctypes

    def missing(*_a, **_k):
        raise OSError("no libc")
    monkeypatch.setattr(ctypes, "CDLL", missing)
    with pytest.raises(IsolationRefused):
        preexec_for(_identity())


def test_homes_below_a_directory_others_can_write_are_refused(tmp_path):
    open_dir = tmp_path / "open"
    open_dir.mkdir()
    os.chmod(open_dir, 0o777)
    with pytest.raises(IsolationRefused):
        make_home(ME, os.getgid(), homes_root=str(open_dir / "homes"), owner=ME)


def test_homes_are_removed_and_swept(tmp_path):
    homes = tmp_path / "homes"
    home, _ = make_home(ME, os.getgid(), homes_root=str(homes), owner=ME)
    (homes / "outside-marker").mkdir()
    remove_home(home, homes_root=str(homes), owner=ME)
    assert not os.path.exists(home)
    remove_home(str(tmp_path), homes_root=str(homes), owner=ME)     # not one of ours: left alone
    assert tmp_path.exists()
    live = make_home(ME, os.getgid(), homes_root=str(homes), owner=ME)[0]
    dead = homes / f"{ME + 1}.0123456789abcdef"       # a HOME whose uid no process runs as
    (dead / "tmp").mkdir(parents=True)
    assert sweep_homes(homes_root=str(homes), owner=ME, live_uids=lambda: {ME}) == 1
    assert os.path.exists(live) and not dead.exists()
    assert (homes / "outside-marker").exists()           # not a HOME name: kept


# ── per-workload uids ─────────────────────────────────────────────────────────

def test_workload_uids_are_unique_among_the_live():
    live = {iso.WORKLOAD_UID_BASE + 1}
    uids = WorkloadUids(base=iso.WORKLOAD_UID_BASE, slots=4, live_uids=lambda: live)
    a, b = uids.acquire("bot-a"), uids.acquire("bot-b")
    assert a == iso.WORKLOAD_UID_BASE and b == iso.WORKLOAD_UID_BASE + 2   # +1 is a live process
    assert uids.acquire("bot-a") == a
    uids.release("bot-a")
    assert uids.acquire("bot-c") == a
    uids.acquire("bot-d")
    with pytest.raises(IsolationRefused):
        uids.acquire("bot-e")


def test_group_ids_skip_unknown_groups_and_root():
    assert iso.group_ids(["no-such-group-vexa"]) == ()
    import grp
    root_group = grp.getgrgid(0).gr_name
    assert iso.group_ids([root_group]) == ()


# ── the drop ──────────────────────────────────────────────────────────────────

def _identity(**kw):
    base = dict(uid=100017, gid=100017, groups=(200000,), home="/h", tmp="/h/tmp")
    return ChildIdentity(**{**base, **kw})


def test_preexec_drops_groups_then_gid_then_uid_then_proves_it(monkeypatch):
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(os, "setgroups", lambda g: calls.append(("groups", tuple(g))))
    monkeypatch.setattr(os, "setgid", lambda g: calls.append(("gid", g)))
    monkeypatch.setattr(os, "setuid", lambda u: calls.append(("uid", u)))
    monkeypatch.setattr(os, "getresuid", lambda: (100017,) * 3, raising=False)
    monkeypatch.setattr(os, "getresgid", lambda: (100017,) * 3, raising=False)
    monkeypatch.setattr(os, "getgroups", lambda: [200000])
    monkeypatch.setattr(iso, "_no_new_privs", lambda: (lambda: calls.append(("nnp", 1))))
    monkeypatch.setattr(iso.userns, "refusal", lambda: (lambda: calls.append(("no userns", 1))))
    preexec_for(_identity())()
    assert calls == [("groups", (200000,)), ("gid", 100017), ("uid", 100017), ("nnp", 1), ("no userns", 1)]
    calls.clear()
    preexec_for(_identity(), user_namespaces=True)()       # a meeting bot keeps them (its sandbox)
    assert calls == [("groups", (200000,)), ("gid", 100017), ("uid", 100017), ("nnp", 1)]


def test_a_child_that_cannot_lose_user_namespaces_is_refused(monkeypatch):
    def unsupported():
        raise iso.userns.Unsupported(38, "no filter here")
    monkeypatch.setattr(iso, "_no_new_privs", lambda: (lambda: None))
    monkeypatch.setattr(iso.userns, "refusal", unsupported)
    with pytest.raises(IsolationRefused):
        preexec_for(_identity())
    preexec_for(_identity(), user_namespaces=True)          # nothing to take away: not refused


def test_preexec_fails_when_the_child_still_holds_root(monkeypatch):
    monkeypatch.setattr(iso, "_no_new_privs", lambda: (lambda: None))
    monkeypatch.setattr(iso.userns, "refusal", lambda: (lambda: None))
    for name in ("setgroups", "setgid", "setuid"):
        monkeypatch.setattr(os, name, lambda *_: None)
    monkeypatch.setattr(os, "getresuid", lambda: (100017, 100017, 0), raising=False)     # saved uid still root
    monkeypatch.setattr(os, "getresgid", lambda: (100017,) * 3, raising=False)
    monkeypatch.setattr(os, "getgroups", lambda: [])
    with pytest.raises(OSError):
        preexec_for(_identity())()


@pytest.mark.parametrize("kw", [{"uid": 0}, {"gid": 0}])
def test_no_identity_runs_a_child_as_root(kw):
    with pytest.raises(IsolationRefused):
        preexec_for(_identity(**kw))
