"""bot_accounts (ADR-0049) — browser sessions, saved bot accounts, and a meeting bot that joins as one.

Drives the SHIPPED app (`create_app`) over fakes: the broker (`FakeBroker`, the broker's own
semantics), the runtime (`FakeRuntimeClient`), meeting-api's Redis (fakeredis under
`RedisSessionStore`), admin-api's roles (`FakeAdminOracle`) and the workload's noVNC/websockify
(`FakeVncUpstream`).
"""
from __future__ import annotations

import base64
import json
from urllib.parse import urlsplit

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from meeting_api import create_app, identity_token
from meeting_api.bot_accounts import (
    NOT_CONFIGURED,
    BotAccounts,
    FakeAdminOracle,
    FakeBroker,
    FakeVncUpstream,
    HttpBrokerClient,
    RedisSessionStore,
    Settings,
    broker_assertion,
    view_path_refusal,
)
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo

SECRET = "bot-accounts-test-admin-token"
OWNER = 7
OTHER = 8
ADMIN_ACCOUNT = 99
PERSON_ADMIN = 50
PROFILE = {"Default/Cookies": b"cookie-bytes", "Local State": b"{}"}
BASE = "http://meeting-api:8080"


def _files_body(files=PROFILE):
    return {"files": [{"path": p, "data": base64.b64encode(d).decode()} for p, d in files.items()]}


class Rig:
    def __init__(self, *, configured=True, policy="admins", identity_key=None):
        self.broker = FakeBroker() if configured else None
        self.runtime = FakeRuntimeClient()
        self.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self.upstream = FakeVncUpstream({
            "http://vexa-bs-{sid}:6080/vnc.html": (200, "text/html; charset=utf-8", b"<html>novnc</html>"),
        })
        self.policy = policy
        self.repo = InMemoryMeetingRepo()
        self.ba = BotAccounts(
            broker=self.broker, runtime=self.runtime, store=RedisSessionStore(self.redis),
            admin=FakeAdminOracle({ADMIN_ACCOUNT: {"admin_account": True},
                                   PERSON_ADMIN: {"is_admin": True}}),
            upstream=self.upstream, settings=Settings(callback_base=BASE),
            policy=lambda: self.policy,
        )
        self.client = TestClient(create_app(
            bot_accounts=self.ba, runtime=self.runtime, meeting_repo=self.repo,
            token_secret=SECRET, open_callbacks=True, identity_key=identity_key,
            internal_secret="bot-accounts-internal" if identity_key is not None else None,
        ))

    def as_(self, uid):
        return {"x-user-id": str(uid)}

    def open(self, uid=OWNER, **body):
        r = self.client.post("/bot-accounts/sessions", headers=self.as_(uid),
                             json={"platform": "google", **body})
        return r

    def last_env(self):
        return json.loads(self.runtime.specs[-1]["env"]["VEXA_BROWSER_SESSION"])

    def save(self, sid, token, files=PROFILE):
        return self.client.put(f"/internal/bot-accounts/sessions/{sid}/profile",
                               headers={"Authorization": f"Bearer {token}"}, json=_files_body(files))

    def seed(self, owner, platform="google", state="ok", verified_at="2026-10-01T00:00:00Z", aid=None):
        aid = aid or (f"{len(self.broker.accounts):032x}")
        return self.broker.seed({"id": aid, "owner": owner, "platform": platform,
                                 "display_name": "acct", "state": state, "verified_at": verified_at},
                                files=list(PROFILE.items()))


