"""`PUT /user/transcription` judges the endpoint before storing it (#1924 s1).

A stub STT server answers a chosen status. A config fault (401/403/402/404) is a typed 422 and the
stored prefs are unchanged; 200 stores; a refused connection saves with `probe.kind=unreachable`.
Neither the URL nor the token appears in the 422 body or in logs. Same testcontainers-PG harness as
test_user_webhook_response (skips without docker)."""
import json
import logging
import socket
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from admin_api.app import db as app_db
from admin_api.app import calendars as _calendars  # noqa: F401 — binds its real ssrf import before any patch
from admin_api.app import ssrf
from admin_api.app import transcription_probe as probe_mod
from admin_api.app.main import create_app
from admin_api.schema.models import Base
from admin_api.schema.sync import ensure_schema_sync

from conftest import requires_docker
from test_stack_admin_api import ADMIN_TOKEN, INTERNAL_SECRET, _admin, _dispose_async_engine

pytestmark = requires_docker

TOKEN = "stt-token-never-echo-5d2e"


@pytest.fixture()
def stub():
    # `gate`: when set to a threading.Event, the stub stalls (after counting the hit) until it is set.
    state = {"status": 200, "hits": 0, "location": None, "gate": None, "requests": []}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            state["requests"].append({"path": self.path, "headers": dict(self.headers), "body": body})
            state["hits"] += 1
            if state["gate"] is not None:
                state["gate"].wait(timeout=30)
            self.send_response(state["status"])
            if state["location"]:
                self.send_header("Location", state["location"])
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{srv.server_port}/secret-path-7"
    yield state
    srv.shutdown()


