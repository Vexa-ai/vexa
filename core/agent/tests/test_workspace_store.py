"""WS-S3: workspace repositories held durably in an S3 bucket (``core/workspaces/store``).

In-process: the bucket is ``MemoryObjects`` (switchable conditional PUT, switchable outage), the lock
service is fakeredis. The guarantee under test is the founder's (2026-10-17): a write is acknowledged
only once it is durable in the bucket, a pod can die at any moment without losing an acknowledged
write, and a cold pod rebuilds its cache from the bucket.
"""
from __future__ import annotations

import shutil
import threading
from pathlib import Path

import fakeredis
import pytest

from workspaces.shared import gitexec
from workspaces.store import boot as store_boot
from workspaces.store.client import StoreClient, WorkerCache, WorkspaceStoreError as WorkerStoreError
from workspaces.store.lock import LockLost, WorkspaceLocks
from workspaces.store.objects import MemoryObjects, StoreUnavailable, self_test
from workspaces.store.observer import CacheSync, StoreObserver, WorkspaceStoreError
from workspaces.store.repostore import RepoStore, ReservedPath, StaleBase, scan_cache

AUTHOR = ("-c", "user.name=t", "-c", "user.email=t@example.com")


def git(repo, *args, check=True):
    return gitexec.run_git(str(repo), *AUTHOR, *args, check=check) if args and args[0] in ("commit", "merge") \
        else gitexec.run_git(str(repo), *args, check=check)


def make_repo(path: Path, files: dict) -> None:
    path.mkdir(parents=True, exist_ok=True)
    gitexec.run_git(str(path), "init", "-q", "-b", "main", check=True)
    commit(path, files, "init")


def commit(path: Path, files: dict, msg: str) -> str:
    for rel, text in files.items():
        p = path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    gitexec.run_git(str(path), "add", "-A", check=True)
    gitexec.run_git(str(path), *AUTHOR, "commit", "-q", "-m", msg, check=True)
    return head(path)


def head(path: Path) -> str:
    return gitexec.run_git(str(path), "rev-parse", "HEAD", check=True).stdout.strip()


@pytest.fixture
def bucket():
    return MemoryObjects(conditional=False)


@pytest.fixture
def redis():
    return fakeredis.FakeRedis(decode_responses=True)


def new_pod(tmp_path: Path, bucket, redis, name: str) -> RepoStore:
    """One agent-api pod: its own empty cache directory, the shared bucket and lock service."""
    return RepoStore(objects=bucket, locks=WorkspaceLocks(redis), cache_root=tmp_path / name)


@pytest.fixture(autouse=True)
def _no_observer():
    yield
    from workspaces.store.client import install_observer, install_path_observer
    install_observer(None)
    install_path_observer(None)


def publish(store: RepoStore, rel: str, **kw):
    with store.locks.hold(rel) as held:
        return store.publish(rel, held=held, **kw)


# ── round trip ─────────────────────────────────────────────────────────────────────────────────
def test_a_repository_round_trips_through_the_bucket(tmp_path, bucket, redis):
    pod = new_pod(tmp_path, bucket, redis, "pod-a")
    make_repo(pod.path("17"), {"README.md": "hello", "kg/entities/a.md": "A"})
    g1 = publish(pod, "17")
    assert g1.gen == 1 and g1.refs["refs/heads/main"] == head(pod.path("17"))
    sha2 = commit(pod.path("17"), {"kg/entities/b.md": "B"}, "two")
    g2 = publish(pod, "17")
    assert g2.gen == 2 and len(g2.bundles) == 2, "the second write is an incremental bundle"

    cold = new_pod(tmp_path, bucket, redis, "pod-b")
    assert cold.hydrate("17").gen == 2
    assert head(cold.path("17")) == sha2
    assert (cold.path("17") / "kg/entities/b.md").read_text() == "B"


