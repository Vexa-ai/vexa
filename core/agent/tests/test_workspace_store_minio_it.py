"""WS-S3 against a REAL S3 server (MinIO), not the in-memory double. Skipped unless pointed at one:

    VEXA_WS_S3_IT_ENDPOINT=http://127.0.0.1:<port> VEXA_WS_S3_IT_KEY=… VEXA_WS_S3_IT_SECRET=… \
    VEXA_WS_S3_IT_BUCKET=… pytest tests/test_workspace_store_minio_it.py

The server runs only on bbb, in a throwaway container with a unique name, removed afterwards
(report: ~/dev/biz/drafts/briefs-0133/ws-s3-report.md).
"""
from __future__ import annotations

import os
import shutil
import threading
import uuid
from pathlib import Path

import fakeredis
import pytest

from workspaces.shared import gitexec
from workspaces.store.lock import WorkspaceLocks
from workspaces.store.objects import S3Objects, S3Settings, self_test
from workspaces.store.observer import StoreObserver, WorkspaceStoreError
from workspaces.store.repostore import RepoStore, StaleBase

ENDPOINT = os.environ.get("VEXA_WS_S3_IT_ENDPOINT", "")
pytestmark = pytest.mark.skipif(not ENDPOINT, reason="no real S3 endpoint (VEXA_WS_S3_IT_ENDPOINT)")
AUTHOR = ("-c", "user.name=t", "-c", "user.email=t@example.com")


def settings(**kw) -> S3Settings:
    base = dict(bucket=os.environ.get("VEXA_WS_S3_IT_BUCKET", ""), endpoint=ENDPOINT,
                access_key=os.environ.get("VEXA_WS_S3_IT_KEY", ""),
                secret_key=os.environ.get("VEXA_WS_S3_IT_SECRET", ""),
                prefix=f"it-{uuid.uuid4().hex[:8]}/", connect_timeout=2, read_timeout=5)
    base.update(kw)
    return S3Settings(**base)


@pytest.fixture
def bucket():
    return S3Objects(settings())


@pytest.fixture
def redis():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture(autouse=True)
def _clear():
    yield
    gitexec.set_ref_observer(None)


def pod(tmp_path, bucket, redis, name, conditional=False):
    return RepoStore(objects=bucket, locks=WorkspaceLocks(redis), cache_root=tmp_path / name,
                     conditional=conditional)


def make_repo(path: Path, files: dict) -> None:
    path.mkdir(parents=True, exist_ok=True)
    gitexec.run_git(str(path), "init", "-q", "-b", "main", check=True)
    commit(path, files, "init")


def commit(path: Path, files: dict, msg: str) -> str:
    for rel, text in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text)
    gitexec.run_git(str(path), "add", "-A", check=True)
    gitexec.run_git(str(path), *AUTHOR, "commit", "-q", "-m", msg, check=True)
    return gitexec.run_git(str(path), "rev-parse", "HEAD", check=True).stdout.strip()


def publish(store, rel):
    with store.locks.hold(rel) as held:
        return store.publish(rel, held=held)


def test_the_self_test_passes_and_detects_conditional_put(bucket):
    probe = self_test(bucket, where=ENDPOINT)
    assert probe.ok, probe.error
    print("conditional_put:", probe.conditional_put, "versioning:", probe.versioning)


def test_round_trip_and_cold_pod_on_a_real_bucket(tmp_path, bucket, redis):
    a = pod(tmp_path, bucket, redis, "a", conditional=self_test(bucket).conditional_put)
    make_repo(a.path("17"), {"README.md": "v1"})
    publish(a, "17")
    gitexec.set_ref_observer(StoreObserver(a))
    acked = commit(a.path("17"), {"kg/x.md": "acknowledged"}, "ack")
    gitexec.set_ref_observer(None)
    shutil.rmtree(a.cache_root)                     # the pod dies
    b = pod(tmp_path, bucket, redis, "b")
    b.hydrate_all()
    assert gitexec.run_git(str(b.path("17")), "rev-parse", "HEAD", check=True).stdout.strip() == acked
    assert (b.path("17") / "kg/x.md").read_text() == "acknowledged"


def test_concurrent_writers_one_loses_cleanly_on_a_real_bucket(tmp_path, bucket, redis):
    a = pod(tmp_path, bucket, redis, "a")
    make_repo(a.path("ws"), {"f": "0"})
    publish(a, "ws")
    b = pod(tmp_path, bucket, redis, "b")
    b.hydrate("ws")
    commit(a.path("ws"), {"f": "a"}, "a")
    commit(b.path("ws"), {"f": "b"}, "b")
    out = {}

    def run(n, p):
        try:
            out[n] = publish(p, "ws").gen
        except StaleBase:
            out[n] = "stale"

    ts = [threading.Thread(target=run, args=x) for x in (("a", a), ("b", b))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(map(str, out.values())) == ["2", "stale"]


def test_an_unreachable_bucket_refuses_the_write_loudly(tmp_path, bucket, redis):
    a = pod(tmp_path, bucket, redis, "a")
    make_repo(a.path("17"), {"README.md": "durable"})
    durable = publish(a, "17").refs["refs/heads/main"]
    a.objects = S3Objects(settings(endpoint="http://127.0.0.1:9", prefix=bucket.settings.prefix))
    gitexec.set_ref_observer(StoreObserver(a))
    with pytest.raises(WorkspaceStoreError) as err:
        commit(a.path("17"), {"README.md": "lost"}, "x")
    assert err.value.status == 503
    assert gitexec.run_git(str(a.path("17")), "rev-parse", "HEAD", check=True).stdout.strip() == durable
    assert (a.path("17") / "README.md").read_text() == "durable"


def test_a_writer_stalled_past_its_ttl_is_fenced_off_on_a_real_bucket(tmp_path, bucket, redis):
    from workspaces.store.lock import LockLost

    a = pod(tmp_path, bucket, redis, "a")
    make_repo(a.path("ws"), {"f": "0"})
    publish(a, "ws")
    b = pod(tmp_path, bucket, redis, "b")
    b.hydrate("ws")
    commit(a.path("ws"), {"f": "stale"}, "a")
    commit(b.path("ws"), {"f": "fresh"}, "b")
    held = a.locks.acquire("ws")

    def stall():
        redis.delete("wsstore:lock:ws")
        publish(b, "ws")

    held.check = stall
    with pytest.raises(LockLost):
        a.publish("ws", held=held)
    c = pod(tmp_path, bucket, redis, "c")
    c.hydrate("ws")
    assert (c.path("ws") / "f").read_text() == "fresh"


def test_lazy_boot_and_eviction_on_a_real_bucket(tmp_path, bucket, redis):
    from workspaces.shared import workspace_paths as wpaths
    from workspaces.store import boot as store_boot

    seed = pod(tmp_path, bucket, redis, "seed")
    for rel in ("x", "y"):
        make_repo(seed.path(rel), {"f.md": rel * 3000})
        publish(seed, rel)
    run = store_boot.start(str(tmp_path / "cache"), redis, objects=bucket,
                           env={"VEXA_WORKSPACE_BACKEND": "s3", "VEXA_WORKSPACE_CACHE_MIN_IDLE_S": "0"})
    try:
        assert (tmp_path / "cache/x/.git/vexa-store-stub").exists()
        assert wpaths.read_text_inside(tmp_path / "cache/x", "f.md") == "x" * 3000
        assert run.cache.evict("x")
        assert wpaths.read_text_inside(tmp_path / "cache/x", "f.md") == "x" * 3000
    finally:
        run.stop()
