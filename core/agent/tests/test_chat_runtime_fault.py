"""P18 on the chat path — a runtime that refuses the spawn is a typed refusal, never a bare 500.

The 0.13.2 demo stack. The person sent "hi"; the runtime refused the worker spawn (kubectl
`AlreadyExists`); agent-api's `urllib` raised `HTTPError 502` with nothing to catch it; the chat
showed "Internal Server Error" while the message sat "queued behind the current turn" for a worker
nobody had started. The founder: *"this fails our 'fail loud' principle"*.

What is pinned here, through the real app, the real dispatcher and the real runtime adapter (only
`urlopen` is faked — it answers the way the demo stack's runtime answered):

  * `/api/chat` and `/api/chat/submit` answer 502/503 with the TYPED fault (`source: runtime`,
    `kind`, a safe `detail`, a `remedy`) — no 500, no "Internal Server Error", no kubectl line;
  * the words the dispatch pre-delivered are withdrawn, so a retry runs the turn once;
  * the fault is published where the chat reads it: an attach to that chat gets an `error` event
    carrying it, and anything still queued is listed `blocked` by it — until the worker moves;
  * every other dispatch door answers the same typed fault (the app-level floor).
"""
from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request

import pytest
from fastapi.testclient import TestClient

from control_plane import unit_faults
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared import unit_input, units
from shared.adapters import RuntimeHttpClient
from shared.config import load_settings

_HEADERS = {"X-User-Id": "u1", "X-User-Email": "a@b.test"}
_UNIT = units.chat_unit_id("u1", "main")
_ALREADY_EXISTS = ('kubectl create -f - -n vexa-agents failed: Error from server (AlreadyExists): '
                   'error when creating "STDIN": pods "agent-u1-main" already exists')


@pytest.fixture
def fake_redis(monkeypatch):
    import fakeredis
    import redis as redis_mod

    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis_mod, "from_url", lambda *_a, **_k: r)
    return r


