"""L2 · Control plane in-process — the cookbook over the REAL control_plane app via ASGI.

The fixture boundary drops below the wire: a real `Slim` client drives the real `control_plane` FastAPI app
through `httpx.ASGITransport` (no network), with fakes for runtime/scheduler and a tmp workspace on disk.
Proves the cookbook → client → endpoint path end-to-end for the agent-domain verbs.

Lives under core/agent/tests (not clients/slim/tests) because it imports BOTH packages — it's an integration
level, so it sits with the server it integrates against and reuses the agent test fakes.

Routines are flows (ADR-0046): the cookbook's routine verbs reach the deprecated `/api/routines` adapter,
which writes and reads the person's own flows — one store — so a routine created here lists and toggles.
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


@pytest.fixture(autouse=True)
def _model_creds_configured(monkeypatch):
    """The cookbook's chat verb crosses /api/chat's credential preflight (live-env oracle) — pin a
    credential so this module dispatches deterministically on any machine (same as test_api.py)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")


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
    def read(self, unit_id, *, resume=None, fresh=False):
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
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(_repo_root() / "behavior" / "workspaces" / "default"))
    scheduler = _FakeScheduler()
    app = create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
        stream_reader=_FakeReader(),
        reader=WorkspaceReader(str(tmp_path)),
        scheduler=scheduler,
        invocations_url="http://agent-api:8100/invocations",
        default_subject="u_jane",                                         # no gateway in-process
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


# ── routines: one store, flows (the deprecated adapter) ───────────────────────────────────────────--
@pytest.fixture
def flows(monkeypatch):
    from control_plane import routine_flows as rf
    from tests.test_routine_flows import FakeFlows
    fake = FakeFlows()
    monkeypatch.setattr(rf, "FlowsClient", lambda: fake)
    monkeypatch.setattr(rf, "configured", lambda: True)
    return fake


async def test_a_routine_is_a_flow_that_lists_and_toggles(ctx, flows):
    out = await cb.schedule_routine(ctx.slim, "digest", cron="0 8 * * *", prompt="brief me")
    assert out["job_id"] is None and out["flow"]["scope"] == "user:u_jane"
    assert ctx.scheduler.jobs == [], "nothing is armed on the runtime scheduler"
    cards = await cb.list_routines(ctx.slim)
    assert any(c.get("name") == "digest" and c["enabled"] for c in cards)
    out = await cb.set_routine_enabled(ctx.slim, "digest", False)
    assert out["enabled"] is False and flows.rows[-1]["status"] == "retired"
