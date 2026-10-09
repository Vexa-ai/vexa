"""The process backend under a ROOT runtime (Lite's shape), run for real: no child is root.

Skipped unless the suite runs as root on Linux — run it in a container, e.g. the runtime image:

    docker run --rm -v "$PWD/core/runtime:/src" -w /src -e PYTHONPATH=/src/src:<pytest> \\
        <runtime image> python -m pytest -q tests/test_isolation_root.py

Every case spawns real children through ``ProcessBackend.start`` and reads what the kernel says
about them (``/proc/self/status``, ``getresuid``), and plants the links a tenant could plant to
prove root never follows them.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time

import pytest

from runtime_kernel import isolation as iso
from runtime_kernel.isolation import SUBJECT_UID_BASE, UID_BASE, WORKLOAD_UID_BASE, IsolationRefused
from runtime_kernel.process_backend import ProcessBackend
from runtime_kernel.profiles import CredentialFile, Runnable

pytestmark = pytest.mark.skipif(os.geteuid() != 0 or not sys.platform.startswith("linux"),
                                reason="needs a root runtime on Linux")

REPORT = """
import json, os
status = dict(l.split(':', 1) for l in open('/proc/self/status').read().splitlines() if ':' in l)
cred = os.path.join(os.environ['HOME'], '.claude', '.credentials.json')
print('REPORT ' + json.dumps({
    'uids': os.getresuid(), 'gids': os.getresgid(), 'groups': os.getgroups(),
    'home': os.environ['HOME'], 'tmp': os.environ['TMPDIR'], 'cwd_home_listing': sorted(os.listdir(os.environ['HOME'])),
    'nnp': status['NoNewPrivs'].strip(), 'cred': open(cred).read() if os.path.exists(cred) else None,
}), flush=True)
"""


def _cmd(code: str) -> list[str]:
    return [sys.executable, "-c", code]


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """A root-owned store, HOME root and log dir under /tmp (sticky, root-owned), on a path the
    children can traverse (pytest makes its own base directories 0700)."""
    for d in (tmp_path, tmp_path.parent, tmp_path.parent.parent):
        os.chmod(d, 0o755)
    store = tmp_path / "store"
    for d in ("17", ".system/17", ".attached", "deal-9"):
        (store / d).mkdir(parents=True)
    logs = tmp_path / "logs"
    monkeypatch.setenv("PROCESS_LOG_DIR", str(logs))
    backend = ProcessBackend(homes_root=str(tmp_path / "homes"))
    return {"root": tmp_path, "store": store, "logs": logs, "backend": backend}


def _env(lab, subject="17", mounts=None):
    store = lab["store"]
    mounts = mounts if mounts is not None else [
        {"slug": "seed", "path": f"{store}/17", "role": "private", "write": True, "primary": True},
        {"slug": "_system", "path": f"{store}/.system/17", "role": "system", "write": True},
        {"slug": "deal-9", "path": f"{store}/deal-9", "role": "shared", "write": True},
    ]
    return {"VEXA_WORKSPACE_MOUNT_TARGET": str(store), "VEXA_OWNER": subject,
            "VEXA_MOUNTS": json.dumps(mounts)}


def _run(lab, workload_id: str, runnable: Runnable, env: dict) -> dict:
    h = lab["backend"].start(workload_id, runnable, env)
    h._impl.wait(timeout=30)
    text = (lab["logs"] / f"{workload_id}.log").read_text()
    lab["backend"].exit_code(h)                       # observes the exit: group reaped, HOME removed
    line = next(l for l in text.splitlines() if l.startswith("REPORT "))
    return json.loads(line[len("REPORT "):])


def test_a_workspace_dispatch_runs_as_its_subject_never_root(lab):
    r = _run(lab, "w-17", Runnable(command=_cmd(REPORT)), _env(lab))
    assert r["uids"] == [UID_BASE + 17] * 3 and r["gids"] == [UID_BASE + 17] * 3
    assert 0 not in r["groups"] and iso.GID_BASE in r["groups"]   # the shared workspace's group
    assert r["nnp"] == "1"
    assert r["tmp"] == os.path.join(r["home"], "tmp") and r["cwd_home_listing"] == ["tmp"]
    assert not os.path.exists(r["home"])                          # removed with the workload
    st = os.stat(lab["store"] / "17")
    assert (st.st_uid, stat.S_IMODE(st.st_mode)) == (UID_BASE + 17, 0o700)


def test_a_non_numeric_subject_runs_as_its_registry_uid(lab):
    env = _env(lab, subject="alice", mounts=[])
    first = _run(lab, "w-alice-1", Runnable(command=_cmd(REPORT)), env)
    second = _run(lab, "w-alice-2", Runnable(command=_cmd(REPORT)), env)
    assert first["uids"] == second["uids"] == [SUBJECT_UID_BASE] * 3
    assert first["home"] != second["home"]                        # a fresh HOME every time


def test_a_workload_with_no_subject_runs_as_its_own_uid_with_only_its_groups(lab):
    bot = Runnable(command=_cmd(REPORT), process_groups=("nogroup", "no-such-group"))
    r = _run(lab, "bot-1", bot, {})
    assert r["uids"][0] >= WORKLOAD_UID_BASE and len(set(r["uids"])) == 1
    assert r["groups"] == [65534]                                 # nogroup, and nothing else


def test_the_profiles_credential_files_land_in_the_childs_own_home(lab):
    cred = lab["root"] / "cred.json"
    cred.write_text('{"t": 1}')
    os.chmod(cred, 0o600)
    runnable = Runnable(command=_cmd(REPORT), credential_files=(
        CredentialFile(source=str(cred), target="/unused", home_path=".claude/.credentials.json"),))
    r = _run(lab, "w-cred", runnable, _env(lab))
    assert r["cred"] == '{"t": 1}'


def test_concurrent_children_cannot_read_each_other_or_the_runtime(lab):
    """Two bots: different uids, and neither can read the other's environment or the runtime's."""
    sleeper = Runnable(command=_cmd("import os, time; print('PID', os.getpid(), flush=True); time.sleep(30)"))
    a = lab["backend"].start("bot-a", sleeper, {"SECRET_OF_A": "a-secret"})
    try:
        a_log = lab["logs"] / "bot-a.log"
        for _ in range(200):
            if a_log.exists() and "PID" in a_log.read_text():
                break
            time.sleep(0.05)
        a_pid = int(a_log.read_text().split("PID", 1)[1].split()[0])
        probe = f"""
