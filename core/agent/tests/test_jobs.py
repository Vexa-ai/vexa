"""BACKGROUND JOBS — the contract, over a fake stream and a fake turn (no docker, no model).

`Vexa-ai/vexa#1584`: a long act must not hold the chat. These pin the five promises the contract
makes, because each one fails silently if it breaks — a spawn that blocks looks exactly like a slow
turn, a completion that never posts looks exactly like a job still running, and a duplicate that is
quietly queued looks exactly like a job that is taking a while.

  1. the spawn RETURNS AT ONCE — the turn is complete before the job's own turn has produced anything
  2. progress carries the JOB id and never the turn id
  3. completion posts one line
  4. failure posts one line (never silence)
  5. a second job on the same target is refused, with a reason

plus the two seams around them: the mark the control plane writes is the mark the worker reads, and
a restart tells the chat about the jobs it killed.
"""
from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient

from control_plane import chat_intents
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from shared.marks import job_mark, read_job_mark
from worker.jobs import JobRunner
from worker.worker import serve

from .test_worker import FakeStream, _msg


# ── the mark: one writer, one reader ─────────────────────────────────────────────────────────────

def test_the_mark_the_router_writes_is_the_mark_the_worker_reads():
    prefix = chat_intents.job_prefix({"kind": "extend", "workspace": "desk", "path": "kg/plan.md"})
    # The grounding and the context sentinel are prepended BEFORE the worker sees the string, so the
    # mark rides mid-prompt by construction — reading it must not depend on where it sits.
    composed = "grounding block\n\n" + prefix + "Extend the page."
    read = read_job_mark(composed)
    assert read is not None
    kind, target, rest = read
    assert (kind, target) == ("extend", "desk/kg/plan.md")
    assert rest == "grounding block\n\nExtend the page."   # stripped in place, nothing else moved


def test_only_the_closed_set_of_kinds_becomes_a_job():
    assert chat_intents.job_prefix({"kind": "create", "path": "a.md"}).startswith("[vexa-job:")
    assert chat_intents.job_prefix({"kind": "explore", "term": "x"}) == ""
    assert chat_intents.job_prefix({"kind": "highlight"}) == ""
    assert chat_intents.job_prefix(None) == ""
    # a kind the wire invented is not a job (and is not guessed into one)
    assert chat_intents.job_prefix({"kind": "rm -rf", "path": "a.md"}) == ""


# ── the route: what the worker is actually told ──────────────────────────────────────────────────

_EXTEND_PRESET = "---\nlabel: extend\n---\n[extend] Go further on {{path}}.\n"


class _FakeRuntime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


