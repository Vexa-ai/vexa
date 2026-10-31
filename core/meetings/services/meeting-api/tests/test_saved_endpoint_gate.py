"""A Settings-saved STT endpoint is judged by its OWN probe before a spawn uses it (#1924 s2).

A loopback stub STT server answers a chosen status; the conftest stub is replaced by the real probe;
only the loopback verdict of the outbound guard is lifted (any other private address still refused).
T2: config faults refuse `POST /bots` with a typed 503 before any row; ok/400/unreachable spawn; the
verdict cache bounds (ok 900 s, fault 60 s, keyed by a hash); the env backend gets no probe traffic.
T3: neither the URL nor the token reaches the 503, logs, log_event fields or the meeting row.
"""
from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn import saved_stt_probe, service as svc
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.webhooks import ssrf

HEADERS = {"x-user-id": "7"}
TOKEN = "saved-stt-token-never-echo-9f1c"
PREFIX = "your saved transcription endpoint is not working"


def _server(state):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            state["requests"].append({"path": self.path, "headers": dict(self.headers), "body": body})
            self.send_response(state["status"])
            if state.get("location"):
                self.send_header("Location", state["location"])
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture()
def stub(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "admin-key-for-tests-0123456789abcdef")
    monkeypatch.setenv("VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE", "/run/vexa-identity/public/key.pem")
    monkeypatch.setenv("RUNTIME_API_TOKEN", "runtime-caller-token-for-tests-0123456789abcdef")
    monkeypatch.setenv("DB_PASSWORD", "db-password-for-tests-0123456789abcdef")
    state = {"status": 200, "requests": []}
    env_state = {"status": 200, "requests": []}
    srv, env_srv = _server(state), _server(env_state)
    state["url"] = f"http://127.0.0.1:{srv.server_port}/secret-path-7"
    state["env"] = env_state
    # the env backend is a live server too, so any probe traffic to it would be recorded
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", f"http://127.0.0.1:{env_srv.server_port}")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "env-token")
    real_blocked = ssrf.is_blocked_ip
    monkeypatch.setattr(ssrf, "is_blocked_ip",
                        lambda a: False if str(a).startswith("127.") else real_blocked(a))
    monkeypatch.setattr(saved_stt_probe, "_probe", saved_stt_probe.probe_endpoint)
    clock = {"t": 1000.0}
    monkeypatch.setattr(saved_stt_probe, "_now", lambda: clock["t"])
    state["clock"] = clock
    state["saved"] = {"url": state["url"], "token": TOKEN}

    async def _ctx(user_id):
        return {"transcription": dict(state["saved"], provider="customer")}

    monkeypatch.setattr(svc, "_fetch_bot_context", _ctx)
    events = []
    real_log = svc.log_event
    monkeypatch.setattr(svc, "log_event", lambda *a, **k: (events.append((a, k)), real_log(*a, **k))[1])
    state["events"] = events
    yield state
    srv.shutdown()
    env_srv.shutdown()


def _spawn(repo=None, mid="saved-stt"):
    repo = repo or InMemoryMeetingRepo()
    c = TestClient(create_app(meeting_repo=repo, runtime=FakeRuntimeClient()))
    return c.post("/bots", headers=HEADERS, json={"platform": "google_meet", "native_meeting_id": mid}), repo


def _secrets(stub):
    return [stub["url"], "secret-path-7", TOKEN, f"127.0.0.1:{stub['url'].split(':')[2].split('/')[0]}"]


@pytest.mark.parametrize("status,kind", [(401, "unauthorized"), (403, "unauthorized"),
                                         (402, "exhausted"), (404, "invalid_endpoint")])
def test_a_rejecting_saved_endpoint_refuses_before_any_row(stub, status, kind, caplog):
    stub["status"] = status
    caplog.set_level(logging.DEBUG)
    r, repo = _spawn()
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert detail.startswith(PREFIX) and f"HTTP {status}" in detail
    assert repo._meetings == {}
    assert len(stub["requests"]) == 1 and stub["requests"][0]["path"] == "/secret-path-7/v1/audio/transcriptions"
    assert stub["requests"][0]["headers"].get("Authorization") == f"Bearer {TOKEN}"
    assert b"RIFF" in stub["requests"][0]["body"]
    ev = [k for a, k in stub["events"] if a and a[0] == "bot_spawn_saved_stt_endpoint_unhealthy"]
    assert ev and ev[0]["fields"] == {"kind": kind, "status": status}
    blob = r.text + caplog.text + json.dumps([k for _, k in stub["events"]], default=str)
    for s in _secrets(stub):
        assert s not in blob, f"leaked {s!r}"
    assert stub["env"]["requests"] == [], "the env backend must receive no probe traffic"