@pytest.fixture
def rig(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.example.test")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "tok-test")
    monkeypatch.setenv("MEETING_API_URL", BASE)
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    for k in ("BOT_AUTHENTICATED", "ADMIN_API_URL"):
        monkeypatch.delenv(k, raising=False)
    return Rig()


# ── not configured ───────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [
    ("GET", "/bot-accounts"),
    ("POST", "/bot-accounts/sessions"),
    ("POST", f"/bot-accounts/{'a' * 32}/verify"),
    ("DELETE", f"/bot-accounts/{'a' * 32}"),
])
def test_an_unconfigured_deployment_answers_409(method, path):
    rig = Rig(configured=False)
    r = rig.client.request(method, path, headers=rig.as_(OWNER), json={"platform": "google"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == NOT_CONFIGURED


def test_an_unconfigured_deployment_still_spawns_guest_bots_and_refuses_an_explicit_account(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.example.test")
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    monkeypatch.delenv("BOT_AUTHENTICATED", raising=False)
    rig = Rig(configured=False)
    r = rig.client.post("/bots", headers=rig.as_(OWNER),
                        json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij"})
    assert r.status_code == 201, r.text
    inv = json.loads(rig.runtime.specs[-1]["env"]["VEXA_BOT_CONFIG"])
    assert "authenticated" not in inv and "sessionRestoreUrl" not in inv
    r = rig.client.post("/bots", headers=rig.as_(OWNER),
                        json={"platform": "google_meet", "native_meeting_id": "abc-defg-hik",
                              "bot_account": "b" * 32})
    assert r.status_code == 409


# ── a browser session ────────────────────────────────────────────────────────────────────────────

def test_opening_a_session_spawns_a_signin_workload(rig):
    r = rig.open(display_name="Sales bot")
    assert r.status_code == 201, r.text
    body = r.json()
    sid = body["session_id"]
    assert len(sid) == 32 and body["status"] == "starting" and body["owner"] == f"user:{OWNER}"
    assert body["view_path"] == f"/bot-accounts/sessions/{sid}/view/vnc.html"
    assert body["ws_path"] == f"/bot-accounts/sessions/{sid}/vnc"
    assert len(body["vnc_password"]) == 8
    spec = rig.runtime.specs[-1]
    assert spec["profile"] == "meeting-bot" and spec["workloadId"] == f"bs-{sid}"
    assert set(spec["env"]) == {"VEXA_BROWSER_SESSION"}  # no BOT_CONFIG
    env = rig.last_env()
    assert env["mode"] == "signin" and env["platform"] == "google" and env["session_id"] == sid
    assert env["vnc_password"] == body["vnc_password"]
    assert env["save_url"] == f"{BASE}/internal/bot-accounts/sessions/{sid}/profile"
    assert env["timeout_ms"] == 900_000
    # The token is stored hashed, never in clear.
    import asyncio
    raw = asyncio.run(rig.redis.get(f"botsession:{sid}"))
    assert env["token"] not in raw and json.loads(raw)["token_hash"]


# ── VEXA_BOT_SIGNIN_EXTRA_HOSTS: an operator's company IdP (a bank's ADFS) ─────────────────────────
_RESOLVES = {"adfs.bank.example": ["203.0.114.10"], "login.okta.bank.example": ["203.0.114.12"], "adfs-internal.bank.example": ["10.1.2.3"],
             "metadata.bank.example": ["169.254.169.254"], "mixed.bank.example": ["203.0.114.11", "127.0.0.1"]}


def _extra_rig(raw):
    rig = Rig()
    rig.ba.settings = Settings(callback_base=BASE,
                               signin_extra_hosts=Settings.from_env({"VEXA_BOT_SIGNIN_EXTRA_HOSTS": raw}).signin_extra_hosts)
    rig.ba.resolver = lambda h: _RESOLVES.get(h, [])
    return rig


def test_no_extra_hosts_by_default(rig):
    assert Settings.from_env({}).signin_extra_hosts == ()
    assert rig.open().status_code == 201
    assert "extra_hosts" not in rig.last_env()


def test_extra_public_hosts_reach_the_signin_browser(rig):
    r = _extra_rig("ADFS.bank.example, login.okta.bank.example")
    assert r.open().status_code == 201, r.client
    assert r.last_env()["extra_hosts"] == ["adfs.bank.example", "login.okta.bank.example"]


@pytest.mark.parametrize("entry", ["*.okta.bank.example", "*.bank.example", "*adfs.bank.example", "adfs.*.example"])
def test_a_wildcard_sign_in_host_refuses_the_boot(entry):
    """The `*.suffix` form is gone: a wildcard could not be resolved ahead, so a subdomain that
    resolved to an internal address reached the sign-in browser. Each host is named exactly."""
    from meeting_api.bot_accounts.service import parse_signin_extra_hosts
    with pytest.raises(ValueError, match="wildcard"):
        parse_signin_extra_hosts(f"adfs.bank.example,{entry}")


@pytest.mark.parametrize("host", ["adfs-internal.bank.example", "metadata.bank.example", "mixed.bank.example",
                                  "unresolved.bank.example"])
def test_an_extra_host_that_resolves_internal_or_not_at_all_is_left_out(rig, host):
    r = _extra_rig(f"adfs.bank.example,{host}")
    assert r.open().status_code == 201
    assert r.last_env()["extra_hosts"] == ["adfs.bank.example"]


@pytest.mark.parametrize("entry", ["10.0.0.5", "192.168.1.1", "169.254.169.254", "127.0.0.1", "[::1]", "::1",
                                   "intranet", "localhost", "a.localhost", "*.localhost", "*.com",
                                   "metadata.google.internal", "1.2.3", "http://adfs.bank.example"])
def test_an_extra_entry_that_names_no_public_host_refuses_the_boot(entry):
    from meeting_api.bot_accounts.service import parse_signin_extra_hosts
    with pytest.raises(ValueError, match="VEXA_BOT_SIGNIN_EXTRA_HOSTS"):
        parse_signin_extra_hosts(f"adfs.bank.example,{entry}")


@pytest.mark.parametrize("body", [{"platform": "skype"}, {"platform": "google", "owner": "them"},
                                  {"platform": "google", "display_name": ""},
                                  {"platform": "google", "account_id": "nope"}])
def test_a_malformed_session_request_is_422(rig, body):
    r = rig.client.post("/bot-accounts/sessions", headers=rig.as_(OWNER), json=body)
    assert r.status_code == 422, r.text


def test_one_open_session_per_person(rig):
    first = rig.open().json()["session_id"]
    assert rig.open().status_code == 409
    assert rig.open(uid=OTHER).status_code == 201  # somebody else is not held
    assert rig.client.delete(f"/bot-accounts/sessions/{first}", headers=rig.as_(OWNER)).json() == {
        "session_id": first, "status": "closed"}
    assert f"bs-{first}" in rig.runtime.deleted
    assert rig.open().status_code == 201


def test_only_the_owner_reads_views_or_closes_a_session(rig):
    sid = rig.open().json()["session_id"]
    for method, path in (("GET", f"/bot-accounts/sessions/{sid}"),
                         ("DELETE", f"/bot-accounts/sessions/{sid}"),
                         ("GET", f"/bot-accounts/sessions/{sid}/view/vnc.html")):
        assert rig.client.request(method, path, headers=rig.as_(OTHER)).status_code == 404
    assert rig.client.get(f"/bot-accounts/sessions/{'f' * 32}", headers=rig.as_(OWNER)).status_code == 404


def test_session_status_turns_ready_when_the_workload_answers_and_carries_the_password(rig):
    opened = rig.open().json()
    sid = opened["session_id"]
    rig.upstream.ready = False
    r = rig.client.get(f"/bot-accounts/sessions/{sid}", headers=rig.as_(OWNER)).json()
    assert r["status"] == "starting"
    rig.upstream.ready = True
    r = rig.client.get(f"/bot-accounts/sessions/{sid}", headers=rig.as_(OWNER)).json()
    assert r == {"session_id": sid, "status": "ready", "platform": "google", "owner": f"user:{OWNER}",
                 "account_id": None, "detail": None, "expires_at": opened["expires_at"],
                 "vnc_password": opened["vnc_password"]}


def test_a_workload_that_stops_before_saving_fails_the_session(rig):
    sid = rig.open().json()["session_id"]
    rig.runtime._workloads = {f"bs-{sid}": {"workloadId": f"bs-{sid}", "state": "stopped"}}
    r = rig.client.get(f"/bot-accounts/sessions/{sid}", headers=rig.as_(OWNER)).json()
    assert r["status"] == "failed" and r["detail"]
    assert rig.open().status_code == 201  # the hold is released


def test_a_company_session_needs_the_admin_account(rig):
    assert rig.open(owner="company").status_code == 403
    assert rig.open(uid=PERSON_ADMIN, owner="company").status_code == 403
    r = rig.open(uid=ADMIN_ACCOUNT, owner="company")
    assert r.status_code == 201 and r.json()["owner"] == "company"


def test_the_runtime_refusing_the_workload_is_a_502_and_releases_the_hold(rig):
    rig.runtime._fail = True
    assert rig.open().status_code == 502
    rig.runtime._fail = False
    assert rig.open().status_code == 201


def test_the_reaper_closes_an_expired_session_and_stops_its_workload(rig):
    import asyncio

    sid = rig.open().json()["session_id"]
    now = rig.ba.clock()
    rig.ba.clock = lambda: now + 10_000
    assert asyncio.run(rig.ba.reap()) == 1
    assert f"bs-{sid}" in rig.runtime.deleted
    rig.ba.clock = lambda: now
    assert rig.client.get(f"/bot-accounts/sessions/{sid}", headers=rig.as_(OWNER)).json()["status"] == "closed"


# ── saving the signed-in profile ─────────────────────────────────────────────────────────────────

def test_the_workload_saves_a_new_account(rig):
    sid = rig.open().json()["session_id"]
    token = rig.last_env()["token"]
    r = rig.save(sid, token)
    assert r.status_code == 200, r.text
    aid = r.json()["account_id"]
    assert r.json() == {"account_id": aid, "files": 2}
    acct = rig.broker.accounts[aid]
    assert acct["owner"] == f"user:{OWNER}" and acct["platform"] == "google"
    assert acct["display_name"] == "Google bot account" and acct["created_by"] == f"user:{OWNER}"
    assert dict(rig.broker.files[aid]) == PROFILE
    assert ("put", f"user:{OWNER}") in rig.broker.calls
    assert f"bs-{sid}" in rig.runtime.deleted  # stopped after answering
    s = rig.client.get(f"/bot-accounts/sessions/{sid}", headers=rig.as_(OWNER)).json()
    assert s["status"] == "saved" and s["account_id"] == aid
    assert rig.save(sid, token).status_code == 409  # a saved session saves once


def test_save_refusals(rig):
    sid = rig.open().json()["session_id"]
    token = rig.last_env()["token"]
    assert rig.save(sid, "wrong").status_code == 401
    assert rig.client.put(f"/internal/bot-accounts/sessions/{sid}/profile",
                          json=_files_body()).status_code == 401
    assert rig.save(sid, token, files={"../etc/passwd": b"x"}).status_code == 422
    assert rig.save(sid, token, files={"Default/Extensions/x": b"x"}).status_code == 422
    assert rig.broker.accounts == {}


def test_signing_in_again_replaces_the_profile_of_the_same_account(rig):
    acct = rig.seed(f"user:{OWNER}", state="signed_out")
    sid = rig.open(account_id=acct["id"]).json()["session_id"]
    r = rig.save(sid, rig.last_env()["token"], files={"Default/Cookies": b"fresh"})
    assert r.json()["account_id"] == acct["id"]
    assert rig.broker.accounts[acct["id"]]["state"] == "ok"
    assert dict(rig.broker.files[acct["id"]]) == {"Default/Cookies": b"fresh"}


def test_signing_in_again_somebody_elses_account_is_404_and_a_company_one_needs_the_admin_account(rig):
    theirs = rig.seed(f"user:{OTHER}")
    assert rig.open(account_id=theirs["id"]).status_code == 404
    company = rig.seed("company")
    assert rig.open(account_id=company["id"]).status_code == 403
    assert rig.open(uid=ADMIN_ACCOUNT, account_id=company["id"]).status_code == 201


# ── listing, policy, delete ──────────────────────────────────────────────────────────────────────

def test_list_marks_mine_and_usable_under_each_policy(rig):
    mine = rig.seed(f"user:{OWNER}")
    company = rig.seed("company")
    rig.seed(f"user:{OTHER}")

    def listing(uid):
        body = rig.client.get("/bot-accounts", headers=rig.as_(uid)).json()
        return body["company_bot_use"], {a["id"]: (a["mine"], a["usable"]) for a in body["accounts"]}

    assert listing(OWNER) == ("admins", {mine["id"]: (True, True), company["id"]: (False, False)})
    assert listing(PERSON_ADMIN)[1] == {company["id"]: (False, True)}
    rig.policy = "all"
    assert listing(OWNER)[1][company["id"]] == (False, True)
    rig.policy = "none"
    assert listing(PERSON_ADMIN)[1][company["id"]] == (False, False)
    assert listing(ADMIN_ACCOUNT)[1][company["id"]] == (False, False)


def test_delete(rig):
    mine = rig.seed(f"user:{OWNER}")
    company = rig.seed("company")
    theirs = rig.seed(f"user:{OTHER}")
    assert rig.client.delete(f"/bot-accounts/{theirs['id']}", headers=rig.as_(OWNER)).status_code == 404
    assert rig.client.delete(f"/bot-accounts/{company['id']}", headers=rig.as_(PERSON_ADMIN)).status_code == 403
    assert rig.client.delete(f"/bot-accounts/{mine['id']}", headers=rig.as_(OWNER)).json() == {
        "account_id": mine["id"], "deleted": True}
    assert rig.client.delete(f"/bot-accounts/{company['id']}", headers=rig.as_(ADMIN_ACCOUNT)).status_code == 200
    assert set(rig.broker.accounts) == {theirs["id"]}
    assert rig.client.delete(f"/bot-accounts/{mine['id']}", headers=rig.as_(OWNER)).status_code == 404


# ── verify ───────────────────────────────────────────────────────────────────────────────────────

def test_verify_restores_checks_and_flips_the_state(rig):
    acct = rig.seed(f"user:{OWNER}")
    r = rig.client.post(f"/bot-accounts/{acct['id']}/verify", headers=rig.as_(OWNER))
    assert r.status_code == 202, r.text
    cid = r.json()["check_id"]
    assert r.json() == {"account_id": acct["id"], "check_id": cid, "status": "checking"}
    env = rig.last_env()
    assert env["mode"] == "verify" and env["session_id"] == cid
    assert env["restore_url"] == f"{BASE}/internal/browser-session-check/{cid}"
    assert env["result_url"] == f"{BASE}/internal/bot-accounts/checks/{cid}"
    auth = {"Authorization": f"Bearer {env['token']}"}
    assert rig.client.get(f"/internal/browser-session-check/{cid}",
                          headers={"Authorization": "Bearer wrong"}).status_code == 401
    files = rig.client.get(f"/internal/browser-session-check/{cid}", headers=auth).json()["files"]
    assert {f["path"]: base64.b64decode(f["data"]) for f in files} == PROFILE
    r = rig.client.post(f"/internal/bot-accounts/checks/{cid}", headers=auth,
                        json={"logged_in": False, "detail": "sign-in page"})
    assert r.json() == {"account_id": acct["id"], "state": "signed_out"}
    assert rig.broker.accounts[acct["id"]]["state"] == "signed_out"
    assert f"bc-{cid}" in rig.runtime.deleted
    assert rig.client.post(f"/internal/bot-accounts/checks/{cid}", headers=auth,
                           json={"logged_in": True, "detail": ""}).status_code == 409


def test_verify_refusals(rig):
    theirs = rig.seed(f"user:{OTHER}")
    company = rig.seed("company")
    assert rig.client.post(f"/bot-accounts/{theirs['id']}/verify", headers=rig.as_(OWNER)).status_code == 404
    assert rig.client.post(f"/bot-accounts/{company['id']}/verify", headers=rig.as_(OWNER)).status_code == 403
    assert rig.client.post(f"/bot-accounts/{'c' * 32}/verify", headers=rig.as_(OWNER)).status_code == 404


# ── the live view ────────────────────────────────────────────────────────────────────────────────

def test_the_view_proxies_novnc_files_for_the_owner(rig):
    sid = rig.open().json()["session_id"]
    rig.upstream.files = {f"http://vexa-bs-{sid}:6080/vnc.html": (200, "text/html; charset=utf-8", b"<html/>"),
                          f"http://vexa-bs-{sid}:6080/core/rfb.js": (200, "text/javascript", b"rfb")}
    r = rig.client.get(f"/bot-accounts/sessions/{sid}/view/vnc.html", headers=rig.as_(OWNER))
    assert r.status_code == 200 and r.content == b"<html/>" and r.headers["content-type"].startswith("text/html")
    r = rig.client.get(f"/bot-accounts/sessions/{sid}/view/core/rfb.js", headers=rig.as_(OWNER))
    assert r.headers["content-type"].startswith("text/javascript")
    assert rig.client.get(f"/bot-accounts/sessions/{sid}/view/app/missing.js",
                          headers=rig.as_(OWNER)).status_code == 404
    assert rig.client.post(f"/bot-accounts/sessions/{sid}/view/vnc.html",
                           headers=rig.as_(OWNER)).status_code == 405


@pytest.mark.parametrize("path", ["app/..%2F..%2Fetc/passwd", "app/%2e%2e/x.js", "etc/passwd",
                                  "app/.hidden", "app//x.js", "vnc.html%3Fx"])
def test_the_view_refuses_anything_but_the_novnc_files(rig, path):
    sid = rig.open().json()["session_id"]
    before = len(rig.upstream.fetched)
    r = rig.client.get(f"/bot-accounts/sessions/{sid}/view/{path}", headers=rig.as_(OWNER))
    assert r.status_code == 404, (path, r.status_code)
    assert len(rig.upstream.fetched) == before  # never forwarded


@pytest.mark.parametrize("path,ok", [("vnc.html", True), ("vnc_lite.html", True), ("app/ui.js", True),
                                     ("core/rfb.js", True), ("vendor/pako/lib/zlib/inflate.js", True),
                                     ("../x", False), ("app/../x", False), ("/etc/passwd", False),
                                     ("secret.txt", False), ("home/x.js", False), ("app\\x.js", False)])
def test_view_path_rule(path, ok):
    assert (view_path_refusal(path) is None) is ok


def test_the_socket_relays_binary_frames_both_ways(rig):
    sid = rig.open().json()["session_id"]
    with rig.client.websocket_connect(f"/bot-accounts/sessions/{sid}/vnc", headers=rig.as_(OWNER),
                                      subprotocols=["binary"]) as ws:
        assert ws.receive_bytes() == b"RFB 003.008\n"
        ws.send_bytes(b"\x01\x02")
        assert ws.receive_bytes() == b"echo:\x01\x02"
    assert rig.upstream.connected == [f"ws://vexa-bs-{sid}:6080/websockify"]
    assert rig.upstream.sockets[0].sent == [b"\x01\x02"]


def test_the_socket_refuses_anybody_but_the_owner_with_4404(rig):
    sid = rig.open().json()["session_id"]
    with pytest.raises(WebSocketDisconnect) as e:
        with rig.client.websocket_connect(f"/bot-accounts/sessions/{sid}/vnc", headers=rig.as_(OTHER)):
            pass
    assert e.value.code == 4404
    assert rig.upstream.connected == []


def test_a_signed_identity_reaches_the_socket_and_the_view_with_the_bot_scope(monkeypatch):
    """route_scopes holds the gateway's relay socket to the session row's scope, and the view route
    has its own row: a signed `bot` key passes both, a `tx` key is refused."""
    monkeypatch.delenv("BOT_AUTHENTICATED", raising=False)
    key = identity_token.generate_signing_key()
    rig = Rig(identity_key=key.public_key())

    def signed(scopes):
        return {identity_token.HEADER: identity_token.sign(key, {"sub": str(OWNER), "scopes": scopes})}

    sid = rig.client.post("/bot-accounts/sessions", headers=signed(["bot"]),
                          json={"platform": "google"}).json()["session_id"]
    with rig.client.websocket_connect(f"/bot-accounts/sessions/{sid}/vnc", headers=signed(["bot"])) as ws:
        assert ws.receive_bytes() == b"RFB 003.008\n"
    rig.upstream.files = {f"http://vexa-bs-{sid}:6080/vnc.html": (200, "text/html", b"<html/>")}
    assert rig.client.get(f"/bot-accounts/sessions/{sid}/view/vnc.html", headers=signed(["bot"])).status_code == 200
    assert rig.client.get(f"/bot-accounts/sessions/{sid}/view/vnc.html", headers=signed(["browser"])).status_code == 403
    with pytest.raises(Exception):
        with rig.client.websocket_connect(f"/bot-accounts/sessions/{sid}/vnc", headers=signed(["browser"])):
            pass


# ── a meeting bot that joins as a bot account ────────────────────────────────────────────────────

def _spawn(rig, uid=OWNER, native="abc-defg-hij", **extra):
    return rig.client.post("/bots", headers=rig.as_(uid),
                           json={"platform": "google_meet", "native_meeting_id": native, **extra})


def _inv(rig):
    return json.loads(rig.runtime.specs[-1]["env"]["VEXA_BOT_CONFIG"])


def test_a_spawn_joins_as_the_callers_newest_verified_account(rig):
    rig.seed(f"user:{OWNER}", verified_at="2026-09-01T00:00:00Z")
    newest = rig.seed(f"user:{OWNER}", verified_at="2026-10-01T00:00:00Z")
    rig.seed(f"user:{OWNER}", platform="zoom", verified_at="2026-10-05T00:00:00Z")
    rig.seed(f"user:{OWNER}", state="signed_out", verified_at="2026-10-09T00:00:00Z")
    r = _spawn(rig)
    assert r.status_code == 201, r.text
    inv = _inv(rig)
    conn = inv["connectionId"]
    assert inv["authenticated"] is True
    assert inv["sessionRestoreUrl"] == f"{BASE}/internal/browser-session/{conn}"
    assert inv["sessionWritebackUrl"] == f"{BASE}/internal/browser-session/{conn}"
    assert not any(k in inv for k in ("userdataS3Path", "s3Endpoint", "s3AccessKey", "s3SecretKey"))
    row = rig.repo._meetings[r.json()["id"]]
    assert row["data"]["bot_account_id"] == newest["id"]
    assert row["data"]["auth_userdata_path"] == f"bot-account:{newest['id']}"


def test_none_or_no_account_spawns_todays_guest_bot(rig):
    rig.seed(f"user:{OWNER}")
    _spawn(rig, bot_account="none")
    assert "authenticated" not in _inv(rig)
    _spawn(rig, uid=OTHER, native="abc-defg-hik")  # no account of their own, no usable company one
    assert "authenticated" not in _inv(rig)


def test_the_default_falls_back_to_a_usable_company_account(rig):
    company = rig.seed("company")
    _spawn(rig)
    assert "authenticated" not in _inv(rig)  # admins policy: a person who is no admin
    r = _spawn(rig, uid=PERSON_ADMIN, native="abc-defg-hik")
    assert rig.repo._meetings[r.json()["id"]]["data"]["bot_account_id"] == company["id"]
    rig.policy = "none"
    r = _spawn(rig, uid=PERSON_ADMIN, native="abc-defg-hil")
    assert "bot_account_id" not in rig.repo._meetings[r.json()["id"]]["data"]


def test_an_explicit_account_is_held_to_every_rule(rig):
    theirs = rig.seed(f"user:{OTHER}")
    signed_out = rig.seed(f"user:{OWNER}", state="signed_out")
    zoom = rig.seed(f"user:{OWNER}", platform="zoom")
    company = rig.seed("company")
    mine = rig.seed(f"user:{OWNER}")
    assert _spawn(rig, bot_account=theirs["id"]).status_code == 404
    r = _spawn(rig, bot_account=signed_out["id"])
    assert r.status_code == 409 and r.json()["detail"]["bot_account"]["remedy"] == "browser_session_open"
    assert _spawn(rig, bot_account=zoom["id"]).status_code == 422
    assert _spawn(rig, bot_account=company["id"]).status_code == 403
    assert _spawn(rig, bot_account="not-an-id").status_code == 422
    assert _spawn(rig, bot_account=123).status_code == 422
    assert rig.repo._meetings == {}  # no refused spawn wrote a row
    r = _spawn(rig, bot_account=mine["id"])
    assert r.status_code == 201
    assert rig.repo._meetings[r.json()["id"]]["data"]["bot_account_id"] == mine["id"]


def test_one_live_bot_per_account(rig):
    acct = rig.seed(f"user:{OWNER}")
    assert _spawn(rig).status_code == 201
    r = _spawn(rig, native="abc-defg-hik")
    assert r.status_code == 409 and acct["id"] in r.json()["detail"]
    assert _spawn(rig, native="abc-defg-hil", bot_account="none").status_code == 201


def test_a_store_that_cannot_answer_spawns_a_guest_rather_than_failing(rig):
    rig.seed(f"user:{OWNER}")
    rig.broker.fail = True
    assert _spawn(rig).status_code == 201
    assert "authenticated" not in _inv(rig)


# ── restore, write-back, signed out ──────────────────────────────────────────────────────────────

def _spawned(rig):
    acct = rig.seed(f"user:{OWNER}")
    r = _spawn(rig)
    inv = _inv(rig)
    return acct, r.json()["id"], inv


def test_the_bot_restores_its_accounts_profile(rig):
    acct, _mid, inv = _spawned(rig)
    path = urlsplit(inv["sessionRestoreUrl"]).path
    r = rig.client.get(path, headers={"Authorization": f"Bearer {inv['token']}"})
    assert r.status_code == 200, r.text
    assert {f["path"]: base64.b64decode(f["data"]) for f in r.json()["files"]} == PROFILE


def test_restore_refusals(rig):
    acct, mid, inv = _spawned(rig)
    path = urlsplit(inv["sessionRestoreUrl"]).path
    assert rig.client.get(path).status_code == 401
    assert rig.client.get(path, headers={"Authorization": "Bearer nope"}).status_code == 401
    # Another session's token
    assert rig.client.get("/internal/browser-session/other",
                          headers={"Authorization": f"Bearer {inv['token']}"}).status_code == 401
    # A meeting without an account
    _spawn(rig, native="abc-defg-hik", bot_account="none")
    guest = _inv(rig)
    r = rig.client.get(f"/internal/browser-session/{guest['connectionId']}",
                       headers={"Authorization": f"Bearer {guest['token']}"})
    assert r.status_code == 404
    # A meeting that is over
    rig.repo._meetings[mid]["status"] = "completed"
    assert rig.client.get(path, headers={"Authorization": f"Bearer {inv['token']}"}).status_code == 403


def test_the_write_back_of_an_account_bot_goes_to_the_account(rig):
    acct, _mid, inv = _spawned(rig)
    path = urlsplit(inv["sessionWritebackUrl"]).path
    r = rig.client.put(path, headers={"Authorization": f"Bearer {inv['token']}"},
                       json=_files_body({"Default/Cookies": b"rotated"}))
    assert r.status_code == 200, r.text
    assert dict(rig.broker.files[acct["id"]]) == {"Default/Cookies": b"rotated"}
    bad = rig.client.put(path, headers={"Authorization": f"Bearer {inv['token']}"},
                         json=_files_body({"Default/Extensions/x": b"x"}))
    assert bad.status_code == 422


def test_a_bot_that_found_its_account_signed_out_flips_it_and_the_meeting_says_so(rig):
    acct, mid, inv = _spawned(rig)
    cb = "/bots/internal/callback/lifecycle"
    assert rig.client.post(cb, headers={"Authorization": f"Bearer {inv['token']}"}, json={"connection_id": inv["connectionId"], "status": "joining"}).status_code == 200
    r = rig.client.post(cb, headers={"Authorization": f"Bearer {inv['token']}"}, json={"connection_id": inv["connectionId"], "status": "failed", "exit_code": 1,
                                  "failure_stage": "joining", "completion_reason": "auth_session_missing",
                                  "reason": "signed out"})
    assert r.status_code == 200, r.text
    assert rig.broker.accounts[acct["id"]]["state"] == "signed_out"
    assert rig.repo._meetings[mid]["data"]["bot_account"] == {
        "id": acct["id"], "state": "signed_out", "remedy": "browser_session_open"}
    # …and that bot's teardown write-back does not overwrite the account with a signed-out profile.
    path = urlsplit(inv["sessionWritebackUrl"]).path
    w = rig.client.put(path, headers={"Authorization": f"Bearer {inv['token']}"}, json=_files_body())
    assert w.status_code == 409
    assert rig.broker.accounts[acct["id"]]["state"] == "signed_out"


def test_a_guest_bot_that_ends_signed_out_touches_no_account(rig):
    acct = rig.seed(f"user:{OWNER}")
    _spawn(rig, bot_account="none")
    inv = _inv(rig)
    cb = "/bots/internal/callback/lifecycle"
    rig.client.post(cb, headers={"Authorization": f"Bearer {inv['token']}"}, json={"connection_id": inv["connectionId"], "status": "joining"})
    rig.client.post(cb, headers={"Authorization": f"Bearer {inv['token']}"}, json={"connection_id": inv["connectionId"], "status": "failed", "exit_code": 1,
                              "failure_stage": "joining", "completion_reason": "auth_session_missing"})
    assert rig.broker.accounts[acct["id"]]["state"] == "ok"
    assert ("state", "meeting-api") not in rig.broker.calls


# ── the broker adapter ───────────────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class _Client:
    def __init__(self, resp):
        self.resp = resp
        self.sent = []

    async def post(self, url, *, content, headers, timeout):
        self.sent.append((url, content, headers))
        return self.resp


async def test_the_http_broker_signs_each_request_as_role_bots():
    key = b"k" * 40
    client = _Client(_Resp(200, {"accounts": [], "files": None}))
    broker = HttpBrokerClient("http://credential-broker:8100", key, client=client)
    assert await broker.list(["user:7", "company"], actor="user:7") == []
    url, content, headers = client.sent[0]
    assert url == "http://credential-broker:8100/api/internal/bot-account"
    assert json.loads(content) == {"action": "list", "owners": ["user:7", "company"]}
    encoded, _, sig = headers[broker_assertion.HEADER].partition(".")
    import hashlib
    import hmac as _hmac
    assert sig == _hmac.new(key, encoded.encode(), hashlib.sha256).hexdigest()
    decoded = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    assert decoded["role"] == "bots" and decoded["actor"] == "user:7" and decoded["method"] == "POST"
    assert decoded["path"] == "/api/internal/bot-account"
    assert decoded["body"] == hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize("status,exc", [(404, "BrokerNotFound"), (409, "BrokerConflict"),
                                        (403, "BrokerNotConfigured"), (500, "BrokerError")])
async def test_the_http_broker_types_each_refusal(status, exc):
    from meeting_api import bot_accounts as B

    broker = HttpBrokerClient("http://b", b"k" * 40, client=_Client(_Resp(status, {})))
    with pytest.raises(getattr(B, exc)):
        await broker.get("a" * 32, actor="meeting-api")


def test_broker_from_env(tmp_path):
    from meeting_api.bot_accounts import BrokerNotConfigured, broker_from_env

    assert broker_from_env({}) is None
    assert broker_from_env({"VEXA_BOT_ACCOUNTS_BROKER_URL": "http://b"}) is None
    short = tmp_path / "short"
    short.write_bytes(b"x")
    with pytest.raises(BrokerNotConfigured):
        broker_from_env({"VEXA_BOT_ACCOUNTS_BROKER_URL": "http://b", "VEXA_BOT_ACCOUNTS_KEY_FILE": str(short)})
    good = tmp_path / "key"
    good.write_bytes(b"z" * 48 + b"\n")
    assert isinstance(broker_from_env({"VEXA_BOT_ACCOUNTS_BROKER_URL": "http://b",
                                       "VEXA_BOT_ACCOUNTS_KEY_FILE": str(good)}), HttpBrokerClient)


async def test_the_admin_oracle_fails_closed_and_caches_only_answers():
    from meeting_api.bot_accounts import HttpAdminOracle

    class C:
        def __init__(self):
            self.n = 0
            self.status = 500

        async def get(self, url, headers, timeout):
            self.n += 1
            return _Resp(self.status, {"is_admin": True, "admin_account": False})

    assert await HttpAdminOracle("", "s").roles(1) == {"is_admin": False, "admin_account": False}
    c = C()
    oracle = HttpAdminOracle("http://admin-api", "s", client=c)
    assert await oracle.roles(1) == {"is_admin": False, "admin_account": False}
    c.status = 200
    assert await oracle.roles(1) == {"is_admin": True, "admin_account": False}
    await oracle.roles(1)
    assert c.n == 2  # the failure was not cached; the answer was


def test_company_bot_use_policy_env():
    from meeting_api.bot_accounts import company_bot_use

    assert company_bot_use({}) == "admins"
    assert company_bot_use({"VEXA_COMPANY_BOT_USE": "ALL"}) == "all"
    assert company_bot_use({"VEXA_COMPANY_BOT_USE": "none"}) == "none"
    assert company_bot_use({"VEXA_COMPANY_BOT_USE": "everyone"}) == "admins"


def test_a_key_path_with_no_file_yet_is_not_configured_and_never_refuses_the_boot(tmp_path, capsys):
    # An upgraded stack has the path wired before its key generator has run again.
    from meeting_api.bot_accounts.broker import broker_from_env
    env = {"VEXA_BOT_ACCOUNTS_BROKER_URL": "http://broker.test",
           "VEXA_BOT_ACCOUNTS_KEY_FILE": str(tmp_path / "absent" / "key")}
    assert broker_from_env(env) is None
    assert "bot_accounts_unconfigured" in capsys.readouterr().out