class _FakeReader:
    def read(self, unit_id, resume=None):
        yield {"type": "turn-complete"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "workspaces"
    (root / "_global" / "asks").mkdir(parents=True)
    (root / "_global" / "asks" / "extend.md").write_text(_EXTEND_PRESET)
    settings = load_settings(workspaces_dir=str(root),
                             global_system_workspace_path=str(root / "_global"),
                             internal_api_secret="s", ui_url="https://app.example.test",
                             redis_url="")
    app = create_app(Dispatcher(settings, _FakeRuntime(), _FakeIdentity()),
                     stream_reader=_FakeReader(), reader=WorkspaceReader(str(root)))
    return TestClient(app)


def _dispatched_prompt(client) -> str:
    return client.app.state.dispatcher.dispatched[-1]["start"]["entrypoint"]["inline"]


def test_an_extend_press_reaches_the_worker_marked_as_a_job(client):
    r = client.post("/api/chat", headers={"X-User-Id": "u1", "X-User-Email": "a@b.test"},
                    json={"prompt": "Extend: kg/plan.md", "session": "main",
                          "intent": {"kind": "extend", "path": "kg/plan.md", "workspace": "desk"}})
    assert r.status_code == 200
    prompt = _dispatched_prompt(client)
    assert "[extend] Go further on kg/plan.md." in prompt      # the admin's words still won
    assert read_job_mark(prompt)[:2] == ("extend", "desk/kg/plan.md")


def test_the_mark_does_not_depend_on_the_preset_library_being_current(client):
    """`create.md` is deliberately absent from this fixture: the turn falls back to the client's
    plainer sentence — and that sentence takes exactly as long to run, so it is still a job."""
    r = client.post("/api/chat", headers={"X-User-Id": "u1", "X-User-Email": "a@b.test"},
                    json={"prompt": "Create: kg/new.md", "session": "main",
                          "intent": {"kind": "create", "path": "kg/new.md"}})
    assert r.status_code == 200
    prompt = _dispatched_prompt(client)
    assert read_job_mark(prompt)[:2] == ("create", "kg/new.md")
    assert "Create: kg/new.md" in read_job_mark(prompt)[2]


def test_an_ordinary_message_reaches_the_worker_unmarked(client):
    r = client.post("/api/chat", headers={"X-User-Id": "u1", "X-User-Email": "a@b.test"},
                    json={"prompt": "what is on today?", "session": "main"})
    assert r.status_code == 200
    assert read_job_mark(_dispatched_prompt(client)) is None


# ── the runner ───────────────────────────────────────────────────────────────────────────────────

class _Sink:
    def __init__(self):
        self.events: list[dict] = []
        self._lock = threading.Lock()

    def __call__(self, ev: dict) -> None:
        with self._lock:
            self.events.append(ev)

    def types(self) -> list[str]:
        return [e["type"] for e in self.events]


def _slow_turn(gate: threading.Event):
    def turn(_brief):
        gate.wait(5)
        yield {"type": "tool-call", "tool": "Write", "args": {}, "callId": "c1"}
        yield {"type": "commit", "sha": "abc123"}
        yield {"type": "done", "reply": "wrote it", "sessionId": "s", "ok": True}
    return turn


def test_spawn_returns_at_once_and_the_job_carries_its_own_id(tmp_path):
    gate = threading.Event()
    sink = _Sink()
    runner = JobRunner(emit=sink, turn=_slow_turn(gate), register_dir=tmp_path / "jobs")

    started = runner.spawn("extend", "kg/plan.md", "Extend it.", turn_id="t1")

    # 1. AT ONCE: spawn has returned while the job's turn is still blocked on the gate.
    assert started["type"] == "job-started"
    assert started["turn_id"] == "t1" and started["job_id"].startswith("j-")
    assert "I'll say when it's there" in started["line"]
    assert sink.types() == ["job-started"]
    assert runner.busy() is True

    gate.set()
    runner.join_all()

    # 2. every event the job's turn produced carries the JOB id and NO turn id.
    job_id = started["job_id"]
    progress = [e for e in sink.events if e["type"] in ("tool-call", "commit", "done")]
    assert progress and all(e["job_id"] == job_id and "turn_id" not in e for e in progress)
    # 3. completion posts exactly one line.
    ends = [e for e in sink.events if e["type"] in ("job-done", "job-failed")]
    assert len(ends) == 1 and ends[0]["type"] == "job-done"
    assert ends[0]["line"] == "kg/plan.md — extended."
    assert runner.busy() is False
    # the register is empty again — a finished job leaves nothing for the next boot to report
    assert list((tmp_path / "jobs").glob("*.json")) == []


def test_a_job_that_fails_says_so(tmp_path):
    def boom(_brief):
        yield {"type": "tool-call", "tool": "Read", "args": {}, "callId": "c1"}
        raise RuntimeError("the endpoint refused")

    sink = _Sink()
    runner = JobRunner(emit=sink, turn=boom, register_dir=tmp_path / "jobs")
    runner.spawn("create", "kg/new.md", "Write it.")
    runner.join_all()

    ends = [e for e in sink.events if e["type"] in ("job-done", "job-failed")]
    assert len(ends) == 1 and ends[0]["type"] == "job-failed"
    assert "kg/new.md failed" in ends[0]["line"] and "the endpoint refused" in ends[0]["line"]
    assert runner.busy() is False


def test_a_turn_that_ends_not_ok_is_a_failed_job(tmp_path):
    def truncated(_brief):
        yield {"type": "done", "reply": "half", "sessionId": "s", "ok": False,
               "reason": "the turn stopped early: tool-call budget"}

    sink = _Sink()
    runner = JobRunner(emit=sink, turn=truncated, register_dir=tmp_path / "jobs")
    runner.spawn("extend", "a.md", "go")
    runner.join_all()
    end = [e for e in sink.events if e["type"] in ("job-done", "job-failed")][0]
    assert end["type"] == "job-failed" and "tool-call budget" in end["line"]


def test_a_second_job_on_the_same_target_is_refused_with_a_reason(tmp_path):
    gate = threading.Event()
    sink = _Sink()
    runner = JobRunner(emit=sink, turn=_slow_turn(gate), register_dir=tmp_path / "jobs")

    first = runner.spawn("extend", "kg/plan.md", "Extend it.")
    second = runner.spawn("extend", "kg/plan.md", "Extend it again.")
    other = runner.spawn("extend", "kg/other.md", "A different page.")

    assert second["type"] == "job-refused" and "job_id" not in second
    assert "already something running on kg/plan.md" in second["line"]
    # a DIFFERENT page runs concurrently — several jobs at once is the normal case
    assert other["type"] == "job-started" and other["job_id"] != first["job_id"]

    gate.set()
    runner.join_all()
    # …and once it has landed the same page can be asked for again
    assert runner.spawn("extend", "kg/plan.md", "again")["type"] == "job-started"
    gate.set()
    runner.join_all()


def test_a_restart_cancels_every_job_and_tells_the_chat(tmp_path):
    gate = threading.Event()
    dead = _Sink()
    runner = JobRunner(emit=dead, turn=_slow_turn(gate), register_dir=tmp_path / "jobs")
    started = runner.spawn("create", "kg/new.md", "Write it.")
    assert (tmp_path / "jobs" / f"{started['job_id']}.json").exists()

    # the process dies here — the thread goes with it and nothing removes the register entry.
    fresh = _Sink()
    reborn = JobRunner(emit=fresh, turn=_slow_turn(threading.Event()), register_dir=tmp_path / "jobs")
    reported = reborn.cancelled_at_boot()

    assert [e["type"] for e in fresh.events] == ["job-failed"]
    assert reported[0]["job_id"] == started["job_id"]
    assert "stopped when the agent restarted" in fresh.events[0]["line"]
    # reported ONCE: the register is cleared, so a second boot says nothing
    assert reborn.cancelled_at_boot() == []

    gate.set()
    runner.join_all()


def test_no_register_dir_is_a_working_runner_that_reports_nothing_at_boot():
    sink = _Sink()
    runner = JobRunner(emit=sink, turn=lambda _b: iter([]), register_dir=None)
    assert runner.cancelled_at_boot() == []
    runner.spawn("create", "a.md", "go")
    runner.join_all()
    assert sink.types()[-1] == "job-done"


# ── the serve loop ───────────────────────────────────────────────────────────────────────────────

def _chat_turn(prompt):
    yield {"type": "message-delta", "text": f"re:{prompt}"}


def test_a_marked_act_completes_the_turn_without_running_it(tmp_path):
    """The whole point, at the loop level: the turn that carries the mark never touches the chat's
    turn function — it acknowledges and completes, and the work happens on the job's thread."""
    gate = threading.Event()
    ran: list[str] = []

    def chat(prompt):
        ran.append(prompt)
        yield from _chat_turn(prompt)

    def jobturn(brief):
        ran.append(f"job:{brief}")
        gate.wait(5)
        yield {"type": "done", "reply": "ok", "sessionId": "s", "ok": True}

    prompt = "context\n" + job_mark("create", "kg/new.md") + "Write the page."
    s = FakeStream(inbox=[_msg("1-0", prompt), ("2-0", {"turn": json.dumps({"type": "stop"})})])
    t = threading.Thread(target=serve, kwargs=dict(
        stream=s, out_topic="o", in_topic="i", turn=chat, job=jobturn,
        jobs_dir=tmp_path / "jobs", start={}, idle_ms=10))
    t.start()
    # the turn completes while the job is still blocked — that is "returns immediately"
    for _ in range(500):
        if any(e["type"] == "turn-complete" for e in s.events()):
            break
        threading.Event().wait(0.01)
    evs = s.events()
    assert [e["type"] for e in evs] == ["turn-accepted", "job-started", "message-delta",
                                        "turn-complete"]
    assert evs[2]["text"] == "Writing kg/new.md — I'll say when it's there."
    assert evs[2]["turn_id"] == "t1" and evs[1]["turn_id"] == "t1"
    assert ran == ["job:context\nWrite the page."]   # the CHAT turn function was never called

    gate.set()
    t.join(10)
    assert not t.is_alive()
    assert [e["type"] for e in s.events()][-1] == "job-done"


def test_an_unmarked_turn_is_untouched_by_the_job_machinery(tmp_path):
    """Every ordinary turn still runs exactly as before — the blocking path is the default."""
    s = FakeStream(inbox=[_msg("1-0", "hello")])
    serve(s, out_topic="o", in_topic="i", turn=_chat_turn, job=lambda _b: iter([]),
          jobs_dir=tmp_path / "jobs", start={}, idle_ms=10)
    assert [e["type"] for e in s.events()] == ["turn-accepted", "message-delta", "turn-complete"]


def test_the_spawn_job_tool_reaches_the_same_runner_and_the_same_refusal(tmp_path):
    """`spawn_job` (the openai-agent builtin) and a marked act are ONE mechanism, not two."""
    from llm import jobs as llm_jobs
    from llm.openai_agent import BUILTIN_SPECS, _Sandbox, run_builtin

    gate = threading.Event()

    def jobturn(_brief):
        gate.wait(5)
        yield {"type": "done", "reply": "ok", "sessionId": "s", "ok": True}

    s = FakeStream(inbox=[])
    t = threading.Thread(target=serve, kwargs=dict(
        stream=s, out_topic="o", in_topic="i", turn=_chat_turn, job=jobturn,
        jobs_dir=tmp_path / "jobs", start={}, idle_ms=10))
    t.start()
    t.join(5)   # no inbox → idle → serve returns, but the spawner it installed stays wired

    assert "spawn_job" in BUILTIN_SPECS and llm_jobs.configured() is True
    box = _Sandbox([tmp_path])
    ok, text = run_builtin("spawn_job", {"kind": "research", "target": "Acme",
                                         "brief": "Find out who they are."}, box)
    assert ok is True and "background job" in text
    ok2, text2 = run_builtin("spawn_job", {"kind": "research", "target": "Acme",
                                           "brief": "again"}, box)
    assert ok2 is False and "already something running on Acme" in text2
    # a brief-less call is refused before anything is started — the job cannot see the conversation
    assert run_builtin("spawn_job", {"kind": "research", "target": "Other", "brief": ""}, box)[0] is False

    gate.set()
    for _ in range(500):
        if any(e["type"] == "job-done" for e in s.events()):
            break
        threading.Event().wait(0.01)
    assert any(e["type"] == "job-done" for e in s.events())
    llm_jobs.set_spawner(None)


def test_the_tool_is_not_attached_when_nothing_can_run_a_job():
    """The harness's own rule: a tool it cannot serve is not offered. Advertising `spawn_job` with
    no runner behind it teaches the model that backgrounding does not work."""
    from llm import jobs as llm_jobs
    from llm.openai_agent import _attached

    llm_jobs.set_spawner(None)
    assert _attached("spawn_job") is False
    llm_jobs.set_spawner(lambda _k, _t, _b: (True, "ok"))
    assert _attached("spawn_job") is True
    llm_jobs.set_spawner(None)


# ── the relay keeps the view open for the job ────────────────────────────────────────────────────

def test_the_stream_view_stays_open_until_the_job_it_watched_start_is_done():
    """`turn-complete` used to be the whole answer to "is this view finished". A job outlives its
    turn by construction, so closing there would drop every one of its events."""
    import shared.adapters as adapters

    frames = [
        {"type": "turn-accepted", "turn_id": "t1"},
        {"type": "job-started", "job_id": "j-1", "turn_id": "t1"},
        {"type": "turn-complete", "turn_id": "t1"},
        {"type": "tool-call", "tool": "Write", "job_id": "j-1"},
        {"type": "job-done", "job_id": "j-1", "line": "a.md — written."},
        {"type": "message-delta", "text": "never read", "turn_id": "t2"},
    ]

    class _FakeRedis:
        def __init__(self):
            self.i = 0

        def xread(self, streams, count=50, block=None):
            if self.i >= len(frames):
                return []
            out = [("t", [(f"{self.i}-0", {"event": json.dumps(frames[self.i])})])]
            self.i += 1
            return out

    class _FakeModule:
        @staticmethod
        def from_url(_url, decode_responses=True):
            return _FakeRedis()

    import sys
    saved = sys.modules.get("redis")
    sys.modules["redis"] = _FakeModule
    try:
        got = [ev for ev, _id in adapters.RedisStreamReader("redis://x").read("u")]
    finally:
        if saved is None:
            sys.modules.pop("redis", None)
        else:
            sys.modules["redis"] = saved

    assert [e["type"] for e in got] == ["turn-accepted", "job-started", "turn-complete",
                                        "tool-call", "job-done"]


def test_a_view_with_no_job_still_closes_on_turn_complete():
    """The other half of the same change: with no job in play the reader behaves exactly as before."""
    import sys

    import shared.adapters as adapters

    frames = [{"type": "message-delta", "text": "hi", "turn_id": "t1"},
              {"type": "turn-complete", "turn_id": "t1"},
              {"type": "message-delta", "text": "never read", "turn_id": "t2"}]

    class _FakeRedis:
        def __init__(self):
            self.i = 0

        def xread(self, streams, count=50, block=None):
            if self.i >= len(frames):
                return []
            out = [("t", [(f"{self.i}-0", {"event": json.dumps(frames[self.i])})])]
            self.i += 1
            return out

    class _FakeModule:
        @staticmethod
        def from_url(_url, decode_responses=True):
            return _FakeRedis()

    saved = sys.modules.get("redis")
    sys.modules["redis"] = _FakeModule
    try:
        got = [ev for ev, _id in adapters.RedisStreamReader("redis://x").read("u")]
    finally:
        if saved is None:
            sys.modules.pop("redis", None)
        else:
            sys.modules["redis"] = saved

    assert [e["type"] for e in got] == ["message-delta", "turn-complete"]
