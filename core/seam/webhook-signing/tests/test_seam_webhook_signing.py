"""L3 SEAM — the per-user webhook secret signs the meeting.completed delivery (Vexa-ai/vexa#1864).

Flow, every hop the SHIPPED code over real HTTP on loopback:

  1. client → REAL gateway (production httpx adapters) → PUT /user/webhook → REAL admin-api
     (uvicorn, Postgres) writes users.data.webhook_secret.
  2. client → gateway → POST /bots: the gateway resolves the key over admin-api /internal/validate and
     injects x-user-webhook-{url,secret,events} → REAL meeting-api (uvicorn) persists them on
     meeting.data.
  3. the bot's lifecycle callbacks (joining → active → completed) → meeting-api's WebhookSink POSTs the
     meeting.completed envelope to a real HTTP receiver, signed
     `X-Webhook-Signature: sha256=HMAC(secret, f"{ts}.{body}")` + `X-Webhook-Timestamp`.

Fakes, and only these: the meeting repo (in-memory) and the runtime kernel (no bot container). The
receiver is a loopback HTTP server, which the SSRF guard rightly refuses, so the sink is built with a
DNS stub that resolves the receiver's hostname to a public address and an inner transport that dials
that one address on 127.0.0.1. The production guard + pinned transport (`build_pinned_transport`) still
run on every delivery.

The same run pins two edges: a meeting spawned BEFORE the secret was set is delivered unsigned (the
config is frozen into meeting.data at spawn), and a later PUT carrying only `webhook_url` keeps the
stored secret.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

ADMIN_TOKEN = "seam-admin-token"
INTERNAL_SECRET = "seam-internal-secret"
SECRET = "test-secret"
HOOK_HOST = "hook.seam.example"
HOOK_PUBLIC_IP = "93.184.216.34"  # what the DNS stub answers; dialed on loopback by the inner transport


# ── loopback HTTP receiver ───────────────────────────────────────────────────────────────────────

class _Receiver:
    def __init__(self):
        self.deliveries: list[dict] = []
        received = self.deliveries

        class _H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                received.append({"path": self.path, "headers": dict(self.headers.items()), "body": body})
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


# ── real uvicorn servers on loopback ─────────────────────────────────────────────────────────────

def _serve(app) -> tuple[str, object]:
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
    threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True).start()
    deadline = time.time() + 30
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}", server


def _loopback_inner_transport():
    """Dial the stubbed public address on 127.0.0.1 — the only deviation from the production transport."""

    class _Loopback(httpx.AsyncHTTPTransport):
        async def handle_async_request(self, request):
            if request.url.host == HOOK_PUBLIC_IP:
                request.url = request.url.copy_with(host="127.0.0.1")
            return await super().handle_async_request(request)

    return _Loopback()


@pytest.fixture(scope="module")
def stack(pg_url):
    mp = pytest.MonkeyPatch()
    mp.setenv("ADMIN_API_TOKEN", ADMIN_TOKEN)
    mp.setenv("INTERNAL_API_SECRET", INTERNAL_SECRET)
    mp.setenv("DEV_MODE", "false")
    mp.setenv("ADMIN_TOKEN", "seam-meeting-token-secret")  # meeting-api mints the MeetingToken with it
    mp.setenv("TRANSCRIPTION_SERVICE_URL", "http://stt.test/transcribe")
    mp.setenv("TRANSCRIPTION_SERVICE_TOKEN", "seam-stt-token")

    # admin-api over Postgres
    from admin_api.app import db as app_db
    from admin_api.app.main import create_app as create_admin_app
    from admin_api.schema.models import Base
    from admin_api.schema.sync import ensure_schema_sync

    eng = create_engine(pg_url)
    Base.metadata.drop_all(eng)
    ensure_schema_sync(eng, Base)
    eng.dispose()
    app_db.configure(pg_url.replace("postgresql+psycopg://", "postgresql+asyncpg://"))
    admin_url, admin_srv = _serve(create_admin_app())

    # meeting-api with the production WebhookSink (SSRF guard + pinned transport + signing)
    from meeting_api import create_app as create_meeting_app
    from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
    from meeting_api.webhooks import WebhookSink
    from meeting_api.webhooks.ssrf import build_pinned_transport

    resolver = lambda host: [HOOK_PUBLIC_IP] if host == HOOK_HOST else []  # noqa: E731

    async def transport(url, body, headers):
        async with httpx.AsyncClient(
            timeout=10.0,
            transport=build_pinned_transport(inner=_loopback_inner_transport(), resolver=resolver),
        ) as c:
            return await c.post(url, content=body, headers=headers)

    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    meeting_app = create_meeting_app(
        meeting_repo=repo, runtime=runtime,
        token_secret="seam-meeting-token-secret",  # the production entrypoint passes ADMIN_TOKEN here
        webhook_sink=WebhookSink(transport, resolver=resolver),
    )
    meeting_url, meeting_srv = _serve(meeting_app)

    # gateway over its production adapters, pointed at the two real servers
    from gateway.adapters import build_auth_and_downstream
    from gateway.app import create_app as create_gateway_app

    authorizer, downstream = build_auth_and_downstream(admin_url, meeting_url)
    gateway = TestClient(create_gateway_app(
        authorizer, downstream, object(),
        meeting_api_url=meeting_url, admin_api_url=admin_url, agent_api_url="",
    ))

    receiver = _Receiver()
    gateway.__enter__()  # one portal loop for the whole module: the httpx pools outlive a request
    try:
        yield {"admin": admin_url, "meeting": meeting_url, "gateway": gateway,
               "receiver": receiver, "repo": repo, "runtime": runtime}
    finally:
        gateway.__exit__(None, None, None)
        receiver.close()
        for srv in (meeting_srv, admin_srv):
            srv.should_exit = True
        time.sleep(0.3)
        mp.undo()


def _sign(body: bytes, ts: str, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


def _bot_token(runtime, session_uid: str) -> str:
    """The MeetingToken the spawned bot carries: the lifecycle callback admits only its own session's."""
    for spec in runtime.specs:
        cfg = json.loads(spec["env"]["VEXA_BOT_CONFIG"])
        if cfg.get("connectionId") == session_uid:
            return cfg["token"]
    raise AssertionError(f"no spawned bot for session {session_uid}")