@pytest.fixture()
def client(pg_url, pg_async_url, monkeypatch):
    sync_engine = create_engine(pg_url)
    Base.metadata.drop_all(sync_engine)
    ensure_schema_sync(sync_engine, Base)
    sync_engine.dispose()
    monkeypatch.setenv("ADMIN_API_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL_SECRET)
    monkeypatch.setenv("DEV_MODE", "false")
    # The stub lives on loopback, which the guard (rightly) never lets an operator allow. Only the
    # loopback verdict is lifted; the guard stays on, so any other private address is still refused
    # (see the guard tests below).
    real_blocked = ssrf.is_blocked_ip
    monkeypatch.setattr(ssrf, "is_blocked_ip",
                        lambda a: False if str(a).startswith("127.") else real_blocked(a))
    app_db.configure(pg_async_url)
    with TestClient(create_app()) as c:
        yield c
    _dispose_async_engine()


def _key(client, email):
    uid = client.post("/admin/users", headers=_admin(), json={"email": email}).json()["id"]
    tok = client.post(f"/admin/users/{uid}/tokens", headers=_admin()).json()["token"]  # the default key
    return {"X-API-Key": tok}


@pytest.mark.parametrize("status,kind", [(401, "unauthorized"), (403, "unauthorized"),
                                         (402, "exhausted"), (404, "invalid_endpoint")])
def test_a_config_fault_is_a_typed_422_and_nothing_is_stored(client, stub, caplog, status, kind):
    h = _key(client, f"fault-{status}@vexa.ai")
    stub["status"] = status
    with caplog.at_level(logging.DEBUG):
        r = client.put("/user/transcription", headers=h, json={"url": stub["url"], "token": TOKEN})
    assert r.status_code == 422, r.text
    body = r.json()
    # One body shape: `detail` is the sentence (a string every client renders), the typed fields beside it.
    assert isinstance(body["detail"], str) and body["detail"] == probe_mod.REASONS[kind]
    assert body["error"] == "transcription_endpoint_rejected" and body["kind"] == kind
    assert body["status"] == status
    for secret in (TOKEN, stub["url"], "secret-path-7", "127.0.0.1"):
        assert secret not in r.text
        assert secret not in caplog.text
    got = client.get("/user/transcription", headers=h).json()
    assert got["url"] is None and got["token_set"] is False


def test_a_missing_token_against_an_endpoint_that_needs_one_is_refused(client, stub):
    h = _key(client, "notoken@vexa.ai")
    stub["status"] = 401
    r = client.put("/user/transcription", headers=h, json={"url": stub["url"]})
    assert r.status_code == 422 and r.json()["kind"] == "unauthorized"
    assert client.get("/user/transcription", headers=h).json()["url"] is None


def test_a_rejected_change_leaves_the_previous_value(client, stub):
    h = _key(client, "keep@vexa.ai")
    assert client.put("/user/transcription", headers=h, json={"url": stub["url"], "token": TOKEN}).status_code == 200
    before = client.get("/user/transcription", headers=h).json()
    stub["status"] = 401
    assert client.put("/user/transcription", headers=h, json={"token": "bad"}).status_code == 422
    assert client.get("/user/transcription", headers=h).json() == before


def test_a_working_endpoint_is_stored(client, stub):
    h = _key(client, "ok@vexa.ai")
    r = client.put("/user/transcription", headers=h, json={"url": stub["url"], "token": TOKEN})
    assert r.status_code == 200 and r.json()["probe"] == {"ok": True}
    assert "probe" not in client.get("/user/transcription", headers=h).json()  # the write's response only
    got = client.get("/user/transcription", headers=h).json()
    assert got["url"] == stub["url"] and got["token_set"] is True
    assert stub["hits"] == 1


def test_an_unreachable_endpoint_saves_and_says_so(client, caplog):
    h = _key(client, "down@vexa.ai")
    s = socket.socket(); s.bind(("127.0.0.1", 0)); closed = s.getsockname()[1]; s.close()  # refused connection
    url = f"http://127.0.0.1:{closed}"
    with caplog.at_level(logging.DEBUG):
        r = client.put("/user/transcription", headers=h, json={"url": url, "token": TOKEN})
    assert r.status_code == 200, r.text
    assert r.json()["probe"] == {"ok": False, "kind": "unreachable"}
    # the refused connection's exception text names the address; it must reach neither the body nor
    # the logs (the body's `url` is the saved url itself, so the port is checked in the logs only)
    for secret in (TOKEN, "refused", "ConnectError", "Errno"):
        assert secret not in r.text
    for secret in (TOKEN, str(closed), "refused", "ConnectError", "Errno"):
        assert secret not in caplog.text
    assert client.get("/user/transcription", headers=h).json()["url"] == url


def test_a_name_resolving_to_a_private_address_is_refused_without_a_connection(client, monkeypatch):
    h = _key(client, "rebind@vexa.ai")
    monkeypatch.setattr(ssrf, "resolve_host", lambda host: ["10.1.2.3"])
    import httpx
    dialled = []
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda self, req: dialled.append(req))
    r = client.put("/user/transcription", headers=h, json={"url": "http://stt.public-looking.example", "token": TOKEN})
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["error"] == "transcription_endpoint_rejected"
    assert body["kind"] == "internal_address" and body["status"] is None
    assert isinstance(body["detail"], str) and "internal or private" in body["detail"]
    assert TOKEN not in r.text
    assert dialled == []
    assert client.get("/user/transcription", headers=h).json()["url"] is None


def test_a_redirect_is_not_followed(client, stub):
    h = _key(client, "redirect@vexa.ai")
    target_hits = []

    class T(BaseHTTPRequestHandler):
        def _hit(self):
            target_hits.append(self.command)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
        do_POST = do_GET = _hit

        def log_message(self, *a):
            pass

    target = HTTPServer(("127.0.0.1", 0), T)
    threading.Thread(target=target.serve_forever, daemon=True).start()
    try:
        stub["status"] = 302
        stub["location"] = f"http://127.0.0.1:{target.server_port}/internal"
        r = client.put("/user/transcription", headers=h, json={"url": stub["url"], "token": TOKEN})
        assert r.status_code == 200, r.text
        assert stub["hits"] == 1 and target_hits == []   # the Location was never dialled
    finally:
        target.shutdown()


