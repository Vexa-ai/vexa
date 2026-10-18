"""ADR-0046 decision 2 — routines become flows: the one-time migration and the adapter routes.

Where flows is deployed, `routines/*.md` files and the runtime scheduler's routine jobs are
converted ONCE into the person's own schedule flows (idempotent, counts-only), the jobs they armed
are cancelled, and `/api/routines` answers from flows — deprecated, for one release."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from control_plane import identity_token
from control_plane import routine_flows as rf
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

KEY = identity_token.generate_signing_key()
URL = "http://agent-api:8100/invocations"
ROUTINE = "---\nenabled: true\ncron: '0 9 * * *'\nprompt: Do the brief.\n---\n"


class _FakeScheduler:
    """The runtime scheduler as the old routine mechanism left it: jobs listed, cancelled by id."""
    def __init__(self):
        self.jobs = []

    def schedule(self, job):
        stored = {**job, "job_id": f"job_{len(self.jobs)}", "status": "pending"}
        self.jobs.append(stored)
        return stored

    def list_jobs(self, *, status=None, limit=50):
        return list(self.jobs)

    def cancel_job(self, job_id):
        for j in self.jobs:
            if j["job_id"] == job_id:
                self.jobs.remove(j)
                return j
        return None


def _old_job(owner, name, cron, prompt, *, source=None, path=None) -> dict:
    """A routine job exactly as the removed compile path stored it on the scheduler."""
    meta = {"routine_id": f"rt_{name}", "owner": owner, "name": name, "cron": cron,
            "kind": "scheduled", "plan_kind": "prompt", "plan_summary": prompt}
    if source:
        meta.update({"source": source, "workspace_path": path})
    return {"cron": cron, "request": {"method": "POST", "url": URL, "body": {}}, "metadata": meta}


class FakeFlows:
    """flows-api, as the routine adapter speaks to it: per-scope versions, statuses, refusals."""

    def __init__(self):
        self.rows: list[dict] = []
        self.refuse: dict = {}

    def submit(self, subject, name, cron, instruction, *, activate=True):
        if name in self.refuse:
            raise rf.FlowsError(403, {"refused": self.refuse[name]})
        scope = f"user:{subject}"
        name = rf.flow_name(name)
        v = 1 + max([r["version"] for r in self.rows if r["scope"] == scope and r["name"] == name],
                    default=0)
        row = {"name": name, "version": v, "scope": scope, "steps": ["agent_run"],
               "trigger": {"kind": "schedule", "cron": cron},
               "params": {"instruction": instruction}, "status": "active" if activate else "draft"}
        self.rows.append(row)
        return dict(row)

    def routines(self, subject):
        newest = {}
        for r in self.rows:
            if r["scope"] == f"user:{subject}":
                if r["name"] not in newest or r["version"] > newest[r["name"]]["version"]:
                    newest[r["name"]] = r
        return list(newest.values())

    def set_status(self, subject, name, version, action):
        for r in self.rows:
            if r["scope"] == f"user:{subject}" and r["name"] == name and r["version"] == version:
                r["status"] = "active" if action in ("resume", "activate") else "retired"
                return r
        raise rf.FlowsError(404, "flow version not found")


@pytest.fixture
def flows(monkeypatch):
    fake = FakeFlows()
    monkeypatch.setattr(rf, "FlowsClient", lambda: fake)
    monkeypatch.setattr(rf, "configured", lambda: True)
    return fake


@pytest.fixture
def world(monkeypatch, tmp_path, flows):
    public = tmp_path / "identity-public-key.pem"
    public.write_bytes(identity_token.public_key_pem(KEY))
    monkeypatch.setenv("VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE", str(public))
    monkeypatch.setenv("INTERNAL_API_SECRET", "agent-test-internal-secret")
    root = tmp_path / "workspaces"
    (root / "7" / "routines").mkdir(parents=True)
    (root / "7" / "routines" / "brief.md").write_text(ROUTINE)
    scheduler = _FakeScheduler()
    # what the old mechanism held: the file's job, and one armed through POST /api/routines
    scheduler.schedule(_old_job("7", "brief", "0 9 * * *", "Do the brief.",
                                source=rf.WORKSPACE_ROUTINE_SOURCE, path="routines/brief.md"))
    scheduler.schedule(_old_job("7", "inbox", "0 12 * * 1-5", "triage"))
    client = TestClient(create_app(Dispatcher(load_settings(), _Runtime(), _Identity()),
                                   scheduler=scheduler, invocations_url=URL,
                                   reader=WorkspaceReader(str(root))))
    return client, scheduler, root


class _Runtime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


def _as(regime=None) -> dict:
    claims = {"sub": "7", "email": "ada@example.com"}
    if regime is not None:
        claims["delegation"] = {"regime": regime, "workspaces": "*" if regime == "human" else ["7"]}
    return {identity_token.HEADER: identity_token.sign(KEY, claims)}


def test_files_and_jobs_become_the_persons_flows_once(world, flows):
    client, scheduler, root = world
    assert len(scheduler.jobs) == 2
    first = rf.migrate(scheduler=scheduler, workspaces_dir=root)
    assert first.files_converted == 1 and first.jobs_converted == 1 and first.failures == 0
    assert scheduler.jobs == [], "the jobs the old mechanism armed are cancelled"
    names = {(r["scope"], r["name"], r["trigger"]["cron"]) for r in flows.rows}
    assert names == {("user:7", "brief", "0 9 * * *"), ("user:7", "inbox", "0 12 * * 1-5")}
    brief = next(r for r in flows.rows if r["name"] == "brief")
    assert brief["steps"] == ["agent_run"] and brief["params"]["instruction"] == "Do the brief."
    again = rf.migrate(scheduler=scheduler, workspaces_dir=root)
    assert again.files_converted == 0 and len(flows.rows) == 2


def test_a_routine_flows_refuses_is_not_retried_every_pass(world, flows):
    client, scheduler, root = world
    flows.refuse["brief"] = "quota_min_interval"
    rf.migrate(scheduler=scheduler, workspaces_dir=root)
    tries = len(flows.rows)
    rf.migrate(scheduler=scheduler, workspaces_dir=root)
    assert len(flows.rows) == tries


def test_the_routes_are_deprecated_adapters_onto_flows(world, flows):
    client, scheduler, root = world
    r = client.post("/api/routines", headers=_as(),
                    json={"name": "Morning brief", "cron": "0 8 * * *", "prompt": "brief me"})
    assert r.status_code == 201 and r.headers["Deprecation"] == "true"
    assert rf.ADAPTER_ENDS in r.headers["Sunset"]
    assert r.json()["routine"]["id"] == "flow:Morning-brief"
    assert all(j["metadata"]["name"] != "Morning brief" for j in scheduler.jobs), "no job is armed"
    cards = client.get("/api/routines", headers=_as()).json()["routines"]
    card = next(c for c in cards if c["name"] == "Morning-brief")
    assert card["thread"] == rf.thread_session("user:7", "Morning-brief")
    assert client.patch("/api/routines/Morning-brief/enabled", headers=_as(),
                        json={"enabled": False}).json()["enabled"] is False
    assert flows.rows[-1]["status"] == "retired"
    assert client.delete("/api/routines/flow:Morning-brief", headers=_as()).json()["retired"]


def test_a_refusal_from_flows_reaches_the_person_typed(world, flows):
    client, _, _ = world
    flows.refuse["fast"] = "quota_min_interval"
    r = client.post("/api/routines", headers=_as(),
                    json={"name": "fast", "cron": "*/5 * * * *", "prompt": "x"})
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "quota_min_interval"


def test_an_unwatched_worker_still_cannot_arm_a_routine(world, flows):
    client, _, _ = world
    r = client.post("/api/routines", headers=_as("autonomous"),
                    json={"name": "x", "cron": "0 8 * * *", "prompt": "x"})
    assert r.status_code == 403 and flows.rows == []


def test_the_thread_name_is_the_one_flows_derives():
    import hashlib
    assert rf.thread_session("user:7", "brief") == "flow-" + hashlib.sha1(
        b"user:7|brief").hexdigest()[:16]


def test_without_flows_there_are_no_routines(world, monkeypatch):
    client, _, _ = world
    monkeypatch.setattr(rf, "configured", lambda: False)
    r = client.get("/api/routines", headers=_as())
    assert r.status_code == 501 and r.json()["detail"]["refused"] == "routines_need_flows"


def test_the_migration_runs_until_done_then_never_again(world, flows):
    client, scheduler, root = world
    rf.start_migration(scheduler=scheduler, workspaces_dir=root, client_factory=lambda: flows,
                       background=False)
    assert rf.migration_done(root) and scheduler.jobs == []
    (root / "7" / "routines" / "later.md").write_text(ROUTINE)
    assert rf.start_migration(scheduler=scheduler, workspaces_dir=root,
                              client_factory=lambda: flows) is None
    assert "later" not in {r["name"] for r in flows.rows}, "a file written afterwards arms nothing"


def test_a_pass_that_cannot_reach_the_scheduler_is_not_done(world, flows):
    client, scheduler, root = world

    class Down(_FakeScheduler):
        def list_jobs(self, **_):
            raise OSError("down")
    rf.start_migration(scheduler=Down(), workspaces_dir=root, client_factory=lambda: flows,
                       interval_sec=0)
    assert not rf.migration_done(root)


def test_a_routine_card_carries_what_its_graph_draws(world, flows):
    client, _, root = world
    client.post("/api/routines", headers=_as(), json={"name": "sweep", "cron": "*/15 * * * *",
                                                       "prompt": "sweep the inbox"})
    card = next(c for c in client.get("/api/routines", headers=_as()).json()["routines"]
                if c["name"] == "sweep")
    assert card["trigger"] == {"kind": "schedule", "cron": "*/15 * * * *"}
    assert card["steps"] == ["agent_run"]
    assert card["policy"]["key"] == "connection_actions_unattended"
    assert "gmail.search" in card["policy"]["refused"] + card["policy"]["allowed"]