@pytest.fixture
def runtime_answers(monkeypatch):
    """What the runtime answers POST /workloads with: an HTTP status + body, or a transport error."""
    answer: dict = {"status": 502, "body": {"detail": _ALREADY_EXISTS}}

    def urlopen(req, timeout=None):
        if answer.get("raise") is not None:
            raise answer["raise"]
        status = answer["status"]
        raw = json.dumps(answer["body"]).encode()
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(raw))

        class _R(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _R(raw)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return answer


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


class _Reader:
    def read(self, unit_id, resume=None):
        yield {"type": "turn-complete"}


@pytest.fixture
def client(tmp_path, monkeypatch, fake_redis, runtime_answers):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    settings = load_settings(workspaces_dir=str(root),
                             global_system_workspace_path=str(root / "_global"),
                             internal_api_secret="s", ui_url="https://app.example.test",
                             redis_url="redis://fake")
    runtime = RuntimeHttpClient("http://runtime:8090", token="caller-token")
    app = create_app(Dispatcher(settings, runtime, _Identity(), warm_stream=fake_redis),
                     stream_reader=_Reader(), reader=WorkspaceReader(str(root)), redis_url="redis://fake")
    return TestClient(app, raise_server_exceptions=False)


def _assert_typed_spawn_refusal(r):
    assert r.status_code == 502, r.text
    assert "Internal Server Error" not in r.text
    for leak in ("kubectl", "vexa-agents", "agent-u1-main", "Traceback"):
        assert leak not in r.text, leak
    body = r.json()
    assert body["fault"] == {
        "source": "runtime", "kind": "spawn_refused", "op": "spawn", "status": 502,
        "detail": "a previous agent for this chat is still registered",
        "remedy": body["fault"]["remedy"]}
    assert body["fault"]["remedy"]
    assert "a previous agent for this chat is still registered" in body["detail"]
    return body["fault"]


# ── the demo-stack failure, end to end ─────────────────────────────────────────────────────────

def test_a_refused_spawn_on_api_chat_is_a_typed_502_not_internal_server_error(client):
    r = client.post("/api/chat", headers=_HEADERS, json={"prompt": "hi", "session": "main", "turn_id": "t-1"})
    _assert_typed_spawn_refusal(r)


def test_a_runtime_409_is_the_same_typed_refusal(client, runtime_answers):
    runtime_answers.update(status=409, body={"detail": "workload u1-main exists"})
    r = client.post("/api/chat/submit", headers=_HEADERS, json={"prompt": "hi", "session": "main", "turn_id": "t-1"})
    assert r.status_code == 502 and r.json()["fault"]["kind"] == "spawn_refused"


def test_a_runtime_that_is_down_is_a_typed_503(client, runtime_answers):
    runtime_answers["raise"] = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
    r = client.post("/api/chat/submit", headers=_HEADERS, json={"prompt": "hi", "session": "main", "turn_id": "t-1"})
    assert r.status_code == 503
    assert r.json()["fault"]["kind"] == "unreachable" and r.json()["fault"]["source"] == "runtime"


def test_the_refused_turn_is_withdrawn_so_a_retry_runs_it_once(client, fake_redis, runtime_answers):
    client.post("/api/chat/submit", headers=_HEADERS, json={"prompt": "hi", "session": "main", "turn_id": "t-1"})
    assert fake_redis.xrange(units.input_topic(_UNIT)) == []          # nothing left to run twice
    runtime_answers.update(status=201, body={"workloadId": _UNIT, "state": "running"})
    r = client.post("/api/chat/submit", headers=_HEADERS, json={"prompt": "hi", "session": "main", "turn_id": "t-1"})
    assert r.status_code == 200
    assert len(fake_redis.xrange(units.input_topic(_UNIT))) == 1
    assert unit_faults.live(fake_redis, _UNIT) is None                # the spawn cleared the block


# ── published on the chat's stream ──────────────────────────────────────────────────────────────

def _sse_events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_an_attach_to_the_refused_chat_gets_the_fault_as_an_error_event(client, fake_redis):
    refused = _assert_typed_spawn_refusal(
        client.post("/api/chat", headers=_HEADERS, json={"prompt": "hi", "session": "main", "turn_id": "t-1"}))
    # A second device (or this one after a reload) attaches to watch the chat run.
    r = client.post("/api/chat", headers={**_HEADERS, "Last-Event-ID": "0-0"},
                    json={"prompt": "", "session": "main"})
    assert r.status_code == 200
    evs = _sse_events(r.text)
    assert evs[0]["type"] == "error"
    assert {k: evs[0]["fault"][k] for k in refused} == refused
    assert "previous agent for this chat is still registered" in evs[0]["message"]
    assert evs[-1] == {"type": "turn-complete"}


def test_queued_rows_behind_the_refusal_say_they_are_blocked_and_by_what(client, fake_redis):
    # A message already on the inbox from before — a worker that died with it queued.
    key = unit_input.unit_key("s", _UNIT)
    fake_redis.xadd(units.input_topic(_UNIT), unit_input.signed_entry(key, {
        "type": "message", "prompt": "the context message", "nonce": "old",
        "inbox": {"id": "q-1", "display": "the context message", "at": time.time()}}))
    client.post("/api/chat/submit", headers=_HEADERS, json={"prompt": "hi", "session": "main", "turn_id": "t-1"})
    seen = client.get("/api/chat/pending", headers=_HEADERS, params={"session": "main"}).json()
    assert [p["id"] for p in seen["pending"]] == ["q-1"]
    blocked = seen["pending"][0]["blocked"]
    assert (blocked["source"], blocked["kind"]) == ("runtime", "spawn_refused")
    assert seen["fault"]["kind"] == "spawn_refused"


def test_the_block_lifts_the_moment_the_worker_takes_anything(client, fake_redis):
    """Evidence, not a latch (P21): a worker that moves its cursor is running, whatever the last
    spawn call said."""
    key = unit_input.unit_key("s", _UNIT)
    for i in (1, 2):
        fake_redis.xadd(units.input_topic(_UNIT), unit_input.signed_entry(key, {
            "type": "message", "prompt": f"m{i}", "nonce": f"n{i}",
            "inbox": {"id": f"q-{i}", "display": f"m{i}", "at": time.time()}}))
    client.post("/api/chat/submit", headers=_HEADERS, json={"prompt": "hi", "session": "main", "turn_id": "t-1"})
    first = fake_redis.xrange(units.input_topic(_UNIT))[0][0]
    fake_redis.set(units.inbox_cursor_key(_UNIT), first)
    seen = client.get("/api/chat/pending", headers=_HEADERS, params={"session": "main"}).json()
    assert [p["id"] for p in seen["pending"]] == ["q-2"]
    assert "blocked" not in seen["pending"][0] and "fault" not in seen


def test_a_turn_a_live_worker_already_took_is_not_refused(client, fake_redis, runtime_answers):
    """The runtime was unreachable for one call while a warm worker kept reading its inbox: the
    message is being answered, so asking the person to send it again would ask twice."""
    runtime_answers["raise"] = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
    real_xadd = fake_redis.xadd

    def xadd_and_take(name, fields, *a, **k):
        eid = real_xadd(name, fields, *a, **k)
        if name == units.input_topic(_UNIT):
            fake_redis.set(units.inbox_cursor_key(_UNIT), eid)      # the warm worker took it
        return eid

    fake_redis.xadd = xadd_and_take
    r = client.post("/api/chat/submit", headers=_HEADERS, json={"prompt": "hi", "session": "main", "turn_id": "t-1"})
    assert r.status_code == 200
    assert unit_faults.live(fake_redis, _UNIT) is None


# ── every other door: the app-level floor ──────────────────────────────────────────────────────

def test_the_internal_dispatch_sink_answers_the_same_typed_fault(client):
    inv = units.make_dispatch(subject="u1", trigger="scheduled", start=units.entrypoint(inline="brief"))
    r = client.post("/invocations", headers={"X-Internal-Secret": "s"}, json=inv)
    assert r.status_code == 502, r.text
    assert r.json()["fault"]["source"] == "runtime"
    assert "Internal Server Error" not in r.text


def test_a_retry_of_a_blocked_row_under_its_own_id_runs_it_once(client, fake_redis, runtime_answers):
    """The blocked row is still on the inbox, and the next worker that boots runs everything there.
    Retrying it under the same id withdraws the held copy first — one entry, not two."""
    key = unit_input.unit_key("s", _UNIT)
    fake_redis.xadd(units.input_topic(_UNIT), unit_input.signed_entry(key, {
        "type": "message", "prompt": "the context message", "nonce": "old",
        "inbox": {"id": "q-1", "display": "the context message", "at": time.time()}}))
    runtime_answers.update(status=201, body={"workloadId": _UNIT, "state": "running"})
    r = client.post("/api/chat/submit", headers=_HEADERS,
                    json={"prompt": "the context message", "session": "main", "turn_id": "q-1"})
    assert r.status_code == 200
    held = [json.loads(f["turn"])["inbox"]["id"] for _id, f in fake_redis.xrange(units.input_topic(_UNIT))]
    assert held == ["q-1"]
