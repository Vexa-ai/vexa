"""A scheduled routine refused the same way three runs in a row is paused (`routine_refusals.py`).

Each run's outcome is the worker's `done` frame on the unit's output stream, stamped `refused` by
the harness. agent-api reads the previous runs at `/invocations`, before the next fire, and at three
consecutive refusals with one reason it switches the routine file off (`enabled: false` +
`paused_reason:`), lets reconcile cancel the job, and puts ONE card on the person's queue.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from control_plane import routine_refusals as rr
from control_plane import workspace_routines as wr
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from shared.units import dispatch_id, output_topic
from tests.test_routines import _FakeScheduler

SECRET = "agent-test-internal-secret"
URL = "http://agent-api:8100/invocations"
INBOX = "---\nenabled: true\ncron: '*/10 * * * *'\nprompt: Check my email.\n---\n"
REFUSED = {"type": "done", "ok": True, "reply": "", "sessionId": None,
           "refused": {"tool": "mail_inbox", "reason": "human_session_required", "count": 1}}
FINE = {"type": "done", "ok": True, "reply": "done", "sessionId": None}


class _Stream:
    """The unit output streams, as the worker writes them (one writer) and agent-api reads them."""

    def __init__(self):
        self.topics: dict[str, list] = {}

    def run(self, uid: str, done: dict) -> None:
        rows = self.topics.setdefault(output_topic(uid), [])
        rows.append((f"{len(rows) + 1}-0", {"event": __import__("json").dumps(done)}))

    def xrevrange(self, name, max="+", min="-", count=None):
        rows = self.topics.get(name, [])
        if min.startswith("("):
            floor = int(min[1:].split("-")[0])
            rows = [r for r in rows if int(r[0].split("-")[0]) > floor]
        return list(reversed(rows))[:count]

    def __getattr__(self, name):            # every other redis call the dispatch path makes
        return lambda *a, **k: None


class _Runtime:
    def __init__(self):
        self.spawned = []

    def spawn(self, workload_id, profile, env):
        self.spawned.append(workload_id)
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


@pytest.fixture
def rig(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERNAL_API_SECRET", SECRET)
    root = tmp_path / "workspaces"
    (root / "7" / "routines").mkdir(parents=True)
    (root / "7" / "routines" / "inbox.md").write_text(INBOX)
    scheduler, stream, runtime, published = _FakeScheduler(), _Stream(), _Runtime(), []
    monkeypatch.setattr("control_plane.publish.publish_routine_paused",
                        lambda *a, **k: published.append(a) or True)
    dispatcher = Dispatcher(load_settings(), runtime, _Identity(), warm_stream=stream)
    client = TestClient(create_app(dispatcher, scheduler=scheduler, invocations_url=URL,
                                   reader=WorkspaceReader(str(root))))
    wr.reconcile_workspace_routines("7", scheduler=scheduler, invocations_url=URL, workspaces_dir=root)
    invocation = scheduler.jobs[0]["request"]["body"]
    return client, scheduler, stream, runtime, published, root, invocation


def _fire(client, invocation):
    r = client.post("/invocations", json=invocation, headers={"x-internal-secret": SECRET})
    assert r.status_code == 202, r.text
    return r.json()


def test_three_refused_runs_pause_the_routine_and_queue_one_card(rig):
    client, scheduler, stream, runtime, published, root, inv = rig
    uid = dispatch_id(inv)
    assert inv["identity"]["launcher"].startswith("schedule:")
    for _ in range(3):
        assert _fire(client, inv)["workload_id"] == uid       # runs 1-3 are dispatched
        stream.run(uid, REFUSED)                               # …and each is refused
    out = _fire(client, inv)                                   # the 4th fire reads the 3rd outcome
    assert out == {"workload_id": None, "paused": "inbox",
                   "routine_id": inv["identity"]["launcher"][len("schedule:"):],
                   "reason": "human_session_required"}
    assert len(runtime.spawned) == 3, "the paused fire is not dispatched"
    text = (root / "7" / "routines" / "inbox.md").read_text()
    assert "enabled: false" in text and "paused_reason:" in text and "Check my email." in text
    assert scheduler.jobs == [], "reconcile cancelled the job"
    assert len(published) == 1 and published[0][2] == "inbox"
    card = wr.routine_cards_for_subject("7", jobs=[], workspaces_dir=root)[0]
    assert card["enabled"] is False and "ask in chat" in card["paused_reason"].lower()

    # A late fire (a job already in flight) neither dispatches into a loop nor queues a second card.
    stream.run(uid, REFUSED)
    _fire(client, inv)
    stream.run(uid, REFUSED)
    _fire(client, inv)
    assert len(published) == 1, "one card per pause, not one per refused run"


def test_a_successful_run_resets_the_count(rig):
    client, scheduler, stream, runtime, published, root, inv = rig
    uid = dispatch_id(inv)
    for done in (REFUSED, REFUSED, FINE, REFUSED, REFUSED):
        _fire(client, inv)
        stream.run(uid, done)
    assert _fire(client, inv)["workload_id"] == uid
    assert published == [] and len(scheduler.jobs) == 1
    assert rr.load_state(root, "7")[inv["identity"]["launcher"][9:]]["count"] == 2


def test_a_different_reason_starts_the_count_again():
    other = {**REFUSED, "refused": {"tool": "x", "reason": "out_of_scope"}}
    events = [(f"{i}-0", ev) for i, ev in enumerate((REFUSED, REFUSED, other), start=1)]
    assert rr.count_runs({}, events) == {"reason": "out_of_scope", "count": 1, "cursor": "3-0"}


def test_a_chat_or_an_event_is_never_counted_or_paused(rig):
    client, scheduler, stream, runtime, published, root, inv = rig
    reads = []
    for trigger, launcher in (("message", inv["identity"]["launcher"]),
                              ("event", "integration:gmail"), ("scheduled", "user:7")):
        other = {**inv, "trigger": trigger, "identity": {**inv["identity"], "launcher": launcher}}
        assert rr.routine_of(other) is None
        assert rr.before_dispatch(other, read_since=lambda *a: reads.append(a) or [],
                                  scheduler=scheduler, invocations_url=URL,
                                  workspaces_dir=root) is None
    assert reads == [], "nothing but a scheduler-fired routine reads a stream"


def test_an_unreadable_stream_counts_nothing_and_the_run_goes_ahead(rig):
    client, scheduler, stream, runtime, published, root, inv = rig
    assert rr.before_dispatch(inv, read_since=lambda *a: None, scheduler=scheduler,
                              invocations_url=URL, workspaces_dir=root) is None
    assert rr.load_state(root, "7") == {}


def test_switching_it_back_on_clears_the_pause(rig):
    client, scheduler, stream, runtime, published, root, inv = rig
    uid = dispatch_id(inv)
    for _ in range(3):
        _fire(client, inv)
        stream.run(uid, REFUSED)
    _fire(client, inv)
    wr.set_routine_file_enabled("7", "inbox", enabled=True, workspaces_dir=root)
    text = (root / "7" / "routines" / "inbox.md").read_text()
    assert "enabled: true" in text and "paused_reason" not in text
