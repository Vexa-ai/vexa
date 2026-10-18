"""WS-S3 at agent-api's edge: the worker store routes are scoped to the unit's own dispatch, a push
answers only once durable, and no state-changing response leaves before the bucket has the change."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane import workspace_store_service as svc
from control_plane.routers import workspace_store as routes
from workspaces.shared import gitexec
from workspaces.store import boot as store_boot
from workspaces.store.objects import MemoryObjects

AUTHOR = ("-c", "user.name=t", "-c", "user.email=t@example.com")


def make_repo(path: Path, files: dict) -> str:
    path.mkdir(parents=True, exist_ok=True)
    gitexec.run_git(str(path), "init", "-q", "-b", "main", check=True)
    for rel, text in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text)
    gitexec.run_git(str(path), "add", "-A", check=True)
    gitexec.run_git(str(path), *AUTHOR, "commit", "-q", "-m", "init", check=True)
    return gitexec.run_git(str(path), "rev-parse", "HEAD", check=True).stdout.strip()


@pytest.fixture
def stack(tmp_path, monkeypatch):
    """A seeded bucket, a booted s3 backend over an empty cache, and an app with the store routes."""
    bucket = MemoryObjects(conditional=False)
    r = fakeredis.FakeRedis(decode_responses=True)
    seed = tmp_path / "seed"
    make_repo(seed / "17", {"README.md": "desk"})
    make_repo(seed / "deal", {"README.md": "shared"})
    make_repo(seed / "other", {"README.md": "someone else"})
    from workspaces.store.migrate import migrate
    from workspaces.store.lock import WorkspaceLocks

    assert migrate(seed, bucket, WorkspaceLocks(r))["ok"]
    cache = tmp_path / "workspaces"
    run = store_boot.start(str(cache), r, env={"VEXA_WORKSPACE_BACKEND": "s3"}, objects=bucket)
    monkeypatch.setattr(svc, "_running", run)
    svc._local_mounts.clear()
    app = FastAPI()
    settings = SimpleNamespace(workspaces_dir=str(cache), mcp_url="http://gateway:8000/mcp",
                               workspace_store_url="s3://vexa-workspaces")
    app.include_router(routes.build(settings=settings, redis_url=""))
    svc.install(app, redis_url="", workspaces_dir=str(cache), sync_interval_s=3600)

    @app.post("/api/test/platform-write")
    def platform_write():
        (cache / ".attached/17").mkdir(parents=True, exist_ok=True)
        (cache / ".attached/17/state.json").write_text('{"active_set": ["deal"]}')
        return {"ok": True}

    yield SimpleNamespace(app=app, client=TestClient(app), bucket=bucket, cache=cache, run=run,
                          settings=settings)
    run.stop()
    svc._running = None


def headers(unit="agent-u-1", subject="17"):
    return {svc.UNIT_HEADER: unit, "x-user-id": subject}


def record(stack, mounts, unit="agent-u-1", subject="17"):
    import json
    svc.record_unit_mounts(None, unit, subject, {"VEXA_MOUNTS": json.dumps(mounts)})


def test_a_worker_fetches_only_the_mounts_its_dispatch_recorded(stack):
    root = str(stack.cache)
    record(stack, [{"path": f"{root}/17", "write": True}, {"path": f"{root}/deal", "write": False}])
    ok = stack.client.get("/api/workspace-store/fetch", params={"path": f"{root}/17"}, headers=headers())
    assert ok.status_code == 200 and ok.content.startswith(b"# v2 git bundle")
    other = stack.client.get("/api/workspace-store/fetch", params={"path": f"{root}/other"}, headers=headers())
    assert other.status_code == 403 and other.json()["detail"]["refused"] == "not_mounted"
    escape = stack.client.get("/api/workspace-store/fetch", params={"path": f"{root}/17/../other"},
                              headers=headers())
    assert escape.status_code == 403
    stranger = stack.client.get("/api/workspace-store/fetch", params={"path": f"{root}/17"},
                                headers=headers(subject="99"))
    assert stranger.status_code == 403 and stranger.json()["detail"]["refused"] == "not_this_dispatch"
    nobody = stack.client.get("/api/workspace-store/fetch", params={"path": f"{root}/17"},
                              headers=headers(unit="agent-u-unknown"))
    assert nobody.status_code == 403


def _worker_commit(tmp_path, bundle: bytes, files: dict) -> tuple[str, str, bytes]:
    w = tmp_path / "worker"
    w.mkdir()
    gitexec.run_git(str(w), "init", "-q", check=True)
    (tmp_path / "in.bundle").write_bytes(bundle)
    gitexec.run_git(str(w), "fetch", "-q", str(tmp_path / "in.bundle"), "+refs/heads/*:refs/heads/*",
                    env={"GIT_ALLOW_PROTOCOL": "file"}, check=True)
    gitexec.run_git(str(w), "symbolic-ref", "HEAD", "refs/heads/main", check=True)
    gitexec.run_git(str(w), "reset", "-q", "--hard", check=True)
    base = gitexec.run_git(str(w), "rev-parse", "HEAD", check=True).stdout.strip()
    for rel, text in files.items():
        (w / rel).parent.mkdir(parents=True, exist_ok=True)
        (w / rel).write_text(text)
    gitexec.run_git(str(w), "add", "-A", check=True)
    gitexec.run_git(str(w), *AUTHOR, "commit", "-q", "-m", "turn", check=True)
    out = tmp_path / "out.bundle"
    gitexec.run_git(str(w), "bundle", "create", "-q", str(out), "refs/heads/main", f"^{base}", check=True)
    return base, gitexec.run_git(str(w), "rev-parse", "HEAD", check=True).stdout.strip(), out.read_bytes()


def test_a_push_is_answered_only_once_durable_and_read_only_mounts_refuse(stack, tmp_path):
    root = str(stack.cache)
    record(stack, [{"path": f"{root}/17", "write": True}, {"path": f"{root}/deal", "write": False}])
    bundle = stack.client.get("/api/workspace-store/fetch", params={"path": f"{root}/17"}, headers=headers()).content
    base, new, data = _worker_commit(tmp_path, bundle, {"kg/x.md": "X"})
    r = stack.client.post("/api/workspace-store/push", params={"path": f"{root}/17", "base": base},
                          content=data, headers=headers())
    assert r.status_code == 200 and r.json() == {"gen": 2, "head": new, "durable": True}
    assert stack.run.store.latest("17").refs["refs/heads/main"] == new
    ro = stack.client.post("/api/workspace-store/push", params={"path": f"{root}/deal", "base": base},
                           content=data, headers=headers())
    assert ro.status_code == 403 and ro.json()["detail"]["refused"] == "read_only"
    again = stack.client.post("/api/workspace-store/push", params={"path": f"{root}/17", "base": base},
                              content=data, headers=headers())
    assert again.status_code == 409 and again.json()["detail"]["refused"] == "workspace_changed"


def test_a_push_touching_policy_is_refused_at_the_write_path(stack, tmp_path):
    root = str(stack.cache)
    record(stack, [{"path": f"{root}/17", "write": True}])
    bundle = stack.client.get("/api/workspace-store/fetch", params={"path": f"{root}/17"}, headers=headers()).content
    base, _, data = _worker_commit(tmp_path, bundle, {"policy/members.json": '{"17": "owner"}'})
    r = stack.client.post("/api/workspace-store/push", params={"path": f"{root}/17", "base": base},
                          content=data, headers=headers())
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "reserved_path"
    assert stack.run.store.latest("17").gen == 1


def test_a_push_is_refused_loudly_while_the_bucket_is_down(stack, tmp_path):
    root = str(stack.cache)
    record(stack, [{"path": f"{root}/17", "write": True}])
    bundle = stack.client.get("/api/workspace-store/fetch", params={"path": f"{root}/17"}, headers=headers()).content
    base, _, data = _worker_commit(tmp_path, bundle, {"kg/x.md": "X"})
    stack.bucket.down = True
    r = stack.client.post("/api/workspace-store/push", params={"path": f"{root}/17", "base": base},
                          content=data, headers=headers())
    assert r.status_code == 503 and r.json()["detail"]["refused"] == "workspace_store_unavailable"
    stack.bucket.down = False
    assert gitexec.run_git(str(stack.cache / "17"), "rev-parse", "HEAD", check=True).stdout.strip() == base


def test_a_state_changing_response_waits_for_the_bucket(stack):
    r = stack.client.post("/api/test/platform-write")
    assert r.status_code == 200
    assert stack.bucket.get("files/.attached/17/state.json") == b'{"active_set": ["deal"]}'
    (stack.cache / ".attached/17/state.json").write_text("before")
    stack.run.sync.sync()
    stack.bucket.down = True
    failed = stack.client.post("/api/test/platform-write")
    assert failed.status_code == 503, "the route's 200 does not leave when the bucket did not take it"
    assert (stack.cache / ".attached/17/state.json").read_text() == "before", "and the change is not kept"


def test_the_volume_backend_serves_no_store_routes(stack):
    svc._running = None
    r = stack.client.get("/api/workspace-store/fetch", params={"path": "/workspaces/17"}, headers=headers())
    assert r.status_code == 404 and r.json()["detail"]["refused"] == "volume_backend"


def test_the_worker_url_comes_from_the_gateway_edge(stack):
    assert svc.worker_url(stack.settings) == "http://gateway:8000/agent/workspace-store"
    assert svc.health()["guard"] == "redis-lock+fencing+generation-election"
    svc._running = None
    assert svc.worker_url(stack.settings) == "" and svc.health() is None