@pytest.mark.parametrize("status", [200, 400])
def test_a_working_saved_endpoint_spawns_and_nothing_leaks(stub, status, caplog):
    stub["status"] = status  # 400: a model-validating vendor refusing whisper-1 is still ok
    caplog.set_level(logging.DEBUG)
    r, repo = _spawn()
    assert r.status_code == 201, r.text
    rows = json.dumps(list(repo._meetings.values()), default=str)
    blob = rows + r.text + caplog.text + json.dumps([k for _, k in stub["events"]], default=str)
    for s in _secrets(stub):
        assert s not in blob, f"leaked {s!r}"
    assert stub["env"]["requests"] == []


def test_unreachable_never_refuses(stub):
    stub["saved"] = {"url": "http://127.0.0.1:1/v1", "token": TOKEN}
    r, _ = _spawn()
    assert r.status_code == 201, r.text


def test_an_ok_verdict_lives_900s_and_a_revoked_token_refuses_after(stub):
    assert _spawn(mid="a")[0].status_code == 201
    stub["status"] = 401  # revoked at the provider
    stub["clock"]["t"] += 899
    assert _spawn(mid="b")[0].status_code == 201
    assert len(stub["requests"]) == 1, "one probe per (url, token) per TTL"
    stub["clock"]["t"] += 2
    r, repo = _spawn(mid="c")
    assert r.status_code == 503 and r.json()["detail"].startswith(PREFIX)
    assert repo._meetings == {}
    assert len(stub["requests"]) == 2


def test_a_fault_verdict_lives_60s(stub):
    stub["status"] = 401
    assert _spawn(mid="a")[0].status_code == 503
    stub["status"] = 200  # fixed at the provider
    stub["clock"]["t"] += 59
    assert _spawn(mid="b")[0].status_code == 503
    stub["clock"]["t"] += 2
    assert _spawn(mid="c")[0].status_code == 201
    assert len(stub["requests"]) == 2


def test_the_cache_holds_verdicts_only_keyed_by_a_hash(stub):
    _spawn()
    assert saved_stt_probe._cache
    dumped = json.dumps(saved_stt_probe._cache, default=str)
    for s in _secrets(stub):
        assert s not in dumped


def test_a_new_token_is_a_new_pair(stub):
    assert _spawn(mid="a")[0].status_code == 201
    stub["status"] = 401
    stub["saved"] = {"url": stub["url"], "token": "another-token"}
    assert _spawn(mid="b")[0].status_code == 503


def test_a_host_resolving_to_a_private_address_is_refused_without_a_connection(stub, monkeypatch):
    monkeypatch.setattr(saved_stt_probe, "resolver", lambda host: ["10.1.2.3"])
    dialled = []
    monkeypatch.setattr(saved_stt_probe.httpx, "Client", lambda *a, **k: dialled.append(1))
    stub["saved"] = {"url": "http://stt.customer.example/v1", "token": TOKEN}
    r, repo = _spawn()
    assert r.status_code == 503 and "internal or private" in r.json()["detail"]
    assert dialled == [] and repo._meetings == {}


def test_a_redirect_is_not_followed(stub):
    other = {"status": 401, "requests": []}
    srv = _server(other)
    try:
        stub["status"], stub["location"] = 302, f"http://127.0.0.1:{srv.server_port}/v1/audio/transcriptions"
        _spawn()
        assert other["requests"] == [], "the Location must never be dialled"
    finally:
        srv.shutdown()


def test_capture_only_spawn_makes_no_probe(stub):
    stub["status"] = 401
    c = TestClient(create_app(meeting_repo=InMemoryMeetingRepo(), runtime=FakeRuntimeClient()))
    r = c.post("/bots", headers=HEADERS, json={"platform": "google_meet", "native_meeting_id": "x",
                                              "transcribe_enabled": False})
    assert r.status_code == 201, r.text
    assert stub["requests"] == []
