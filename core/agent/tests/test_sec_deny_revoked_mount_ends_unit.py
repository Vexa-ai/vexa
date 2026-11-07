"""A removed member's unit ends (gate 0.13.4 final, car 55 row 1).

On the volume backend a worker's shared mount is a bind of the workspace's tree itself. agent-api
names the mount ``revoked`` in the unit's audience key the moment its subject loses the membership
grant (``control_plane.mount_audience.refresh``). From then on the turn must not go on writing into
that tree, what it already wrote must not stay there, and the worker must not keep holding the bind.

Driven through the real pieces: ``mount_audience`` on a fake Redis as agent-api's roster listener,
``Guard.from_env`` reading the unit's key as the worker does, ``run_harness_turn`` committing, and
``worker.engine.serve`` running the unit.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import fakeredis
import pytest

from control_plane import mount_audience
from control_plane import workspace_membership as m
from llm import private_writes
from llm.ports import run_harness_turn
from llm.private_writes import Guard
from tests.test_private_writes_commit import AUTHOR, OWNER, _log, _repo
from worker import engine

UNIT = "agent-7-chat-live"
#: Long enough that a unit which merely idles out cannot pass for one that ended.
IDLE_MS = 30_000


class _Stream:
    """The unit's Redis as ``serve`` sees it: nothing queued; an idle read waits (at most 5 s)."""

    def __init__(self):
        self.out, self.kv = [], {}

    def xrange(self, *a, **k):
        return []

    def xrevrange(self, *a, **k):
        return []

    def xread(self, streams, count=None, block=None):
        if block:
            time.sleep(min(block / 1000.0, 5.0))
        return None

    def xadd(self, topic, fields):
        self.out.append(json.loads(fields["event"]))

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v):
        self.kv[k] = v


class _Harness:
    """Writes a draft into the shared tree, then runs a silent tool until it is killed (the real
    CLI's stdout then hits EOF and nothing more is written) or ``release`` is set, when it writes a
    second file and finishes."""

    def __init__(self, shared: Path):
        self.shared = shared
        self.killed, self.release = threading.Event(), threading.Event()

    def stop_running_turn(self):
        self.killed.set()
        return True

    def run_turn(self, work, prompt, **_):
        (self.shared / "notes").mkdir(exist_ok=True)
        (self.shared / "notes" / "draft.md").write_text("draft")
        yield {"type": "tool-call", "tool": "Bash", "callId": "c1", "args": {}}
        end = time.monotonic() + 8
        while time.monotonic() < end and not self.killed.is_set() and not self.release.is_set():
            time.sleep(0.02)
        if self.killed.is_set():
            return
        (self.shared / "notes" / "after.md").write_text("written after")
        yield {"type": "tool-result", "callId": "c1", "ok": True}
        yield {"type": "done", "ok": True, "reply": "ok"}


@pytest.fixture
def unit(tmp_path, monkeypatch):
    """Unit ``agent-7-chat-live`` of person 7 mounts their desk and ``meetup`` (7 owner, 8
    contributor), published by agent-api as the dispatch does; the worker reads its key."""
    monkeypatch.setattr(engine, "STOP_POLL_S", 0.05)
    if hasattr(engine, "REVOKE_POLL_MS"):
        monkeypatch.setattr(engine, "REVOKE_POLL_MS", 50)
    root = tmp_path
    desk, shared = _repo(root / OWNER), _repo(root / "meetup")
    m.store_put(root, "meetup", "members", [{"subject": OWNER, "role": "owner"},
                                            {"subject": "8", "role": "contributor"}])
    fake = fakeredis.FakeRedis(decode_responses=True)
    mounts = [{"path": str(desk), "role": "private", "write": True},
              {"path": str(shared), "role": "shared", "write": True}]
    mount_audience.publish(fake, root, UNIT, OWNER, mounts, {str(desk): [], str(shared): []})
    m.set_roster_listener(lambda r_, ws: mount_audience.refresh(fake, r_, ws))
    private_writes.configure_worker(fake, UNIT)
    guard = Guard.from_env({"VEXA_OWNER": OWNER}, [{"path": str(desk)}, {"path": str(shared)}])
    try:
        yield root, desk, shared, guard, mounts
    finally:
        private_writes.configure_worker(None, "")
        m.set_roster_listener(None)


def _serve(stream, desk, shared, guard, mounts, harness, *, start):
    """Run the unit as the worker's ``main`` does, on a thread; returns the thread."""
    kw = {}
    if hasattr(engine, "RevokedMounts"):
        kw["revoked"] = engine.RevokedMounts(lambda: guard, lambda: mounts)

    def turn(prompt):
        return run_harness_turn(desk, prompt, harness, extra_mounts=[shared], private_guard=guard,
                                author=AUTHOR)
    th = threading.Thread(target=engine.serve, daemon=True, args=(stream,), kwargs=dict(
        in_topic="in", out_topic="out", start=start, turn=turn, idle_ms=IDLE_MS, harness=harness,
        stop_key="stop", **kw))
    th.start()
    return th