def test_a_long_chain_is_compacted_into_one_bundle(tmp_path, bucket, redis):
    pod = new_pod(tmp_path, bucket, redis, "pod")
    pod.compact_after = 3
    make_repo(pod.path("ws"), {"a": "0"})
    publish(pod, "ws")
    for i in range(1, 6):
        commit(pod.path("ws"), {"a": str(i)}, f"c{i}")
        g = publish(pod, "ws")
    assert len(g.bundles) <= 3
    assert len(bucket.list("repos/ws/@/bundles/")) == len(g.bundles), "superseded bundles are deleted"
    cold = new_pod(tmp_path, bucket, redis, "cold")
    cold.hydrate("ws")
    assert (cold.path("ws") / "a").read_text() == "5"


# ── the guarantee ──────────────────────────────────────────────────────────────────────────────
def test_an_acknowledged_write_survives_the_pod(tmp_path, bucket, redis):
    """Kill the pod right after an acknowledged write, cold-start a fresh one, and the write is there."""
    pod = new_pod(tmp_path, bucket, redis, "pod-1")
    make_repo(pod.path("17"), {"README.md": "v1"})
    publish(pod, "17")
    observer = StoreObserver(pod)
    gitexec.set_ref_observer(observer)
    (pod.path("17") / "README.md").write_text("acknowledged")
    gitexec.run_git(str(pod.path("17")), "add", "-A", check=True)
    gitexec.run_git(str(pod.path("17")), *AUTHOR, "commit", "-q", "-m", "ack", check=True)  # returned = acknowledged
    acked = head(pod.path("17"))
    # the pod dies: its cache directory and every in-process object are gone
    gitexec.set_ref_observer(None)
    shutil.rmtree(pod.cache_root)
    del observer, pod
    fresh = new_pod(tmp_path, bucket, redis, "pod-2")
    assert fresh.hydrate_all()["repos"] == 1
    assert head(fresh.path("17")) == acked
    assert (fresh.path("17") / "README.md").read_text() == "acknowledged"


def test_a_write_the_bucket_did_not_take_is_refused_and_not_kept(tmp_path, bucket, redis):
    pod = new_pod(tmp_path, bucket, redis, "pod")
    make_repo(pod.path("17"), {"README.md": "durable"})
    durable = publish(pod, "17").refs["refs/heads/main"]
    gitexec.set_ref_observer(StoreObserver(pod))
    (pod.path("17") / "README.md").write_text("lost")
    gitexec.run_git(str(pod.path("17")), "add", "-A", check=True)
    bucket.down = True
    with pytest.raises(WorkspaceStoreError) as err:
        gitexec.run_git(str(pod.path("17")), *AUTHOR, "commit", "-q", "-m", "x", check=True)
    assert err.value.status == 503 and err.value.reason == "workspace_store_unavailable"
    assert head(pod.path("17")) == durable, "the unacknowledged commit is not left in the cache"
    assert (pod.path("17") / "README.md").read_text() == "durable"


def test_reads_are_served_from_the_cache_while_the_bucket_is_down(tmp_path, bucket, redis):
    pod = new_pod(tmp_path, bucket, redis, "pod")
    make_repo(pod.path("17"), {"README.md": "cached"})
    publish(pod, "17")
    bucket.down = True
    assert (pod.path("17") / "README.md").read_text() == "cached"
    assert gitexec.run_git(str(pod.path("17")), "log", "--oneline", check=True).stdout


def test_a_pod_crash_mid_write_leaves_the_last_generation(tmp_path, bucket, redis):
    """A local commit whose publish never happened (the pod died first) is gone after a cold start."""
    pod = new_pod(tmp_path, bucket, redis, "pod")
    make_repo(pod.path("17"), {"README.md": "v1"})
    g1 = publish(pod, "17")
    commit(pod.path("17"), {"README.md": "never published"}, "crash")
    restarted = RepoStore(objects=bucket, locks=WorkspaceLocks(redis), cache_root=pod.cache_root)
    restarted.hydrate_all()
    assert head(restarted.path("17")) == g1.refs["refs/heads/main"]
    assert (restarted.path("17") / "README.md").read_text() == "v1"


