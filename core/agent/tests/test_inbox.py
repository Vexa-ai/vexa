"""THE CHAT'S INBOX — everything submitted is on the server, and everything on the server runs.

`Vexa-ai/vexa#1610`. The founder, dropping several Extend acts with their own instruction lines onto
one page while a job ran (the chat answered *"There is already something running on
oenb-b5e60c/README.md — I'll finish that one first"* twice):

> *"i drop new tasks to that chat, can i be sure everything submitted there is actually processed?"*

Two ways the answer was no, and these pin both shut:

  1. **A submission lived in one browser.** A message typed mid-turn sat in that tab's localStorage
     until the turn ended — another device never saw it, a cleared browser never sent it, and
     nothing anywhere recorded that it existed. Now every submission is XADD'd onto the session's
     in-topic at once and the pending list is READ BACK from the server, so a reload, a second
     device and a swapped container all count the same queue.
  2. **A second act on one page was refused.** Its instruction line was then held by nobody. Now
     same-target acts queue, in press order, each running its own brief — and only an identical
     press collapses into the one in flight, saying so.

The runner half of (2) lives in `test_jobs.py` (it is the file that owns `JobRunner`); what is here
is the INBOX: the loop draining it, the cursor that says how far it has been drained, and the two
routes that put it in front of a person.
"""
from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from control_plane import api_shared
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared import units
from shared.config import load_settings
from shared.marks import job_mark, read_job_mark
from worker.worker import serve

from .test_worker import CursorStream


# ── the inbox is a stream AND a key, so the fake has to be a real redis ─────────────────────────

@pytest.fixture
def fake_redis(monkeypatch):
    """One `fakeredis` behind every `redis.from_url` in the process.

    A hand-rolled double will not do here: the inbox spans the in-topic (a stream), the out-topic
    (another) and the worker's cursor (a key), and the reader asks for a RANGE from that cursor —
    the exact place a home-made id comparison would agree with the test and disagree with redis."""
    import fakeredis
    import redis as redis_mod

    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis_mod, "from_url", lambda *_a, **_k: r)
    return r


class _FakeRuntime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "running"        # WARM: the pre-delivery is the only delivery, as in production


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


class _FakeReader:
    def read(self, unit_id, resume=None):
        yield {"type": "turn-complete"}


_EXTEND_PRESET = "---\nlabel: extend\n---\n[extend] Go further on {{path}}.\n"


@pytest.fixture
def client(tmp_path, monkeypatch, fake_redis):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "workspaces"
    (root / "_global" / "asks").mkdir(parents=True)
    (root / "_global" / "asks" / "extend.md").write_text(_EXTEND_PRESET)
    settings = load_settings(workspaces_dir=str(root),
                             global_system_workspace_path=str(root / "_global"),
                             internal_api_secret="s", ui_url="https://app.example.test",
                             redis_url="redis://fake")
    app = create_app(Dispatcher(settings, _FakeRuntime(), _FakeIdentity(), warm_stream=fake_redis),
                     stream_reader=_FakeReader(), reader=WorkspaceReader(str(root)),
                     redis_url="redis://fake")
    return TestClient(app)


_HEADERS = {"X-User-Id": "u1", "X-User-Email": "a@b.test"}
_UNIT = units.chat_unit_id("u1", "main")


def _submit(client, **body):
    return client.post("/api/chat/submit", headers=_HEADERS, json={"session": "main", **body})


# ── the submission is on the server the moment it is submitted ─────────────────────────────────

def test_a_submission_lands_on_the_server_at_once_and_comes_back_as_a_pending_row(client, fake_redis):
    r = _submit(client, prompt="and share it with dmitry@vexa.ai", turn_id="c-1")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["id"] == "c-1"

    # ON THE WIRE, NOT IN A TAB: the words are in the session's in-topic before the response returns.
    entries = fake_redis.xrange(units.input_topic(_UNIT))
    assert len(entries) == 1
    assert "and share it with dmitry@vexa.ai" in json.loads(entries[0][1]["turn"])["prompt"]

    # …and the answer carries the server's own view of what is queued.
    assert [(p["id"], p["display"]) for p in body["pending"]] == [
        ("c-1", "and share it with dmitry@vexa.ai")]


def test_an_act_submitted_mid_turn_is_marked_as_a_job_and_named_in_its_row(client):
    """The submit route runs the SAME composition `/api/chat` runs — one pipeline, not two."""
    body = _submit(client, prompt="Extend: kg/plan.md", turn_id="c-2",
                   intent={"kind": "extend", "path": "kg/plan.md", "workspace": "desk"}).json()
    prompt = client.app.state.dispatcher.dispatched[-1]["start"]["entrypoint"]["inline"]
    assert "[extend] Go further on kg/plan.md." in prompt          # the admin's words still won
    assert read_job_mark(prompt)[:2] == ("extend", "desk/kg/plan.md")
    row = body["pending"][0]
    assert (row["kind"], row["target"]) == ("extend", "desk/kg/plan.md")