def _wait_for(pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def _remove_owner(root):
    m.write_policy(root, "meetup", "members", [{"subject": "8", "role": "owner"}])


def test_a_member_removed_while_the_turn_runs_ends_the_turn_and_the_unit(unit):
    """7 is removed from meetup while their turn runs a silent tool: the turn is killed, the draft it
    already wrote is put back out of the shared tree, nothing written after lands, and the unit ends
    without waiting to idle out."""
    root, desk, shared, guard, mounts = unit
    stream, harness = _Stream(), _Harness(shared)
    th = _serve(stream, desk, shared, guard, mounts, harness, start={"entrypoint": {"inline": "go"}})
    draft = shared / "notes" / "draft.md"
    assert _wait_for(draft.exists, 5), "the turn never wrote"

    _remove_owner(root)
    assert _wait_for(lambda: harness.killed.is_set() and not draft.exists() and not th.is_alive(), 3), (
        f"3 s after the removal: turn killed={harness.killed.is_set()}, draft still in the shared "
        f"tree={draft.exists()}, unit still running={th.is_alive()}")
    th.join(10)
    assert not (shared / "notes" / "after.md").exists()
    assert _log(shared) == 1, "nothing committed into meetup"
    refused = [e for e in stream.out if e.get("type") == "write-refused"]
    assert refused and refused[0]["paths"] == ["notes/draft.md"], stream.out
    assert {"type": "turn-truncated", "turn_id": "t0",
            "reason": getattr(engine, "REVOKED_REASON", None)} in stream.out
    assert stream.out[-1]["type"] == "turn-complete"


def test_an_idle_unit_ends_when_its_subject_is_removed(unit):
    """No turn is running: the unit still ends at once, and whatever its worker left uncommitted in
    the shared tree is put back."""
    root, desk, shared, guard, mounts = unit
    stream, harness = _Stream(), _Harness(shared)
    th = _serve(stream, desk, shared, guard, mounts, harness, start={})
    stray = shared / "stray.md"
    stray.write_text("left by a background job")
    time.sleep(0.2)
    assert th.is_alive()

    _remove_owner(root)
    assert _wait_for(lambda: not th.is_alive(), 3), "the idle unit kept its mount after the removal"
    assert not stray.exists()
    assert _log(shared) == 1


def test_a_member_still_on_the_roster_keeps_running_and_commits(unit):
    """Control: the roster changes (8 leaves) but 7 stays: the turn is not stopped, both files are
    committed into meetup, and the unit goes on serving until it idles out."""
    root, desk, shared, guard, mounts = unit
    stream, harness = _Stream(), _Harness(shared)
    th = _serve(stream, desk, shared, guard, mounts, harness, start={"entrypoint": {"inline": "go"}})
    assert _wait_for((shared / "notes" / "draft.md").exists, 5)

    m.write_policy(root, "meetup", "members", [{"subject": OWNER, "role": "owner"}])
    time.sleep(0.5)
    assert not harness.killed.is_set()
    harness.release.set()
    assert _wait_for(lambda: any(e.get("type") == "turn-complete" for e in stream.out), 5)
    assert not harness.killed.is_set()
    assert _log(shared) == 2
    out = subprocess.run(["git", "-C", str(shared), "show", "--name-only", "--format=", "HEAD"],
                         capture_output=True, text=True, check=True).stdout.split()
    assert sorted(out) == ["notes/after.md", "notes/draft.md"]
    assert not [e for e in stream.out if e.get("type") in ("write-refused", "turn-truncated")]
    time.sleep(0.3)
    assert th.is_alive(), "a unit whose subject is still a member ended"
    _remove_owner(root)                                   # end the unit
    th.join(6)


def test_a_dispatch_that_no_longer_mounts_a_revoked_workspace_keeps_it_revoked(unit):
    """The next message after the removal is dispatched before the old worker looked: the new
    dispatch no longer mounts meetup, and the key still names meetup revoked, so the worker that
    holds it ends instead of running that message on the old bind. Mounting it again (7 re-added)
    clears the mark."""
    from shared import audience
    root, desk, shared, guard, mounts = unit
    _remove_owner(root)
    assert guard.revoked_mounts([shared]) == [shared]

    fake = private_writes._WORKER_STORE[0][0]
    mount_audience.publish(fake, root, UNIT, OWNER, mounts[:1], {str(desk): []})
    assert guard.revoked_mounts([desk, shared]) == [shared], audience.decode(fake.get(audience.unit_key(UNIT)))
    m.write_policy(root, "meetup", "members", [{"subject": "8", "role": "owner"},
                                               {"subject": "9", "role": "viewer"}])
    assert guard.revoked_mounts([shared]) == [shared], "a later roster change cleared the mark"

    m.write_policy(root, "meetup", "members", [{"subject": "8", "role": "owner"},
                                               {"subject": OWNER, "role": "contributor"}])
    mount_audience.publish(fake, root, UNIT, OWNER, mounts, {str(desk): [], str(shared): ["8"]})
    assert guard.revoked_mounts([desk, shared]) == []
