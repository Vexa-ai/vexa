"""A routine is armed only from content a person stands behind.

A routine file arms an unwatched agent on a clock, and an unwatched (scheduled) run mounts its
person's workspace read-write, so it could write one. A write on disk carries no identity, so the
reconciler arms a routine only when a person has stood behind its exact bytes: their own write through
`PUT /api/workspace/file` (their credential, or a worker in the human regime), `POST
/api/routines/{name}/confirm`, or — once, at the upgrade — the files already there. Anything else is
`pending_confirmation` on the Routines surface and is not armed (`workspace_routines.PENDING`).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from control_plane import identity_token
from control_plane import workspace_routines as wr
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from tests.test_routines import _FakeScheduler

KEY = identity_token.generate_signing_key()
URL = "http://agent-api:8100/invocations"
ROUTINE = "---\nenabled: true\ncron: '0 9 * * *'\nprompt: Do the brief.\n---\n"


class _Runtime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


@pytest.fixture
def store(monkeypatch, tmp_path):
    public = tmp_path / "identity-public-key.pem"
    public.write_bytes(identity_token.public_key_pem(KEY))
    monkeypatch.setenv("VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE", str(public))
    monkeypatch.setenv("INTERNAL_API_SECRET", "agent-test-internal-secret")
    root = tmp_path / "workspaces"
    (root / "7" / "routines").mkdir(parents=True)
    (root / "7" / "routines" / "old.md").write_text(ROUTINE)  # there before the upgrade
    scheduler = _FakeScheduler()
    client = TestClient(create_app(Dispatcher(load_settings(), _Runtime(), _Identity()),
                                   scheduler=scheduler, invocations_url=URL,
                                   reader=WorkspaceReader(str(root))))
    wr.reconcile_all_workspace_routines(scheduler=scheduler, invocations_url=URL,
                                        workspaces_dir=root)  # the first pass after the upgrade
    return client, scheduler, root


def _as(regime=None) -> dict:
    claims = {"sub": "7", "email": "ada@example.com"}
    if regime is not None:
        claims["delegation"] = {"regime": regime, "workspaces": "*" if regime == "human" else ["7"]}
    return {identity_token.HEADER: identity_token.sign(KEY, claims)}


def _armed(scheduler) -> set:
    return {j["metadata"]["name"] for j in scheduler.jobs}


def _reconcile(scheduler, root):
    return wr.reconcile_workspace_routines("7", scheduler=scheduler, invocations_url=URL,
                                           workspaces_dir=root, signing_secret="s")


def _card(client, name):
    cards = client.get("/api/routines", headers=_as()).json()["routines"]
    return next(c for c in cards if c["name"] == name)


def test_routines_there_before_the_upgrade_stay_armed(store):
    client, scheduler, _ = store
    assert _armed(scheduler) == {"old"}
    assert _card(client, "old")["pending_confirmation"] is False


def test_a_routine_written_straight_onto_the_workspace_waits_for_a_person(store):
    client, scheduler, root = store
    (root / "7" / "routines" / "new.md").write_text(ROUTINE)  # a worker's write on its mount
    result = _reconcile(scheduler, root)
    assert result.pending == 1 and "new" not in _armed(scheduler)
    card = _card(client, "new")
    assert card["status"] == wr.PENDING and card["pending_confirmation"] is True

    assert client.post("/api/routines/new/confirm", headers=_as("autonomous")).status_code == 403
    assert "new" not in _armed(scheduler)
    r = client.post("/api/routines/new/confirm", headers=_as())
    assert r.status_code == 200 and r.json()["confirmed"] is True
    assert "new" in _armed(scheduler)
    assert _card(client, "new")["pending_confirmation"] is False


def test_an_armed_routine_rewritten_by_an_unwatched_run_is_disarmed_until_confirmed(store):
    client, scheduler, root = store
    (root / "7" / "routines" / "old.md").write_text(ROUTINE.replace("Do the brief.", "Mail it all out."))
    _reconcile(scheduler, root)
    assert "old" not in _armed(scheduler)
    assert _card(client, "old")["pending_confirmation"] is True


@pytest.mark.parametrize("regime,armed", [(None, True), ("human", True), ("autonomous", False)])
def test_who_writes_it_through_the_file_route_decides(store, regime, armed):
    client, scheduler, root = store
    r = client.put("/api/workspace/file", headers=_as(regime),
                   json={"path": "routines/written.md", "content": ROUTINE})
    assert r.status_code == 200, r.text
    assert r.json().get("routine_pending_confirmation", False) is (not armed)
    _reconcile(scheduler, root)
    assert ("written" in _armed(scheduler)) is armed


def test_the_toggle_carries_an_approval_and_never_grants_one(store):
    client, scheduler, root = store
    (root / "7" / "routines" / "new.md").write_text(ROUTINE.replace("true", "false", 1))
    _reconcile(scheduler, root)
    assert client.patch("/api/routines/new/enabled", headers=_as(),
                        json={"enabled": True}).status_code == 200
    assert "new" not in _armed(scheduler)
    assert _card(client, "new")["pending_confirmation"] is True

    assert client.patch("/api/routines/old/enabled", headers=_as(),
                        json={"enabled": False}).status_code == 200
    assert client.patch("/api/routines/old/enabled", headers=_as(),
                        json={"enabled": True}).status_code == 200
    assert "old" in _armed(scheduler)


def test_the_record_is_outside_every_workspace(store):
    _, _, root = store
    assert (root / wr.STATE_DIR / wr.APPROVALS_DIR / "7.json").is_file()
    assert wr.STATE_DIR not in wr.scan_workspace_subjects(root)