def test_the_pending_list_is_the_same_from_anywhere(client):
    """A reload, a second device, a swapped terminal container: none of them is remembering it, so
    all of them read the same list."""
    _submit(client, prompt="one", turn_id="c-1")
    _submit(client, prompt="two", turn_id="c-2")
    seen = client.get("/api/chat/pending", headers=_HEADERS, params={"session": "main"}).json()
    assert [p["id"] for p in seen["pending"]] == ["c-1", "c-2"]
    # a second read (a reload) is identical — the list is derived, never stored by the reader
    again = client.get("/api/chat/pending", headers=_HEADERS, params={"session": "main"}).json()
    assert again["pending"] == seen["pending"]


def test_a_row_stops_being_pending_when_the_worker_takes_it(client, fake_redis):
    _submit(client, prompt="one", turn_id="c-1")
    _submit(client, prompt="two", turn_id="c-2")
    entries = fake_redis.xrange(units.input_topic(_UNIT))
    # the worker takes the first entry and says so — its cursor is the one writer of this fact
    fake_redis.set(units.inbox_cursor_key(_UNIT), entries[0][0])
    seen = client.get("/api/chat/pending", headers=_HEADERS, params={"session": "main"}).json()
    assert [p["id"] for p in seen["pending"]] == ["c-2"]


def test_what_the_inbox_view_refuses_to_call_pending(fake_redis):
    """Two filters, both about not inventing a queue nobody is in: an entry from a build that had no
    inbox, and an entry so old that the only explanation is a worker that never ran."""
    topic = units.input_topic("u-x")
    fake_redis.xadd(topic, {"turn": json.dumps({"type": "message", "prompt": "old build"})})
    fake_redis.xadd(topic, {"turn": json.dumps({"type": "message", "prompt": "stale", "inbox": {
        "id": "c-old", "display": "stale", "at": time.time() - api_shared.INBOX_PENDING_MAX_AGE_SEC - 60}})})
    fake_redis.xadd(topic, {"turn": json.dumps({"type": "message", "prompt": "live", "inbox": {
        "id": "c-new", "display": "live", "at": time.time()}})})
    assert [p["id"] for p in api_shared.inbox_pending("redis://fake", "u-x")] == ["c-new"]


def test_no_redis_is_an_empty_inbox_and_never_an_error():
    """A chat that cannot read its inbox shows what it showed before one existed. It must never be
    the thing that fails the surface asking it."""
    assert api_shared.inbox_pending("", "u-x") == []
    assert api_shared.inbox_pending(None, "u-x") == []


def test_a_submission_never_moves_the_streaming_turn_s_head(client, fake_redis):
    """The turn-head record means "the turn currently being STREAMED", and a submission is by
    definition not that. Overwriting it would leave the streamed turn's own no-cursor retry unable
    to recognise itself — and a retry that does not recognise itself dispatches a second copy."""
    client.post("/api/chat", headers=_HEADERS,
                json={"prompt": "watch this one", "session": "main", "turn_id": "watched"})
    head = json.loads(fake_redis.get(api_shared._chat_turn_head_key(_UNIT)))
    _submit(client, prompt="and this one later", turn_id="submitted")
    assert json.loads(fake_redis.get(api_shared._chat_turn_head_key(_UNIT))) == head


# ── the loop draining it ────────────────────────────────────────────────────────────────────────

class CursorStreamWithKeys(CursorStream):
    """`CursorStream` plus the one key the worker writes — how far it has read its inbox."""

    def __init__(self, preloaded=None):
        super().__init__(preloaded)
        self.kv: dict[str, str] = {}

    def set(self, key, value, ex=None):
        self.kv[key] = value


def _entry(eid, prompt, **meta):
    return (eid, {"turn": json.dumps({"prompt": prompt, "inbox": meta})})


def _act(eid, kind, target, line):
    return _entry(eid, job_mark(kind, target) + line, id=eid, kind=kind, target=target)


