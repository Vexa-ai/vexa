"""WS-S3, second pass: writes outside git are durable before they return (background threads
included), and agent-api hydrates a workspace on first use and evicts least-recently-used ones."""
from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import fakeredis
import pytest

from shared.atomic_json import write_json_atomic
from workspaces.shared import gitexec
from workspaces.shared import workspace_paths as wpaths
from workspaces.store import boot as store_boot
from workspaces.store.lock import WorkspaceLocks
from workspaces.store.objects import MemoryObjects
from workspaces.store.observer import WorkspaceStoreError
from workspaces.store.repostore import STUB_FLAG, RepoStore

AUTHOR = ("-c", "user.name=t", "-c", "user.email=t@example.com")


def make_repo(path: Path, files: dict) -> None:
    path.mkdir(parents=True, exist_ok=True)
    gitexec.run_git(str(path), "init", "-q", "-b", "main", check=True)
    for rel, text in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text)
    gitexec.run_git(str(path), "add", "-A", check=True)
    gitexec.run_git(str(path), *AUTHOR, "commit", "-q", "-m", "init", check=True)


def seed(bucket, redis, tmp_path, repos: dict) -> None:
    pod = RepoStore(objects=bucket, locks=WorkspaceLocks(redis), cache_root=tmp_path / "seed")
    for rel, files in repos.items():
        make_repo(pod.path(rel), files)
        with pod.locks.hold(rel) as held:
            pod.publish(rel, held=held)


@pytest.fixture
def env():
    return {"VEXA_WORKSPACE_BACKEND": "s3", "VEXA_WORKSPACE_CACHE_MIN_IDLE_S": "0"}


@pytest.fixture
def bucket():
    return MemoryObjects(conditional=False)


@pytest.fixture
def redis():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def boot(tmp_path, bucket, redis, env):
    runs = []

    def start(**extra):
        run = store_boot.start(str(tmp_path / "cache"), redis, env={**env, **extra}, objects=bucket)
        runs.append(run)
        return run

    yield start
    for run in runs:
        run.stop()


def is_stub(path: Path) -> bool:
    return (path / STUB_FLAG).exists()


# ── gap 1: writes outside git are durable before they return ───────────────────────────────────
def test_a_background_platform_write_is_in_the_bucket_before_it_returns(tmp_path, bucket, redis, boot):
    run = boot()
    cache = tmp_path / "cache"
    done = {}

    def background():                      # no request, no middleware, no timer
        wpaths.write_text_inside(cache, ".attached/17/state.json", '{"active_set": ["17"]}')
        done["after_state"] = bucket.get("files/.attached/17/state.json")
        (cache / ".imports/17").mkdir(parents=True)
        write_json_atomic(cache / ".imports/17/op.json", {"status": "running"})
        done["after_import"] = bucket.get("files/.imports/17/op.json")

    t = threading.Thread(target=background)
    t.start()
    t.join()
    assert done["after_state"] == b'{"active_set": ["17"]}'
    assert json.loads(done["after_import"]) == {"status": "running"}
    wpaths.unlink_inside(cache, ".attached/17/state.json")
    assert bucket.get("files/.attached/17/state.json") is None, "a removal is durable too"
    assert run.sync.files.get(".imports/17/op.json")


def test_a_platform_write_the_bucket_refuses_fails_and_is_not_kept(tmp_path, bucket, redis, boot):
    boot()
    cache = tmp_path / "cache"
    wpaths.write_text_inside(cache, ".attached/17/state.json", "durable")
    bucket.down = True
    with pytest.raises(WorkspaceStoreError) as err:
        wpaths.write_text_inside(cache, ".attached/17/state.json", "lost")
    assert err.value.status == 503
    assert (cache / ".attached/17/state.json").read_text() == "durable"
    with pytest.raises(WorkspaceStoreError):
        wpaths.write_text_inside(cache, ".attached/18/new.json", "lost")
    assert not (cache / ".attached/18/new.json").exists()


def test_removing_a_workspace_tree_deletes_it_from_the_bucket_before_returning(tmp_path, bucket, redis, boot):
    seed(bucket, redis, tmp_path, {"deal": {"README.md": "deal"}, "17": {"README.md": "desk"}})
    run = boot()
    wpaths.remove_tree(tmp_path / "cache" / "deal")
    assert run.store.repos() == ["17"]


def test_a_by_name_writer_notifies_and_is_durable(tmp_path, bucket, redis, boot):
    boot()
    p = tmp_path / "cache" / ".routines-migrated" / "17.json"
    p.parent.mkdir(parents=True)
    p.write_text("{}")
    wpaths.notify_written(p)
    assert bucket.get("files/.routines-migrated/17.json") == b"{}"