def test_the_probe_spec_is_the_one_meeting_api_declares():
    decl = Path(__file__).resolve().parents[4] / "meetings/services/meeting-api/src/meeting_api/config.v1.json"
    if not decl.is_file():
        pytest.skip(f"meeting-api's declaration is not in this checkout ({decl}); the pin needs the monorepo")
    probe_decl = json.loads(decl.read_text())["capabilities"]["stt"]["probe"]
    declared = probe_decl["http"]
    assert probe_mod.PROBE_TIMEOUT_S == float(probe_decl["timeout_s"])
    # Every field the probe uses matches the declaration; the only declared fields it leaves out are
    # the env-key names (the person's URL and token are passed in, never read from env).
    assert probe_mod.STT_PROBE_SPEC == {k: declared[k] for k in probe_mod.STT_PROBE_SPEC}
    assert set(declared) - set(probe_mod.STT_PROBE_SPEC) == {"url_key", "auth_key"}


def test_clearing_the_url_does_not_probe(client, stub):
    h = _key(client, "clear@vexa.ai")
    client.put("/user/transcription", headers=h, json={"url": stub["url"], "token": TOKEN})
    hits = stub["hits"]
    stub["status"] = 401
    assert client.put("/user/transcription", headers=h, json={"url": ""}).status_code == 200
    assert stub["hits"] == hits


def test_a_model_validating_vendor_400_is_stored_as_ok(client, stub):
    """A vendor that checks the model name answers the probe's model with 400: the token got in, so
    it is not a config fault (A2) and the write saves."""
    h = _key(client, "vendor400@vexa.ai")
    stub["status"] = 400
    r = client.put("/user/transcription", headers=h, json={"url": stub["url"], "token": TOKEN})
    assert r.status_code == 200, r.text
    assert r.json()["probe"] == {"ok": True}
    got = client.get("/user/transcription", headers=h).json()
    assert got["url"] == stub["url"] and got["token_set"] is True


def test_the_probe_is_a_real_audio_transcription_request(client, stub):
    h = _key(client, "wire@vexa.ai")
    assert client.put("/user/transcription", headers=h,
                      json={"url": stub["url"], "token": TOKEN}).status_code == 200
    assert len(stub["requests"]) == 1
    req = stub["requests"][0]
    headers = {k.lower(): v for k, v in req["headers"].items()}
    assert req["path"] == "/secret-path-7" + probe_mod.STT_PROBE_SPEC["path"]
    assert headers["authorization"] == f"Bearer {TOKEN}"
    assert headers["content-type"].startswith("multipart/form-data; boundary=")
    body = req["body"]
    assert b"RIFF" in body and b'filename="probe.wav"' in body
    assert b'name="model"' in body and b"whisper-1" in body


def test_a_language_only_update_does_not_probe(client, stub):
    h = _key(client, "lang@vexa.ai")
    assert client.put("/user/transcription", headers=h,
                      json={"url": stub["url"], "token": TOKEN}).status_code == 200
    hits = stub["hits"]
    stub["status"] = 401  # a probe now would refuse; none is sent
    r = client.put("/user/transcription", headers=h, json={"language": "de"})
    assert r.status_code == 200, r.text
    assert "probe" not in r.json()
    assert stub["hits"] == hits
    assert client.get("/user/transcription", headers=h).json()["url"] == stub["url"]


def test_the_probe_does_not_hold_the_row_lock(client, stub):
    """While the endpoint stalls, another write by the same person (it takes the row lock) finishes."""
    h = _key(client, "lock@vexa.ai")
    stub["gate"] = threading.Event()
    first, second = {}, {}

    def _slow_write():
        first["r"] = client.put("/user/transcription", headers=h, json={"url": stub["url"], "token": TOKEN})

    def _language_write():
        second["r"] = client.put("/user/transcription", headers=h, json={"language": "fr"})

    t1 = threading.Thread(target=_slow_write, daemon=True)
    try:
        t1.start()
        for _ in range(200):  # wait until the probe has reached the stub and is stalled there
            if stub["hits"]:
                break
            time.sleep(0.05)
        assert stub["hits"] == 1
        t2 = threading.Thread(target=_language_write, daemon=True)
        t2.start()
        t2.join(timeout=10)
        assert not t2.is_alive(), "a same-user write waited on the stalled probe"
        assert second["r"].status_code == 200, second["r"].text
        assert "r" not in first  # the probed write is still waiting on the endpoint
    finally:
        stub["gate"].set()
    t1.join(timeout=20)
    assert first["r"].status_code == 200, first["r"].text
    got = client.get("/user/transcription", headers=h).json()
    assert got["url"] == stub["url"] and got["token_set"] is True