import json, os
out = {{}}
for name, pid in (("bot", {a_pid}), ("runtime", {os.getpid()})):
    try:
        open(f"/proc/{{pid}}/environ", "rb").read(); out[name] = "READ"
    except PermissionError:
        out[name] = "denied"
print("REPORT " + json.dumps({{"probe": out, "uid": os.getuid()}}), flush=True)
"""
        b = lab["backend"].start("bot-b", Runnable(command=_cmd(probe)), {})
        b._impl.wait(timeout=30)
        report = json.loads((lab["logs"] / "bot-b.log").read_text().split("REPORT ", 1)[1])
        a_uid = os.stat(f"/proc/{a_pid}").st_uid
        assert report["uid"] != a_uid and 0 not in (report["uid"], a_uid)
        assert report["probe"] == {"bot": "denied", "runtime": "denied"}
    finally:
        lab["backend"].cleanup(a)


def test_a_child_that_cannot_be_isolated_is_refused_and_never_started(lab, monkeypatch):
    os.chown(lab["store"], 1234, 1234)                           # a store root root does not own
    spawned = []
    real = subprocess.Popen
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: spawned.append(a) or real(*a, **k))
    with pytest.raises(IsolationRefused):
        lab["backend"].start("w-refused", Runnable(command=_cmd("print('ran')")), _env(lab))
    with pytest.raises(IsolationRefused):
        lab["backend"].start("w-bad-subject", Runnable(command=_cmd("print('ran')")),
                             _env(lab, subject="../17"))
    assert spawned == []


# ── R4-3: links a tenant planted are never followed by root ───────────────────

def _victim(lab, name: str):
    """A root-owned file and directory outside the store, standing in for /etc."""
    d = lab["root"] / f"victim-{name}"
    d.mkdir()
    (d / "f").write_text("root's")
    os.chmod(d, 0o755)
    return d


def _owner(path):
    st = os.lstat(path)
    return st.st_uid, st.st_gid, stat.S_IMODE(st.st_mode)


def test_links_inside_a_tenant_tree_are_never_followed_when_it_is_re_owned(lab):
    victim = _victim(lab, "tree")
    tree = lab["store"] / "17"
    (tree / "dirlink").symlink_to(victim)
    (tree / "filelink").symlink_to(victim / "f")
    os.link(victim / "f", tree / "hard")
    (tree / "own").write_text("x")
    os.chmod(tree, 0o755)                                        # the tenant asks for a re-own
    before = {p: _owner(p) for p in (victim, victim / "f")}
    _run(lab, "w-tree", Runnable(command=_cmd(REPORT)), _env(lab))
    assert {p: _owner(p) for p in before} == before
    assert os.stat(tree / "own").st_uid == UID_BASE + 17


@pytest.mark.parametrize("planted", [".attached/17", ".system/17", "17"])
def test_a_tenant_dir_replaced_by_a_link_refuses_the_dispatch(lab, planted):
    victim = _victim(lab, planted.replace("/", "-"))
    path = lab["store"] / planted
    if path.is_dir() and not path.is_symlink():
        path.rmdir()
    path.symlink_to(victim)
    before = {p: _owner(p) for p in (victim, victim / "f")}
    with pytest.raises(IsolationRefused):
        lab["backend"].start("w-link", Runnable(command=_cmd(REPORT)), _env(lab))
    assert {p: _owner(p) for p in before} == before


def test_a_log_path_replaced_by_a_link_is_not_written_through(lab):
    victim = _victim(lab, "log")
    lab["logs"].mkdir(mode=0o700)
    (lab["logs"] / "w-log.log").symlink_to(victim / "f")
    h = lab["backend"].start("w-log", Runnable(command=_cmd("print('output')")), _env(lab))
    h._impl.wait(timeout=30)
    lab["backend"].cleanup(h)
    assert (victim / "f").read_text() == "root's"


def test_logs_are_root_only(lab):
    h = lab["backend"].start("w-perm", Runnable(command=_cmd("print('x')")), {})
    h._impl.wait(timeout=30)
    lab["backend"].cleanup(h)
    assert stat.S_IMODE(os.stat(lab["logs"]).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(lab["logs"] / "w-perm.log").st_mode) == 0o600