# ── concurrency ────────────────────────────────────────────────────────────────────────────────
def test_two_writers_on_one_workspace_one_loses_cleanly(tmp_path, bucket, redis):
    a = new_pod(tmp_path, bucket, redis, "pod-a")
    make_repo(a.path("ws"), {"f": "0"})
    publish(a, "ws")
    b = new_pod(tmp_path, bucket, redis, "pod-b")
    b.hydrate("ws")
    commit(a.path("ws"), {"f": "a"}, "a")
    commit(b.path("ws"), {"f": "b"}, "b")
    won = publish(a, "ws")
    with pytest.raises(StaleBase):
        publish(b, "ws")
    assert a.latest("ws").gen == won.gen == 2
    cold = new_pod(tmp_path, bucket, redis, "cold")
    cold.hydrate("ws")
    assert (cold.path("ws") / "f").read_text() == "a"


def test_concurrent_publishes_serialize_on_the_lock(tmp_path, bucket, redis):
    a = new_pod(tmp_path, bucket, redis, "pod-a")
    make_repo(a.path("ws"), {"f": "0"})
    publish(a, "ws")
    b = new_pod(tmp_path, bucket, redis, "pod-b")
    b.hydrate("ws")
    commit(a.path("ws"), {"f": "a"}, "a")
    commit(b.path("ws"), {"f": "b"}, "b")
    results = {}

    def run(name, pod):
        try:
            results[name] = publish(pod, "ws").gen
        except StaleBase:
            results[name] = "stale"

    threads = [threading.Thread(target=run, args=(n, p)) for n, p in (("a", a), ("b", b))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(map(str, results.values())) == ["2", "stale"]


def test_a_lost_lock_stops_the_publish_before_the_commit_point(tmp_path, bucket, redis):
    pod = new_pod(tmp_path, bucket, redis, "pod")
    make_repo(pod.path("ws"), {"f": "0"})
    publish(pod, "ws")
    commit(pod.path("ws"), {"f": "1"}, "1")
    with pod.locks.hold("ws") as held:
        redis.delete("wsstore:lock:ws")            # the TTL ran out under a stalled writer
        with pytest.raises(LockLost):
            pod.publish("ws", held=held)
    assert pod.latest("ws").gen == 1


def test_a_writer_stalled_past_its_lock_ttl_is_refused_by_the_fence(tmp_path, bucket, redis):
    """No conditional PUT (Linode): A takes the lock, passes its lock check, then stalls past the TTL;
    B takes the lock with a newer fencing token and publishes. A wakes up and is refused."""
    a = new_pod(tmp_path, bucket, redis, "pod-a")
    make_repo(a.path("ws"), {"f": "0"})
    publish(a, "ws")
    b = new_pod(tmp_path, bucket, redis, "pod-b")
    b.hydrate("ws")
    commit(a.path("ws"), {"f": "stale"}, "a")
    commit(b.path("ws"), {"f": "fresh"}, "b")
    held_a = a.locks.acquire("ws")
    won = {}

    def stall():                                 # A has read generation 1 and passes its check …
        redis.delete("wsstore:lock:ws")          # … then stalls past the TTL: its lock expires
        won["gen"] = publish(b, "ws")            # B takes the lock (newer token), publishes gen 2

    held_a.check = stall
    with pytest.raises(LockLost, match="newer"):
        a.publish("ws", held=held_a)             # A resumes: the fence refuses it
    won = won["gen"]
    assert won.fence > held_a.fence
    assert a.latest("ws").writer == won.writer and a.latest("ws").fence == won.fence
    cold = new_pod(tmp_path, bucket, redis, "cold")
    cold.hydrate("ws")
    assert (cold.path("ws") / "f").read_text() == "fresh"


def test_two_writers_racing_for_one_generation_cannot_both_be_acknowledged(tmp_path, bucket, redis):
    """Even past the fence check (both read generation 1), each writes its own key and only one can
    find itself alone at generation 2 — the other is refused and its object removed."""
    a = new_pod(tmp_path, bucket, redis, "pod-a")
    make_repo(a.path("ws"), {"f": "0"})
    g1 = publish(a, "ws")
    b = new_pod(tmp_path, bucket, redis, "pod-b")
    b.hydrate("ws")
    commit(a.path("ws"), {"f": "a"}, "a")
    commit(b.path("ws"), {"f": "b"}, "b")
    acked = publish(a, "ws")
    b.latest = lambda rel: g1                     # B's view lags: it saw generation 1 throughout
    held_b = b.locks.acquire("ws")
    held_b.check = lambda: None
    with pytest.raises(LockLost, match="same time"):
        b.publish("ws", held=held_b)
    keys = [k for k in bucket.list("repos/ws/@/gen/") if "/000000000002-" in k and k.endswith(".json")]
    assert len(keys) == 1
    assert type(a).latest(a, "ws").writer == acked.writer, "the acknowledged generation stays current"


def test_a_stale_writers_claim_left_beside_an_acknowledged_generation_is_never_elected(tmp_path, bucket, redis):
    """A writer acknowledges generation 2 (fence F). A stale writer holding an OLDER fencing token
    passed its fence check before that (it saw generation 1), PUTs its own claim at generation 2 and
    dies before it can see the rival and remove its object. Every reader must still elect the
    acknowledged write; the lowest-token rule alone elected the dead writer's claim."""
    a = new_pod(tmp_path, bucket, redis, "pod-a")
    make_repo(a.path("ws"), {"f": "0"})
    g1 = publish(a, "ws")
    b = new_pod(tmp_path, bucket, redis, "pod-b")
    b.hydrate("ws")
    commit(a.path("ws"), {"f": "acknowledged"}, "a")
    commit(b.path("ws"), {"f": "stale"}, "b")
    stale = b.locks.acquire("ws")                   # B takes the lock first (the older token) …
    redis.delete("wsstore:lock:ws")                 # … and stalls past its TTL
    acked = publish(a, "ws")                        # A: a newer token, generation 2, acknowledged
    assert acked.gen == 2 and acked.fence > stale.fence

    class Dies(Exception):
        pass

    class DyingBucket:
        """The bucket as the stale pod sees it: the PUT lands, then the pod dies at its next LIST."""
        def __init__(self, inner):
            self.inner, self.put_done = inner, False

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def put(self, key, data):
            self.inner.put(key, data)
            if "/gen/" in key:
                self.put_done = True

        def list(self, prefix):
            if self.put_done:
                raise Dies("pod killed between its PUT and its rival check")
            return self.inner.list(prefix)

    b.objects = DyingBucket(bucket)
    b.latest = lambda rel: g1                       # it passed its fence check on generation 1
    stale.check = lambda: None
    with pytest.raises(Dies):
        b.publish("ws", held=stale)
    claims = [k for k in bucket.list("repos/ws/@/gen/") if "/000000000002-" in k and k.endswith(".json")]
    assert len(claims) == 2, claims                 # the dead writer's claim stays in the bucket
    reader = new_pod(tmp_path, bucket, redis, "reader")
    assert reader.latest("ws").writer == acked.writer
    reader.hydrate("ws")
    assert (reader.path("ws") / "f").read_text() == "acknowledged"


def test_with_no_ack_at_the_top_generation_the_lowest_fence_is_elected():
    """The control: a generation nobody acknowledged yet (a writer between its PUT and its ack)
    is elected by the lowest token, as before; an ack wins over a lower unacknowledged token."""
    from workspaces.store.repostore import ack_key, current_key, gen_key

    keys = [gen_key("ws", 1, 1), ack_key("ws", 1, 1), gen_key("ws", 2, 7), gen_key("ws", 2, 3)]
    assert current_key(keys) == gen_key("ws", 2, 3)
    assert current_key(keys + [ack_key("ws", 2, 7)]) == gen_key("ws", 2, 7)
    assert current_key([ack_key("ws", 3, 1)] + keys) == gen_key("ws", 2, 3)   # an ack alone is no generation


def test_the_current_generation_is_the_highest_with_the_lowest_fence():
    from workspaces.store.repostore import current_key, gen_key

    keys = [gen_key("ws", 1, 1), gen_key("ws", 2, 7), gen_key("ws", 2, 3), gen_key("ws", 2, 9)]
    assert current_key(keys) == gen_key("ws", 2, 3)


# ── the cache ──────────────────────────────────────────────────────────────────────────────────
def test_a_warm_cache_fetches_only_what_it_lacks(tmp_path, bucket, redis):
    a = new_pod(tmp_path, bucket, redis, "writer")
    make_repo(a.path("ws"), {"f": "0"})
    publish(a, "ws")
    reader = new_pod(tmp_path, bucket, redis, "reader")
    reader.hydrate("ws")                                   # cold
    commit(a.path("ws"), {"g": "1"}, "1")
    publish(a, "ws")
    fetched = []
    orig = reader._fetch_bundle
    reader._fetch_bundle = lambda repo, key: (fetched.append(key), orig(repo, key))[1]
    reader.hydrate("ws")                                   # warm
    assert len(fetched) == 1
    assert (reader.path("ws") / "g").read_text() == "1"
    assert reader.hydrate("ws") and len(fetched) == 1, "an up-to-date cache fetches nothing"


def test_a_write_on_a_stale_cache_refreshes_first_when_clean(tmp_path, bucket, redis):
    a = new_pod(tmp_path, bucket, redis, "a")
    make_repo(a.path("ws"), {"f": "0"})
    publish(a, "ws")
    b = new_pod(tmp_path, bucket, redis, "b")
    b.hydrate("ws")
    commit(a.path("ws"), {"f": "a"}, "a")
    publish(a, "ws")
    gitexec.set_ref_observer(StoreObserver(b))
    (b.path("ws") / "g").write_text("b")
    gitexec.run_git(str(b.path("ws")), "add", "g", check=True)
    gitexec.run_git(str(b.path("ws")), *AUTHOR, "commit", "-q", "-m", "b", check=True)
    gitexec.set_ref_observer(None)
    cold = new_pod(tmp_path, bucket, redis, "cold")
    cold.hydrate("ws")
    assert (cold.path("ws") / "f").read_text() == "a" and (cold.path("ws") / "g").read_text() == "b"


# ── platform files and whole trees ─────────────────────────────────────────────────────────────
def test_platform_files_and_moved_or_deleted_trees_follow_the_cache(tmp_path, bucket, redis):
    pod = new_pod(tmp_path, bucket, redis, "pod")
    pod.cache_root.mkdir(parents=True)
    sync = CacheSync(pod)
    sync.baseline()
    (pod.cache_root / ".attached/17").mkdir(parents=True)
    (pod.cache_root / ".attached/17/state.json").write_text('{"active_set": ["17"]}')
    make_repo(pod.path("deal"), {"README.md": "deal"})
    done = sync.sync()
    assert done["published"] == 1 and done["files_put"] == 1
    # the slot moves (re-homed into .attached), then is deleted
    shutil.move(str(pod.path("deal")), str(pod.path(".attached/17/deal")))
    sync.sync()
    assert pod.repos() == [".attached/17/deal"]
    shutil.rmtree(pod.path(".attached/17/deal"))
    (pod.cache_root / ".attached/17/state.json").unlink()
    sync.sync()
    assert pod.repos() == [] and pod.files() == []


def test_a_platform_file_the_bucket_did_not_take_is_reverted(tmp_path, bucket, redis):
    pod = new_pod(tmp_path, bucket, redis, "pod")
    pod.cache_root.mkdir(parents=True)
    (pod.cache_root / "state.json").write_text("durable")
    sync = CacheSync(pod)
    sync.sync()
    (pod.cache_root / "state.json").write_text("not durable")
    bucket.down = True
    with pytest.raises(WorkspaceStoreError):
        sync.sync()
    assert (pod.cache_root / "state.json").read_text() == "durable"


def test_scan_cache_tells_repositories_from_platform_files(tmp_path):
    make_repo(tmp_path / "17", {"a": "1"})
    make_repo(tmp_path / ".attached/17/deal", {"b": "2"})
    (tmp_path / ".attached/17/state.json").write_text("{}")
    (tmp_path / ".attached/17/.state.json.1.2.3.tmp").write_text("{}")
    repos, files = scan_cache(tmp_path)
    assert repos == [".attached/17/deal", "17"]
    assert files == [".attached/17/state.json"]


# ── worker push and fetch, through agent-api ───────────────────────────────────────────────────
def test_a_pushed_range_that_touches_policy_is_refused(tmp_path, bucket, redis):
    pod = new_pod(tmp_path, bucket, redis, "pod")
    make_repo(pod.path("ws"), {"README.md": "x", "policy/members.json": "{}"})
    base = publish(pod, "ws").refs["refs/heads/main"]
    worker = tmp_path / "worker"
    gitexec.run_git(str(tmp_path), "clone", "-q", str(pod.path("ws")), str(worker),
                    env={"GIT_ALLOW_PROTOCOL": "file"}, check=True)
    commit(worker, {"policy/members.json": '{"owner": "me"}'}, "grant myself")
    data = _bundle(worker, base)
    with pod.locks.hold("ws") as held, pytest.raises(ReservedPath):
        pod.accept_push("ws", data, base=base, held=held)
    assert head(pod.path("ws")) == base and pod.latest("ws").gen == 1


def test_a_worker_push_lands_only_after_it_is_durable(tmp_path, bucket, redis):
    pod = new_pod(tmp_path, bucket, redis, "pod")
    make_repo(pod.path("ws"), {"README.md": "x"})
    base = publish(pod, "ws").refs["refs/heads/main"]
    worker = tmp_path / "worker"
    gitexec.run_git(str(tmp_path), "clone", "-q", str(pod.path("ws")), str(worker),
                    env={"GIT_ALLOW_PROTOCOL": "file"}, check=True)
    new = commit(worker, {"kg/entities/x.md": "X"}, "turn")
    with pod.locks.hold("ws") as held:
        gen = pod.accept_push("ws", _bundle(worker, base), base=base, held=held)
    assert gen.refs["refs/heads/main"] == new
    cold = new_pod(tmp_path, bucket, redis, "cold")
    cold.hydrate("ws")
    assert (cold.path("ws") / "kg/entities/x.md").read_text() == "X"


def _bundle(repo: Path, base: str) -> bytes:
    out = repo.parent / "push.bundle"
    gitexec.run_git(str(repo), "bundle", "create", "-q", str(out), "refs/heads/main", f"^{base}", check=True)
    data = out.read_bytes()
    out.unlink()
    return data


class _FakeAgentApi:
    """agent-api's two store routes, in process, for the worker client (no HTTP)."""

    def __init__(self, store: RepoStore) -> None:
        self.store = store
        self.root = "/workspaces"

    def __call__(self, req, timeout=None):
        import io
        import urllib.parse

        url = urllib.parse.urlparse(req.full_url)
        q = dict(urllib.parse.parse_qsl(url.query))
        rel = q["path"][len(self.root) + 1:]
        if url.path.endswith("/fetch"):
            data = self.store.bundle_for(rel, have=q.get("have", ""))
            return _Resp(204 if data is None else 200, data or b"")
        with self.store.locks.hold(rel) as held:
            try:
                gen = self.store.accept_push(rel, req.data, base=q["base"], held=held)
            except StaleBase:
                import urllib.error
                raise urllib.error.HTTPError(req.full_url, 409, "stale", {}, io.BytesIO(b"workspace_changed"))
        return _Resp(200, ('{"gen": %d}' % gen.gen).encode())


class _Resp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_a_worker_hydrates_its_mounts_and_its_commit_is_durable_before_it_returns(tmp_path, bucket, redis):
    pod = new_pod(tmp_path, bucket, redis, "agent-api")
    make_repo(pod.path("17"), {"README.md": "desk"})
    make_repo(pod.path("other"), {"README.md": "not mounted"})
    publish(pod, "17")
    publish(pod, "other")
    api = _FakeAgentApi(pod)
    wroot = tmp_path / "worker-pod"
    mounts = [{"slug": "17", "path": "/workspaces/17", "write": True}]
    cache = WorkerCache(StoreClient("http://gw/agent/workspace-store", lambda: "tok", opener=api), mounts)
    cache.mounts = {str(wroot / "17"): {**mounts[0]}}                 # the worker's local path
    assert cache.hydrate() == 1
    assert (wroot / "17/README.md").read_text() == "desk"
    assert not (wroot / "other").exists(), "only the dispatch's workspaces are on the worker's disk"
    gitexec.set_ref_observer(cache)
    (wroot / "17/kg.md").write_text("from the turn")
    gitexec.run_git(str(wroot / "17"), "add", "-A", check=True)
    gitexec.run_git(str(wroot / "17"), *AUTHOR, "commit", "-q", "-m", "turn", check=True)
    gitexec.set_ref_observer(None)
    assert pod.latest("17").gen == 2, "durable in the bucket before the commit returned"
    cold = new_pod(tmp_path, bucket, redis, "cold")
    cold.hydrate("17")
    assert (cold.path("17") / "kg.md").read_text() == "from the turn"


def test_a_worker_push_that_fails_is_not_kept_as_saved(tmp_path, bucket, redis):
    pod = new_pod(tmp_path, bucket, redis, "agent-api")
    make_repo(pod.path("17"), {"README.md": "desk"})
    publish(pod, "17")
    wroot = tmp_path / "worker-pod"
    cache = WorkerCache(StoreClient("http://gw/s", lambda: "t", opener=_FakeAgentApi(pod)),
                        [{"path": "/workspaces/17", "write": True}])
    cache.mounts = {str(wroot / "17"): {"path": "/workspaces/17", "write": True}}
    cache.hydrate()
    base = head(wroot / "17")
    commit(pod.path("17"), {"README.md": "a route wrote meanwhile"}, "route")
    publish(pod, "17")
    gitexec.set_ref_observer(cache)
    (wroot / "17/x").write_text("turn")
    gitexec.run_git(str(wroot / "17"), "add", "-A", check=True)
    with pytest.raises(WorkerStoreError):
        gitexec.run_git(str(wroot / "17"), *AUTHOR, "commit", "-q", "-m", "turn", check=True)
    gitexec.set_ref_observer(None)
    assert head(wroot / "17") == base, "un-committed locally: the turn cannot report it saved"
    cache.freshen()
    assert (wroot / "17/README.md").read_text() == "a route wrote meanwhile"


# ── boot: self-test and configuration ──────────────────────────────────────────────────────────
def test_the_self_test_reports_conditional_put_and_fails_loud_on_a_dead_bucket():
    assert self_test(MemoryObjects(conditional=True)).conditional_put is True
    assert self_test(MemoryObjects(conditional=False)).conditional_put is False
    dead = self_test(MemoryObjects(down=True), where="workspace store https://s3.example/ws")
    assert not dead.ok and "https://s3.example/ws" in dead.error and "CA bundle" in dead.error


class _ClosingConditional(MemoryObjects):
    """Linode us-sea-1 (2026-10-17): a conditional PUT on an existing key closes the connection."""

    def put(self, key, data, *, if_absent=False):
        if if_absent and key in self.data:
            raise StoreUnavailable("connection closed")
        return super().put(key, data)


def test_a_store_that_drops_conditional_puts_is_reported_unsupported():
    probe = self_test(_ClosingConditional())
    assert probe.ok and probe.conditional_put is False


def test_boot_refuses_a_misconfiguration_with_a_sentence(tmp_path, redis):
    with pytest.raises(store_boot.StoreMisconfigured, match="VEXA_WORKSPACE_S3_BUCKET"):
        store_boot.start(str(tmp_path), redis, env={"VEXA_WORKSPACE_BACKEND": "s3"})
    with pytest.raises(store_boot.StoreMisconfigured, match="Redis"):
        store_boot.start(str(tmp_path), None, env={"VEXA_WORKSPACE_BACKEND": "s3"}, objects=MemoryObjects())
    with pytest.raises(store_boot.StoreMisconfigured, match="cannot write and read"):
        store_boot.start(str(tmp_path), redis, env={"VEXA_WORKSPACE_BACKEND": "s3"},
                         objects=MemoryObjects(down=True))
    assert store_boot.start(str(tmp_path), redis, env={}) is None, "volume is the default"


def test_boot_hydrates_and_installs_the_observer(tmp_path, bucket, redis):
    seed = new_pod(tmp_path, bucket, redis, "seed")
    make_repo(seed.path("17"), {"README.md": "x"})
    publish(seed, "17")
    run = store_boot.start(str(tmp_path / "pod"), redis, env={"VEXA_WORKSPACE_BACKEND": "s3"}, objects=bucket)
    try:
        from workspaces.shared import workspace_paths as wpaths
        assert (tmp_path / "pod/17/README.md").exists(), "a stub answers existence checks"
        assert wpaths.read_text_inside(tmp_path / "pod/17", "README.md") == "x", "first use hydrates"
        assert gitexec._ref_observer is run.observer
        assert run.health()["guard"] == "redis-lock+fencing+generation-election"
    finally:
        run.stop()


# ── migration: volume → bucket ─────────────────────────────────────────────────────────────────
def test_the_migration_copies_every_repository_and_file_and_verifies_them(tmp_path, bucket, redis):
    from workspaces.store.migrate import migrate

    vol = tmp_path / "volume"
    make_repo(vol / "17", {"README.md": "desk"})
    commit(vol / "17", {"kg/entities/a.md": "A"}, "two")
    make_repo(vol / ".attached/17/deal", {"README.md": "deal"})
    make_repo(vol / "_global", {"POLICIES.md": "p"})
    (vol / ".attached/17/state.json").write_text('{"active_set": ["17"]}')
    (vol / ".policy/ws-1").mkdir(parents=True)
    (vol / ".policy/ws-1/members.json").write_text('{"owner": "17"}')
    before = {p: p.stat().st_mtime_ns for p in vol.rglob("*") if p.is_file()}
    report = migrate(vol, bucket, WorkspaceLocks(redis))
    assert report["ok"], report
    assert report["published"] == 3 and report["copied"] == 2
    assert {p: p.stat().st_mtime_ns for p in vol.rglob("*") if p.is_file()} == before, "the source is not written"
    # an agent-api pod cold-starts on the bucket alone
    pod = new_pod(tmp_path, bucket, redis, "pod")
    pod.hydrate_all()
    assert (pod.cache_root / ".policy/ws-1/members.json").read_text() == '{"owner": "17"}'
    assert (pod.path("17") / "kg/entities/a.md").read_text() == "A"
    assert migrate(vol, bucket, WorkspaceLocks(redis), verify_only=True)["ok"]


def test_the_migration_refuses_uncommitted_changes_and_verify_catches_a_mismatch(tmp_path, bucket, redis):
    from workspaces.store.migrate import migrate

    vol = tmp_path / "volume"
    make_repo(vol / "17", {"README.md": "desk"})
    (vol / "17/README.md").write_text("edited, never committed")
    report = migrate(vol, bucket, WorkspaceLocks(redis))
    assert not report["ok"] and "17" in report["uncommitted"] and bucket.puts == 0
    gitexec.run_git(str(vol / "17"), "checkout", "--", "README.md", check=True)
    assert migrate(vol, bucket, WorkspaceLocks(redis))["ok"]
    commit(vol / "17", {"late.md": "written after the copy"}, "late")
    assert not migrate(vol, bucket, WorkspaceLocks(redis), verify_only=True)["ok"]
