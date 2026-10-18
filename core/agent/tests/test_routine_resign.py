"""A routine created before dispatches were signed still fires after the upgrade.

`POST /invocations` now takes the internal tier or a routine job agent-api signed. A routine created
through `POST /api/routines` before that sits in the scheduler unsigned; the reconciler's first pass
re-arms it signed (`control_plane/routine_resign.py`) — once, and only a job that is exactly what this
service compiles from the job's own record.
"""
from __future__ import annotations

import json
import logging

from fastapi.testclient import TestClient

from control_plane import dispatch_sink, routine_resign
from control_plane import routines as routines_mod
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from shared.config import load_settings
from tests.test_routines import _FakeScheduler

SECRET = "agent-test-internal-secret"
URL = "http://agent-api:8100/invocations"


class _Runtime:
    def __init__(self):
        self.spawned = []

    def spawn(self, workload_id, profile, env):
        self.spawned.append((workload_id, profile, env))
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


def _pre_change_job(scheduler, subject="u_jane", name="Morning brief", prompt="Summarize my mail"):
    """What `POST /api/routines` stored before this release: compiled with no signing secret."""
    routine = routines_mod.make_routine(subject=subject, name=name, cron="0 8 * * *", prompt=prompt)
    return scheduler.schedule(json.loads(json.dumps(
        routines_mod.compile_to_job(routine, invocations_url=URL))))


def _fire(client, job):
    return client.post("/invocations", json=job["request"]["body"],
                       headers=job["request"].get("headers") or {})


def test_a_routine_created_before_the_upgrade_still_fires_after_it(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("INTERNAL_API_SECRET", SECRET)
    scheduler = _FakeScheduler()
    old = _pre_change_job(scheduler)
    runtime = _Runtime()
    client = TestClient(create_app(Dispatcher(load_settings(), runtime, _Identity())))
    assert _fire(client, old).status_code == 401  # what the upgrade alone would do to it

    with caplog.at_level(logging.WARNING):
        rearmed = routine_resign.resign_unsigned_routines(
            scheduler, invocations_url=URL, signing_secret=SECRET, store_root=tmp_path)
    assert rearmed == [old["metadata"]["routine_id"]]
    assert "re-armed with a dispatch signature" in caplog.text
    (job,) = scheduler.jobs
    assert job["metadata"] == old["metadata"] and job["cron"] == old["cron"]
    assert _fire(client, job).status_code == 202
    assert len(runtime.spawned) == 1


def test_the_pass_runs_once(tmp_path):
    scheduler = _FakeScheduler()
    _pre_change_job(scheduler)
    assert routine_resign.resign_unsigned_routines(scheduler, invocations_url=URL,
                                                   signing_secret=SECRET, store_root=tmp_path)
    _pre_change_job(scheduler, name="Written into the store later")
    assert routine_resign.resign_unsigned_routines(scheduler, invocations_url=URL,
                                                   signing_secret=SECRET, store_root=tmp_path) is None
    assert sum(1 for j in scheduler.jobs if "headers" not in j["request"]) == 1


def test_a_job_that_is_not_what_this_service_compiles_is_left_unsigned(tmp_path, caplog):
    scheduler = _FakeScheduler()
    job = _pre_change_job(scheduler)
    for tamper in (lambda b: b.update(trigger="message"),
                   lambda b: b["identity"].update(subject="u_admin"),
                   lambda b: b.update(start={"entrypoint": {"inline": "something else"}}),
                   lambda b: b.update(workspaces=[{"id": "u_admin", "mode": "rw"}])):
        scheduler.jobs.clear()
        bad = json.loads(json.dumps(job))
        tamper(bad["request"]["body"])
        scheduler.jobs.append(bad)
        with caplog.at_level(logging.WARNING):
            assert routine_resign.resign_unsigned_routines(
                scheduler, invocations_url=URL, signing_secret=SECRET,
                store_root=tmp_path / str(id(tamper))) == []
        assert "headers" not in scheduler.jobs[0]["request"]
    assert "left unsigned" in caplog.text


def test_workspace_routines_and_signed_jobs_are_not_its_business(tmp_path):
    scheduler = _FakeScheduler()
    routine = routines_mod.make_routine(subject="u_jane", name="x", cron="0 8 * * *", prompt="go")
    signed = routines_mod.compile_to_job(routine, invocations_url=URL, signing_secret=SECRET)
    scheduler.schedule(signed)
    ws = routines_mod.compile_to_job(routine, invocations_url=URL)
    ws["metadata"]["source"] = "workspace-routine"
    scheduler.schedule(ws)
    assert routine_resign.resign_unsigned_routines(scheduler, invocations_url=URL,
                                                   signing_secret=SECRET, store_root=tmp_path) == []
    assert len(scheduler.jobs) == 2


def test_a_running_job_keeps_the_pass_open(tmp_path):
    scheduler = _FakeScheduler()
    _pre_change_job(scheduler)["status"] = "executing"
    scheduler.jobs[0]["status"] = "executing"
    assert routine_resign.resign_unsigned_routines(scheduler, invocations_url=URL,
                                                   signing_secret=SECRET, store_root=tmp_path) == []
    assert not (tmp_path / routine_resign.STATE_DIR / routine_resign.MARKER).exists()
    scheduler.jobs[0]["status"] = "pending"
    assert len(routine_resign.resign_unsigned_routines(scheduler, invocations_url=URL,
                                                       signing_secret=SECRET, store_root=tmp_path)) == 1
    assert dispatch_sink.HEADER in scheduler.jobs[0]["request"]["headers"]


def test_the_reconciler_runs_the_pass(tmp_path):
    from control_plane.workspace_routines import start_workspace_routine_reconciler
    scheduler = _FakeScheduler()
    _pre_change_job(scheduler)
    handle = start_workspace_routine_reconciler(scheduler=scheduler, invocations_url=URL,
                                                workspaces_dir=tmp_path, interval_sec=3600,
                                                signing_secret=SECRET)
    try:
        assert dispatch_sink.HEADER in scheduler.jobs[0]["request"]["headers"]
    finally:
        handle.stop()