def test_ten_submissions_during_a_job_all_execute_in_order(tmp_path):
    """THE ACCEPTANCE. Ten things dropped into one chat while a job runs — ordinary messages, acts
    on the same page, acts on another page — and all ten execute, in the order they were submitted,
    each act running its OWN instruction line. Nothing refused, nothing merged, nothing lost."""
    turn_gate = threading.Event()
    s = CursorStreamWithKeys(preloaded=[])
    said: list[str] = []
    briefs: list[str] = []

    # what a person drops into the chat while the first turn is still answering
    submissions = [
        _act("6-0", "extend", "desk/plan.md", "sharpen the risks"),
        _entry("7-0", "and what about pricing?", id="m1"),
        _act("8-0", "extend", "desk/plan.md", "add the timeline"),
        _act("9-0", "create", "desk/other.md", "start the other one"),
        _entry("10-0", "who is Acme?", id="m2"),
        _act("11-0", "extend", "desk/plan.md", "name the owners"),
        _entry("12-0", "and their renewal date", id="m3"),
        _act("13-0", "extend", "desk/other.md", "extend that one too"),
        _act("14-0", "extend", "desk/plan.md", "one last pass"),
        _entry("15-0", "thanks", id="m4"),
    ]

    def chat(prompt):
        said.append(prompt)
        if prompt == "hello":
            s.entries.extend(submissions)      # they all land mid-turn, as the founder's did
        yield {"type": "message-delta", "text": "thinking"}
        if prompt == "hello":
            turn_gate.wait(5)
        yield {"type": "done", "reply": "answered", "sessionId": "s", "ok": True}

    lock = threading.Lock()

    def jobturn(brief):
        with lock:
            briefs.append(brief)
        yield {"type": "done", "reply": "ok", "sessionId": "s", "ok": True}

    t = threading.Thread(target=serve, kwargs=dict(
        stream=s, out_topic="o", in_topic="i", turn=chat, job=jobturn,
        jobs_dir=tmp_path / "jobs", inbox_cursor="unit:u:cursor",
        start={"entrypoint": {"inline": "hello"}}, idle_ms=10))
    t.start()
    turn_gate.set()
    t.join(30)
    assert not t.is_alive()

    evs = s.events()
    # EVERY ORDINARY MESSAGE RAN, IN ORDER, as its own turn.
    assert said == ["hello", "and what about pricing?", "who is Acme?",
                    "and their renewal date", "thanks"]
    # EVERY ACT RAN — six of them, none refused, each on its own instruction line.
    assert sorted(briefs) == sorted([
        "sharpen the risks", "add the timeline", "start the other one",
        "name the owners", "extend that one too", "one last pass"])
    assert not [e for e in evs if e["type"] == "job-refused"]
    assert not [e for e in evs if e["type"] == "job-collapsed"]
    assert len([e for e in evs if e["type"] == "job-done"]) == 6
    # …and the four acts on ONE page ran in press order, one at a time.
    plan = [b for b in briefs if b in ("sharpen the risks", "add the timeline",
                                       "name the owners", "one last pass")]
    assert plan == ["sharpen the risks", "add the timeline", "name the owners", "one last pass"]
    # THE INBOX IS EMPTY AND SAYS SO: the cursor the worker published is the last entry it took.
    assert s.kv["unit:u:cursor"] == "15-0"


def test_the_worker_publishes_its_cursor_as_it_takes_each_entry(tmp_path):
    """The pending list is "everything after this cursor", so a cursor that lags is a chat claiming
    to be behind when it is not."""
    s = CursorStreamWithKeys(preloaded=[])
    seen: list[str] = []

    def chat(prompt):
        seen.append(prompt)
        if prompt == "hello":
            s.entries.append(_entry("6-0", "first", id="m1"))
            s.entries.append(_entry("7-0", "second", id="m2"))
        # the cursor already names the entry being run — never the one behind it
        yield {"type": "message-delta", "text": s.kv.get("unit:u:cursor", "-")}

    serve(s, out_topic="o", in_topic="i", turn=chat, inbox_cursor="unit:u:cursor",
          start={"entrypoint": {"inline": "hello"}}, idle_ms=10)
    assert seen == ["hello", "first", "second"]
    marks = [e["text"] for e in s.events() if e["type"] == "message-delta"]
    assert marks == ["-", "6-0", "7-0"]
    assert s.kv["unit:u:cursor"] == "7-0"


def test_a_stream_that_cannot_hold_a_key_still_serves_every_turn(tmp_path):
    """The inbox VIEW is furniture; the turn is what the person is waiting for. A stream with no
    `set` (every fake in this repo, and any deployment that has not taken the key) runs byte-for-byte
    as it did before."""
    s = CursorStream(preloaded=[])

    def chat(prompt):
        if prompt == "hello":
            s.entries.append(_entry("6-0", "and this", id="m1"))
        yield {"type": "message-delta", "text": f"re:{prompt}"}

    serve(s, out_topic="o", in_topic="i", turn=chat, inbox_cursor="unit:u:cursor",
          start={"entrypoint": {"inline": "hello"}}, idle_ms=10)
    assert [e["type"] for e in s.events()] == [
        "turn-accepted", "message-delta", "turn-complete",
        "turn-accepted", "message-delta", "turn-complete"]
