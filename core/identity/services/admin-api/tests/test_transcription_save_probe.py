"""`PUT /user/transcription` judges the endpoint before storing it (#1924 s1).

A stub STT server answers a chosen status. A config fault (401/403/402/404) is a typed 422 and the
stored prefs are unchanged; 200 stores; a refused connection saves with `probe.kind=unreachable`.
Neither the URL nor the token appears in the 422 body or in logs. Same testcontainers-PG harness as
test_user_webhook_response (skips without docker)."""
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from admin_api.app import db as app_db
from admin_api.app import ssrf
from admin_api.app.main import create_app
from admin_api.schema.models import Base
from admin_api.schema.sync import ensure_schema_sync

from conftest import requires_docker
from test_stack_admin_api import ADMIN_TOKEN, INTERNAL_SECRET, _admin, _dispose_async_engine

pytestmark = requires_docker

TOKEN = "stt-token-never-echo-5d2e"


@pytest.fixture()
def stub():
    state = {"status": 200, "hits": 0}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            state["hits"] += 1
            self.send_response(state["status"])
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
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
    # the stub lives on loopback; the SSRF guard has its own tests
    monkeypatch.setattr(ssrf, "validate_url", lambda *a, **k: None)
    app_db.configure(pg_async_url)
    with TestClient(create_app()) as c:
        yield c
    _dispose_async_engine()


def _key(client, email):
    uid = client.post("/admin/users", headers=_admin(), json={"email": email}).json()["id"]
    tok = client.post(f"/admin/users/{uid}/tokens?scopes=bot,tx", headers=_admin()).json()["token"]
    return {"X-API-Key": tok}


@pytest.mark.parametrize("status,kind", [(401, "unauthorized"), (403, "unauthorized"),
                                         (402, "exhausted"), (404, "invalid_endpoint")])
def test_a_config_fault_is_a_typed_422_and_nothing_is_stored(client, stub, caplog, status, kind):
    h = _key(client, f"fault-{status}@vexa.ai")
    stub["status"] = status
    with caplog.at_level(logging.DEBUG):
        r = client.put("/user/transcription", headers=h, json={"url": stub["url"], "token": TOKEN})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "transcription_endpoint_rejected" and detail["kind"] == kind
    for secret in (TOKEN, stub["url"], "secret-path-7", "127.0.0.1"):
        assert secret not in r.text
        assert secret not in caplog.text
    got = client.get("/user/transcription", headers=h).json()
    assert got["url"] is None and got["token_set"] is False


def test_a_missing_token_against_an_endpoint_that_needs_one_is_refused(client, stub):
    h = _key(client, "notoken@vexa.ai")
    stub["status"] = 401
    r = client.put("/user/transcription", headers=h, json={"url": stub["url"]})
    assert r.status_code == 422 and r.json()["detail"]["kind"] == "unauthorized"
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
    assert r.status_code == 200 and "probe" not in r.json()
    got = client.get("/user/transcription", headers=h).json()
    assert got["url"] == stub["url"] and got["token_set"] is True
    assert stub["hits"] == 1


def test_an_unreachable_endpoint_saves_and_says_so(client, stub):
    h = _key(client, "down@vexa.ai")
    url = stub["url"]  # port closed afterwards = refused connection
    port = int(url.split(":")[2].split("/")[0])
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); closed = s.getsockname()[1]; s.close()
    r = client.put("/user/transcription", headers=h, json={"url": f"http://127.0.0.1:{closed}", "token": TOKEN})
    assert r.status_code == 200, r.text
    assert r.json()["probe"] == {"ok": False, "kind": "unreachable"}
    assert str(closed) not in r.json()["probe"].__repr__()
    assert client.get("/user/transcription", headers=h).json()["url"] == f"http://127.0.0.1:{closed}"
    assert port != closed


def test_clearing_the_url_does_not_probe(client, stub):
    h = _key(client, "clear@vexa.ai")
    client.put("/user/transcription", headers=h, json={"url": stub["url"], "token": TOKEN})
    hits = stub["hits"]
    stub["status"] = 401
    assert client.put("/user/transcription", headers=h, json={"url": ""}).status_code == 200
    assert stub["hits"] == hits