# ── gap 3: hydrate on first use, LRU eviction ──────────────────────────────────────────────────
def test_boot_leaves_workspaces_as_stubs_and_first_use_hydrates(tmp_path, bucket, redis, boot):
    seed(bucket, redis, tmp_path, {"17": {"README.md": "desk", "kg/a.md": "A"},
                                   "_global": {"POLICIES.md": "p"}})
    run = boot()
    cache = tmp_path / "cache"
    assert not is_stub(cache / "_global"), "_global is read on every request: always real"
    assert is_stub(cache / "17"), "nothing else is loaded at boot"
    assert (cache / "17/kg/a.md").exists() and (cache / "17/kg/a.md").read_text() == ""
    assert wpaths.read_text_inside(cache / "17", "kg/a.md") == "A", "the cold miss hydrates"
    assert not is_stub(cache / "17")
    assert run.cache.hydrations == 1


def test_a_git_command_on_a_stub_hydrates_it_first(tmp_path, bucket, redis, boot):
    seed(bucket, redis, tmp_path, {"17": {"README.md": "desk"}})
    boot()
    sha = gitexec.run_git(str(tmp_path / "cache/17"), "rev-parse", "HEAD", check=True).stdout.strip()
    assert len(sha) == 40 and not is_stub(tmp_path / "cache/17")


def test_least_recently_used_workspaces_are_evicted_and_come_back_intact(tmp_path, bucket, redis, boot):
    seed(bucket, redis, tmp_path, {"a": {"f.md": "A" * 4000}, "b": {"f.md": "B" * 4000},
                                   "c": {"f.md": "C" * 4000}, "_global": {"POLICIES.md": "p"}})
    run = boot(VEXA_WORKSPACE_CACHE_MAX_BYTES="0")
    cache = tmp_path / "cache"
    for rel in ("a", "b", "c"):
        assert wpaths.read_text_inside(cache / rel, "f.md")[0] == rel.upper()
    run.cache.max_bytes = run.cache.sizes["a"] + run.cache.sizes["b"] + run.cache.sizes["_global"] + 1
    assert run.cache.maybe_evict() == 1
    assert is_stub(cache / "a") and not is_stub(cache / "b") and not is_stub(cache / "c"), "a was used longest ago"
    assert not is_stub(cache / "_global"), "pinned"
    assert wpaths.read_text_inside(cache / "a", "f.md") == "A" * 4000, "re-hydrated from the bucket"
    assert is_stub(cache / "b"), "bringing a back pushed out the next least recently used, b"
    assert run.cache.evictions == 2 and run.cache.hydrations == 4


def test_a_workspace_with_unpublished_changes_is_never_evicted(tmp_path, bucket, redis, boot):
    seed(bucket, redis, tmp_path, {"a": {"f.md": "A"}})
    run = boot()
    cache = tmp_path / "cache"
    wpaths.read_text_inside(cache / "a", "f.md")
    (cache / "a/draft.md").write_text("not committed yet")
    assert run.cache.evict("a") is False
    assert (cache / "a/draft.md").read_text() == "not committed yet"
    (cache / "a/draft.md").unlink()
    holding, release = threading.Event(), threading.Event()

    def writer():
        with run.store.locks.hold("a"):
            holding.set()
            release.wait(5)

    t = threading.Thread(target=writer)
    t.start()
    holding.wait(5)
    try:
        assert run.cache.evict("a") is False, "nor one whose lock another writer holds"
    finally:
        release.set()
        t.join()
    assert run.cache.evict("a") is True


def test_a_stub_moved_whole_is_hydrated_from_its_source_and_republished(tmp_path, bucket, redis, boot):
    seed(bucket, redis, tmp_path, {"deal": {"README.md": "deal"}})
    run = boot()
    cache = tmp_path / "cache"
    (cache / ".attached/17").mkdir(parents=True)
    shutil.move(str(cache / "deal"), str(cache / ".attached/17/deal"))   # a whole-tree move
    assert wpaths.read_text_inside(cache / ".attached/17/deal", "README.md") == "deal"
    run.sync.sync()
    assert run.store.repos() == [".attached/17/deal"]


def test_an_acknowledged_write_survives_eviction_and_a_dead_pod(tmp_path, bucket, redis, boot):
    seed(bucket, redis, tmp_path, {"17": {"README.md": "v1"}})
    run = boot()
    cache = tmp_path / "cache"
    wpaths.write_text_inside(cache / "17", "README.md", "acknowledged")
    gitexec.run_git(str(cache / "17"), "add", "-A", check=True)
    gitexec.run_git(str(cache / "17"), *AUTHOR, "commit", "-q", "-m", "ack", check=True)
    assert run.cache.evict("17")
    run.stop()
    shutil.rmtree(cache)
    run2 = boot()
    assert wpaths.read_text_inside(tmp_path / "cache/17", "README.md") == "acknowledged"
    assert run2.cache.hydrations == 1
