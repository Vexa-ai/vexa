"""L2 · Control plane in-process — the cookbook over the REAL control_plane app via ASGI.

The fixture boundary drops below the wire: a real `Slim` client drives the real `control_plane` FastAPI app
through `httpx.ASGITransport` (no network), with fakes for runtime/scheduler and a tmp workspace on disk.
Proves the cookbook → client → endpoint path end-to-end for the agent-domain verbs.

Lives under core/agent/tests (not clients/slim/tests) because it imports BOTH packages — it's an integration
level, so it sits with the server it integrates against and reuses the agent test fakes.

KNOWN UPSTREAM GAP (surfaced by this level): `schedule_routine` (POST /api/routines) compiles a job only,
while `list`/`set_routine_enabled` read FILE-authored routines (routines/*.md) — two stores. Enabling a
POST-created routine 404s. Both paths are tested separately below; unifying them is a deferred upstream fix.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "clients" / "slim" / "vexa_slim").is_dir():
            return parent
    raise FileNotFoundError("repo root with clients/slim not found")


sys.path.insert(0, str(_repo_root() / "clients" / "slim"))   # make vexa_slim importable here

from control_plane.api import create_app                      # noqa: E402
from control_plane.dispatch import Dispatcher                 # noqa: E402
from control_plane.workspace_reader import WorkspaceReader    # noqa: E402
from shared.config import load_settings                       # noqa: E402
from vexa_slim import cookbook as cb                          # noqa: E402
from vexa_slim.client import Slim                             # noqa: E402

pytestmark = pytest.mark.asyncio   # core/agent runs asyncio in strict mode — mark this module's tests


class _FakeRuntime:
    def spawn(self, workload_id, profile, env): return workload_id
    def await_done(self, workload_id, timeout_sec=0.0): return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools): return "tok"


class _FakeScheduler:
    def __init__(self): self.jobs = []
    def schedule(self, job):
        stored = {**job, "job_id": f"job_{len(self.jobs)}", "status": "pending", "execute_at": 1000.0}
        self.jobs.append(stored)
        return stored
    def list_jobs(self, *, status=None, limit=50): return list(self.jobs)
    def cancel_job(self, job_id):
        self.jobs = [j for j in self.jobs if j["job_id"] != job_id]
        return None


class _FakeReader:
    def read(self, unit_id):
        yield {"type": "message-delta", "text": "hi"}
        yield {"type": "commit", "sha": "abc123"}


class _AgentToApi:
    """ASGI shim: the client targets the canonical `/agent/*`; the bare app serves `/api/*` (the gateway
    does this rewrite in prod). Map one to the other so the in-process app answers the client's URLs."""
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope["path"].startswith("/agent/"):
            scope = dict(scope)
            scope["path"] = "/api/" + scope["path"][len("/agent/"):]
            scope["raw_path"] = scope["path"].encode()
        await self.app(scope, receive, send)


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_AGENT_DEFAULT_SUBJECT", "u_jane")          # no gateway in-process → fallback subject
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(_repo_root() / "core" / "agent" / "workspace-seeds" / "default"))
    scheduler = _FakeScheduler()
    app = create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
        stream_reader=_FakeReader(),
        reader=WorkspaceReader(str(tmp_path)),
        scheduler=scheduler,
        invocations_url="http://agent-api:8100/invocations",
    )
    transport = httpx.ASGITransport(app=_AgentToApi(app))
    real = httpx.AsyncClient
    monkeypatch.setattr("vexa_slim.client.httpx.AsyncClient",
                        lambda *a, **k: real(*a, **{**k, "transport": transport}))
    slim = Slim("http://gw", "test-key")
    return type("Ctx", (), {"slim": slim, "scheduler": scheduler, "ws": tmp_path / "u_jane"})()


# ── init + workspace reads ──────────────────────────────────────────────────────────────────────────
async def test_init_workspace_seeds_then_reads_back(ctx):
    out = await cb.init_workspace(ctx.slim)
    assert out.get("ok") or out.get("seeded")
    tree = await cb.browse_workspace(ctx.slim)
    paths = [f if isinstance(f, str) else (f.get("path") or f.get("name")) for f in tree]
    assert "CLAUDE.md" in paths
    body = await cb.read_workspace_file(ctx.slim, "CLAUDE.md")
    assert body and "workspace" in body.lower()


# ── chat (SSE) routes and streams ─────────────────────────────────────────────────────────────────--
async def test_chat_streams_reply(ctx):
    await cb.init_workspace(ctx.slim)
    reply = await cb.chat(ctx.slim, "hello")
    assert reply == "hi"          # _FakeReader's message-delta folded by the cookbook


# ── routines: the POST create path ────────────────────────────────────────────────────────────────--
async def test_schedule_routine_compiles_a_job(ctx):
    out = await cb.schedule_routine(ctx.slim, "digest", cron="0 8 * * *", prompt="brief me")
    assert out["job_id"] == "job_0"
    assert ctx.scheduler.jobs and ctx.scheduler.jobs[0]["cron"] == "0 8 * * *"


# ── routines: the FILE-authored path (list + enable/disable) ───────────────────────────────────────--
async def test_file_routine_lists_and_toggles(ctx):
    await cb.init_workspace(ctx.slim)
    routines = ctx.ws / "routines"
    routines.mkdir(parents=True, exist_ok=True)
    (routines / "digest.md").write_text(
        '---\nenabled: true\ncron: "30 9 * * mon-fri"\nprompt: "brief me"\n---\nGroup by person.\n')

    cards = await cb.list_routines(ctx.slim)
    assert any(c.get("name") == "digest" for c in cards)

    out = await cb.set_routine_enabled(ctx.slim, "digest", False)
    assert out["enabled"] is False
    assert 'enabled: false' in (routines / "digest.md").read_text().lower()