def _complete(meeting_url: str, session_uid: str, token: str):
    for status in ("joining", "active", "completed"):
        ev = {"connection_id": session_uid, "status": status,
              "timestamp": "2026-10-22T10:00:00.000Z"}
        if status == "completed":
            ev["completion_reason"] = "stopped"
        r = httpx.post(f"{meeting_url}/bots/internal/callback/lifecycle", json=ev,
                       headers={"Authorization": f"Bearer {token}"}, timeout=10)
        assert r.status_code == 200, r.text


def _session_uid(bot: dict) -> str:
    s = bot["data"]["sessions"][-1]
    return s if isinstance(s, str) else s["session_uid"]


def _completed_for(receiver: _Receiver, native_id: str) -> dict:
    deadline = time.time() + 10
    while time.time() < deadline:
        for d in receiver.deliveries:
            env = json.loads(d["body"])
            if env.get("event_type") == "meeting.completed" and \
                    env["data"]["meeting"].get("native_meeting_id") == native_id:
                return d
        time.sleep(0.05)
    raise AssertionError(f"no meeting.completed delivered for {native_id}: {len(receiver.deliveries)} deliveries")


def test_user_webhook_secret_signs_meeting_completed(stack):
    gw, rx = stack["gateway"], stack["receiver"]
    admin = {"X-Admin-API-Key": ADMIN_TOKEN}
    hook = f"http://{HOOK_HOST}:{rx.port}/hook"

    r = httpx.post(f"{stack['admin']}/admin/users", headers=admin,
                   json={"email": "seam-1864@vexa.ai", "name": "Seam", "max_concurrent_bots": 5})
    assert r.status_code in (200, 201), r.text
    r = httpx.post(f"{stack['admin']}/admin/users/{r.json()['id']}/tokens?scopes=bot,tx", headers=admin)
    assert r.status_code == 201, r.text
    key = {"X-API-Key": r.json()["token"]}

    def spawn(native_id: str) -> dict:
        r = gw.post("/bots", headers=key, json={"platform": "google_meet", "native_meeting_id": native_id})
        assert r.status_code == 201, r.text
        return r.json()

    # Edge A: a meeting spawned while the user has a URL but NO secret.
    r = gw.put("/user/webhook", headers=key,
               json={"webhook_url": hook, "webhook_events": {"meeting.completed": True}})
    assert r.status_code == 200, r.text
    before = spawn("seam-before-secret")

    # The reporter's request, verbatim shape.
    r = gw.put("/user/webhook", headers=key,
               json={"webhook_url": hook, "webhook_secret": SECRET,
                     "webhook_events": {"meeting.completed": True}})
    assert r.status_code == 200, r.text
    assert "webhook_secret" not in r.json()["data"]          # the response serializer omits it ...
    r = gw.get("/user/webhook", headers=key)
    assert r.json()["webhook_secret_set"] is True             # ... and the read-back masks it
    assert SECRET not in json.dumps(r.json())
    signed = spawn("seam-with-secret")

    # Edge B: a URL-only PUT keeps the stored secret.
    r = gw.put("/user/webhook", headers=key, json={"webhook_url": hook})
    assert r.status_code == 200, r.text
    assert gw.get("/user/webhook", headers=key).json()["webhook_secret_set"] is True
    kept = spawn("seam-url-only-put")

    for bot in (before, signed, kept):
        uid = _session_uid(bot)
        _complete(stack["meeting"], uid, _bot_token(stack["runtime"], uid))

    for native_id in ("seam-with-secret", "seam-url-only-put"):
        d = _completed_for(rx, native_id)
        h = {k.lower(): v for k, v in d["headers"].items()}
        ts = h["x-webhook-timestamp"]
        assert h["x-webhook-signature"] == _sign(d["body"], ts, SECRET), native_id
        assert abs(int(ts) - time.time()) < 60

    d = _completed_for(rx, "seam-before-secret")
    h = {k.lower() for k in d["headers"]}
    assert "x-webhook-signature" not in h and "x-webhook-timestamp" not in h
