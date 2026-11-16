"""bot_accounts (ADR-0049) — browser sessions, saved bot accounts, and a meeting bot that joins as one.

Drives the SHIPPED app (`create_app`) over fakes: the broker (`FakeBroker`, the broker's own
semantics), the runtime (`FakeRuntimeClient`), meeting-api's Redis (fakeredis under
`RedisSessionStore`), admin-api's roles (`FakeAdminOracle`) and the workload's noVNC/websockify
(`FakeVncUpstream`).
"""
from __future__ import annotations

import asyncio

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


def test_a_company_session_needs_the_admin_account_or_an_instance_admin(rig):
    assert rig.open(owner="company").status_code == 403
    # P14 (rev 4.1): under VEXA_BROWSER_BOTS_WHO=all (the default) an instance admin manages company accounts.
    assert rig.open(uid=PERSON_ADMIN, owner="company").status_code == 201
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

    # A company account is listed only to someone who may use or manage it (S1a break-it M-b).
    assert listing(OWNER) == ("admins", {mine["id"]: (True, True)})
    assert listing(PERSON_ADMIN)[1] == {company["id"]: (False, True)}
    rig.policy = "all"
    assert listing(OWNER)[1][company["id"]] == (False, True)
    rig.policy = "none"
    assert listing(PERSON_ADMIN)[1][company["id"]] == (False, False)      # under `all` an instance admin manages it
    assert listing(ADMIN_ACCOUNT)[1][company["id"]] == (False, False)     # the admin account manages it
    assert listing(ADMIN_ACCOUNT)[1][company["id"]] == (False, False)


def test_delete(rig):
    mine = rig.seed(f"user:{OWNER}")
    company = rig.seed("company")
    theirs = rig.seed(f"user:{OTHER}")
    assert rig.client.delete(f"/bot-accounts/{theirs['id']}", headers=rig.as_(OWNER)).status_code == 404
    assert rig.client.delete(f"/bot-accounts/{company['id']}", headers=rig.as_(OWNER)).status_code == 403
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


def test_the_default_falls_back_to_a_usable_company_account(rig, monkeypatch):
    monkeypatch.setenv("VEXA_MEET_ROOM_ROUTING", "1")
    company = rig.seed("company")
    _spawn(rig)
    assert "authenticated" not in _inv(rig)  # admins policy: a person who is no admin
    # D402: a Meet room nobody has seen joins anonymous, even with a usable company account …
    r = _spawn(rig, uid=PERSON_ADMIN, native="abc-defg-hik")
    assert "bot_account_id" not in rig.repo._meetings[r.json()["id"]]["data"]
    # … and a room that refused an anonymous bot joins as the company account from the first try.
    _refused_room(rig, "abc-defg-hil")
    r = _spawn(rig, uid=PERSON_ADMIN, native="abc-defg-hil")
    assert rig.repo._meetings[r.json()["id"]]["data"]["bot_account_id"] == company["id"]
    rig.policy = "none"
    _refused_room(rig, "abc-defg-him")
    r = _spawn(rig, uid=PERSON_ADMIN, native="abc-defg-him")
    assert "bot_account_id" not in rig.repo._meetings[r.json()["id"]]["data"]


def _refused_room(rig, native):
    """A past meeting of the room (another person's) whose anonymous bot Meet refused an hour ago."""
    from datetime import datetime, timedelta, timezone
    at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    m = asyncio.run(rig.repo.create_meeting(user_id=OTHER, platform="google_meet", native_meeting_id=native,
                                            data={"meet_respawn": {"state": "exhausted",
                                                                   "attempts": [{"at": at, "signed_in": False}]}}))
    rig.repo._meetings[m["id"]].update(status="failed", created_at=at)


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


def test_one_account_serves_many_meetings_up_to_the_cap(rig, monkeypatch):
    """Founder, 16 Nov 2026: no one-bot-per-account; a cap per account (VEXA_BOT_ACCOUNT_MAX_MEETINGS)."""
    monkeypatch.setenv("VEXA_BOT_ACCOUNT_MAX_MEETINGS", "2")
    acct = rig.seed(f"user:{OWNER}")
    assert _spawn(rig).status_code == 201
    assert "authenticated" in _inv(rig)
    r = _spawn(rig, native="abc-defg-hik")
    assert r.status_code == 201, r.text                     # a second meeting on the same account
    assert "authenticated" in _inv(rig)
    r = _spawn(rig, native="abc-defg-hil")                  # the account is full: a guest bot
    assert r.status_code == 201, r.text
    assert "authenticated" not in _inv(rig)
    assert acct["id"]


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


def test_two_bots_on_one_account_both_restore_and_only_the_last_one_out_writes_back(rig):
    """Founder, 16 Nov 2026: many bots per account, and a write-back never lets an older profile replace a
    newer one: while another bot still uses the account, a leaving bot's write-back is deferred."""
    acct = rig.seed(f"user:{OWNER}")
    assert _spawn(rig).status_code == 201
    first = _inv(rig)
    r2 = _spawn(rig, native="abc-defg-hik")
    assert r2.status_code == 201, r2.text
    second = _inv(rig)
    for inv in (first, second):   # each live bot restores the account's profile
        r = rig.client.get(urlsplit(inv["sessionRestoreUrl"]).path, headers={"Authorization": f"Bearer {inv['token']}"})
        assert r.status_code == 200, r.text
    # the first bot leaves while the second is still in its meeting: deferred, nothing written
    w = rig.client.put(urlsplit(first["sessionWritebackUrl"]).path, headers={"Authorization": f"Bearer {first['token']}"},
                       json=_files_body({"Default/Cookies": b"older"}))
    assert w.status_code == 200 and w.json().get("deferred"), w.text
    assert dict(rig.broker.files[acct["id"]]) != {"Default/Cookies": b"older"}
    # end the first meeting; the second bot is now the last one out and writes back
    first_mid = next(m["id"] for m in rig.repo._meetings.values()
                     if m.get("native_meeting_id") not in ("abc-defg-hik",) and (m.get("data") or {}).get("bot_account_id"))
    rig.repo._meetings[first_mid]["status"] = "completed"
    w = rig.client.put(urlsplit(second["sessionWritebackUrl"]).path, headers={"Authorization": f"Bearer {second['token']}"},
                       json=_files_body({"Default/Cookies": b"newest"}))
    assert w.status_code == 200 and not w.json().get("deferred"), w.text
    assert dict(rig.broker.files[acct["id"]]) == {"Default/Cookies": b"newest"}


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


def test_routing_off_a_default_meet_bot_never_takes_a_company_account(rig, monkeypatch):
    """Cut-over S2 / RB-R: with the routing flag off every Meet bot joins anonymously, even an admin's in a
    room that refused one."""
    monkeypatch.delenv("VEXA_MEET_ROOM_ROUTING", raising=False)
    rig.seed("company")
    _refused_room(rig, "abc-defg-hin")
    r = _spawn(rig, uid=PERSON_ADMIN, native="abc-defg-hin")
    assert "bot_account_id" not in rig.repo._meetings[r.json()["id"]]["data"]



# ── S1 (prod rung MVP0): sessions and company accounts open to the admin account alone ───────────
from meeting_api.bot_accounts.policy import usable
from meeting_api.bot_accounts.service import SESSIONS_ADMIN_ONLY, _sessions_who


def _admin_only(rig):
    rig.ba.settings = Settings(callback_base=BASE, sessions_who="admin_account")
    rig.policy = "admin_account"
    return rig


@pytest.mark.parametrize("uid", [OWNER, OTHER, PERSON_ADMIN])
def test_with_sessions_for_the_admin_account_nobody_else_opens_one_and_no_workload_starts(rig, uid):
    _admin_only(rig)
    before = len(rig.runtime.specs)
    for owner in ("me", "company"):
        r = rig.open(uid, owner=owner)
        assert r.status_code == 403, r.text
        assert SESSIONS_ADMIN_ONLY in r.text
    assert len(rig.runtime.specs) == before


def test_with_sessions_for_the_admin_account_the_admin_account_still_opens_one(rig):
    _admin_only(rig)
    r = rig.open(ADMIN_ACCOUNT, owner="company")
    assert r.status_code in (200, 201), r.text
    assert len(rig.runtime.specs) == 1


def test_with_sessions_for_the_admin_account_nobody_else_checks_a_stored_account(rig):
    _admin_only(rig)
    aid = rig.seed("company")["id"] if isinstance(rig.seed("company"), dict) else None
    acc = rig.broker.accounts
    any_id = next(iter(acc)) if isinstance(acc, dict) else "a" * 32
    before = len(rig.runtime.specs)
    r = rig.client.post(f"/bot-accounts/{any_id}/verify", headers=rig.as_(PERSON_ADMIN))
    assert r.status_code == 403, r.text
    assert len(rig.runtime.specs) == before


def test_company_use_admin_account_opens_company_accounts_to_the_admin_account_alone():
    company, mine = {"owner": "company"}, {"owner": f"user:{OWNER}"}
    assert usable(company, user_id=ADMIN_ACCOUNT, policy="admin_account", roles={"admin_account": True})
    assert not usable(company, user_id=PERSON_ADMIN, policy="admin_account", roles={"is_admin": True})
    assert not usable(company, user_id=OWNER, policy="admin_account", roles={})
    assert usable(mine, user_id=OWNER, policy="admin_account", roles={})          # their own, as before
    from meeting_api.bot_accounts.policy import company_bot_use
    assert company_bot_use({"VEXA_COMPANY_BOT_USE": "admin_account"}) == "admin_account"


def test_sessions_who_reads_all_by_default_and_narrows_on_anything_else():
    assert _sessions_who(None) == "all" and _sessions_who("") == "all" and _sessions_who(" ALL ") == "all"
    for v in ("admin_account", "nobody", "x", "admin"):
        assert _sessions_who(v) == "admin_account"
    assert _sessions_who("admins") == "admins" and _sessions_who(" Admins ") == "admins"
    assert Settings.from_env({}).sessions_who == "all"
    assert Settings.from_env({"VEXA_BROWSER_BOTS_WHO": "admin_account"}).sessions_who == "admin_account"



def test_a_browser_session_runs_as_the_signed_in_profile_when_one_is_named(rig, monkeypatch):
    monkeypatch.setenv("VEXA_SIGNED_IN_BOT_PROFILE", "meeting-bot-signed-in")
    assert rig.open(OWNER).status_code in (200, 201)
    assert rig.runtime.specs[-1]["profile"] == "meeting-bot-signed-in"


def test_a_browser_session_is_a_meeting_bot_when_none_is_named(rig, monkeypatch):
    monkeypatch.delenv("VEXA_SIGNED_IN_BOT_PROFILE", raising=False)
    assert rig.open(OWNER).status_code in (200, 201)
    assert rig.runtime.specs[-1]["profile"] == "meeting-bot"



# ── VEXA_BROWSER_BOTS_WHO=admins: an instance admin is the bots' admin (founder, his existing account) ──
from meeting_api.bot_accounts.service import SESSIONS_ADMINS_ONLY
from meeting_api.bot_accounts.policy import NO_ROLES


def _admins(rig):
    rig.ba.settings = Settings(callback_base=BASE, sessions_who="admins")
    rig.policy = "admins"
    return rig


@pytest.mark.parametrize("uid", [PERSON_ADMIN, ADMIN_ACCOUNT])
def test_with_sessions_for_admins_an_instance_admin_or_the_admin_account_signs_in_a_company_account(rig, uid):
    _admins(rig)
    r = rig.open(uid, owner="company")
    assert r.status_code == 201, r.text
    assert r.json()["owner"] == "company" and len(rig.runtime.specs) == 1


@pytest.mark.parametrize("uid", [OWNER, OTHER])
def test_with_sessions_for_admins_a_customer_is_refused_and_no_workload_starts(rig, uid):
    _admins(rig)
    for owner in ("me", "company"):
        r = rig.open(uid, owner=owner)
        assert r.status_code == 403, r.text
        assert SESSIONS_ADMINS_ONLY in r.text
    company = rig.seed("company")
    assert rig.client.post(f"/bot-accounts/{company['id']}/verify", headers=rig.as_(uid)).status_code == 403
    assert rig.client.delete(f"/bot-accounts/{company['id']}", headers=rig.as_(uid)).status_code in (403, 404)
    assert rig.broker.accounts and len(rig.runtime.specs) == 0


def test_with_sessions_for_admins_an_instance_admin_deletes_a_company_account(rig):
    _admins(rig)
    company = rig.seed("company")
    r = rig.client.delete(f"/bot-accounts/{company['id']}", headers=rig.as_(PERSON_ADMIN))
    assert r.status_code == 200, r.text


def test_with_sessions_for_admins_roles_that_cannot_be_read_refuse(rig):
    _admins(rig)

    class Silent:
        async def roles(self, user_id):
            return dict(NO_ROLES)
    rig.ba.admin = Silent()
    assert rig.open(PERSON_ADMIN, owner="company").status_code == 403
    assert len(rig.runtime.specs) == 0


@pytest.mark.parametrize("who", ["admin_account"])
def test_outside_admins_an_instance_admin_still_cannot_sign_in_or_delete_a_company_account(rig, who):
    rig.ba.settings = Settings(callback_base=BASE, sessions_who=who)
    assert rig.open(PERSON_ADMIN, owner="company").status_code == 403
    company = rig.seed("company")
    assert rig.client.delete(f"/bot-accounts/{company['id']}", headers=rig.as_(PERSON_ADMIN)).status_code == 403



# ── S1 review M3: off Meet, a default bot never shares a company account with a live bot ─────────────
async def test_off_meet_a_default_bot_skips_a_busy_company_account_and_is_a_guest_when_all_are(rig):
    a = rig.seed("company", platform="zoom", verified_at="2026-10-02T00:00:00Z")
    b = rig.seed("company", platform="zoom", verified_at="2026-10-01T00:00:00Z")
    from meeting_api.bot_accounts.service import account_meeting_cap
    cap = account_meeting_cap()
    live = {a["id"]: 0, b["id"]: 0}

    async def load(account):
        return live[account["id"]]
    pick = lambda: rig.ba.resolve_for_spawn(PERSON_ADMIN, "zoom", None, room=None, load=load)
    assert (await pick())["id"] in live
    live[a["id"]] = cap
    assert (await pick())["id"] == b["id"]
    live[b["id"]] = cap
    assert await pick() is None
    assert await rig.ba.resolve_for_spawn(OWNER, "zoom", None, room=None, load=load) is None  # a customer: none, as before



# ── S1a break-it pass (run s1a-storm-20261114T173117Z): M-a a hung broker never slows a customer's bot; M-b a
#    customer never sees a company account ────────────────────────────────────────────────────────────────────
import asyncio as _asyncio
import time as _time

from meeting_api.bot_accounts import service as _service


async def test_with_sessions_narrowed_a_customer_default_bot_never_asks_the_broker(rig):
    _admins(rig)
    rig.seed("company", platform="zoom")
    before = len(rig.broker.calls)
    assert await rig.ba.resolve_for_spawn(OWNER, "zoom", None, room=None) is None
    assert len(rig.broker.calls) == before
    assert (await rig.ba.resolve_for_spawn(PERSON_ADMIN, "zoom", None, room=None)) is not None   # an admin still gets one


async def test_a_hung_broker_makes_a_default_bot_a_guest_within_the_bound(rig, monkeypatch):
    _admins(rig)
    monkeypatch.setattr(_service, "DEFAULT_LIST_TIMEOUT_S", 0.2)

    async def hang(*a, **kw):
        await _asyncio.sleep(30)
    monkeypatch.setattr(rig.broker, "list", hang)
    t0 = _time.monotonic()
    assert await rig.ba.resolve_for_spawn(PERSON_ADMIN, "zoom", None, room=None) is None
    assert _time.monotonic() - t0 < 2


@pytest.mark.parametrize("who", ["admins", "admin_account"])
def test_a_customer_listing_shows_no_company_account(rig, who):
    rig.ba.settings = Settings(callback_base=BASE, sessions_who=who)
    rig.policy = "admins"
    company = rig.seed("company")
    mine = rig.seed(f"user:{OWNER}")
    seen = {a["id"] for a in rig.client.get("/bot-accounts", headers=rig.as_(OWNER)).json()["accounts"]}
    assert company["id"] not in seen and mine["id"] in seen
    admin_seen = {a["id"] for a in rig.client.get("/bot-accounts", headers=rig.as_(PERSON_ADMIN)).json()["accounts"]}
    assert company["id"] in admin_seen


# ── GET /bot-accounts answers manages_company: the panel shows company buttons from it alone (L4 review M3) ──
@pytest.mark.parametrize("who,uid,want", [
    ("admins", PERSON_ADMIN, True), ("admins", ADMIN_ACCOUNT, True), ("admins", OWNER, False),
    ("all", PERSON_ADMIN, True), ("all", ADMIN_ACCOUNT, True), ("all", OWNER, False), ("admin_account", PERSON_ADMIN, False),
    ("admin_account", ADMIN_ACCOUNT, True), ("bogus", PERSON_ADMIN, False),
])
def test_the_list_answers_manages_company_as_every_company_action_is_checked(rig, who, uid, want):
    from meeting_api.bot_accounts.service import _sessions_who
    rig.ba.settings = Settings(callback_base=BASE, sessions_who=_sessions_who(who))
    for seeded in (False, True):          # before the first company account exists, and after
        if seeded:
            rig.seed("company")
        body = rig.client.get("/bot-accounts", headers=rig.as_(uid)).json()
        assert body["manages_company"] is want, (who, uid, seeded, body)
    if want:                              # what the list says is what the action does
        assert rig.open(uid, owner="company").status_code == 201


def test_the_list_answers_manages_company_false_when_roles_cannot_be_read(rig):
    _admins(rig)

    class Silent:
        async def roles(self, user_id):
            return dict(NO_ROLES)
    rig.ba.admin = Silent()
    assert rig.client.get("/bot-accounts", headers=rig.as_(PERSON_ADMIN)).json()["manages_company"] is False


# ── P14 · signed-in bots for all (doc:deliver/s1a-design rev 4.1) ─────────────────────────────────────────────
from meeting_api.bot_accounts.service import BROWSERS_FULL


def _all(rig, **kw):
    rig.ba.settings = Settings(callback_base=BASE, sessions_who="all", **kw)
    return rig


def _check(rig, uid, aid):
    return rig.client.post(f"/bot-accounts/{aid}/verify", headers=rig.as_(uid))


def test_p14_under_all_a_customer_signs_in_their_own_and_company_accounts_stay_with_admins(rig):
    _all(rig)
    assert rig.open(OWNER).status_code == 201                          # their own: yes
    assert rig.open(OTHER, owner="company").status_code == 403          # a company one: no
    company = rig.seed("company")
    assert rig.client.delete(f"/bot-accounts/{company['id']}", headers=rig.as_(OTHER)).status_code == 403
    assert rig.open(PERSON_ADMIN, owner="company").status_code == 201   # an instance admin: yes
    assert rig.client.delete(f"/bot-accounts/{company['id']}", headers=rig.as_(PERSON_ADMIN)).status_code == 200


def test_p14_admin_account_mode_stays_narrow_for_company_accounts(rig):
    rig.ba.settings = Settings(callback_base=BASE, sessions_who="admin_account")
    assert rig.open(PERSON_ADMIN, owner="company").status_code == 403
    assert rig.open(ADMIN_ACCOUNT, owner="company").status_code == 201


def test_p14_a_check_takes_the_one_browser_claim(rig):
    _all(rig)
    acct = rig.seed(f"user:{OWNER}")
    r = _check(rig, OWNER, acct["id"])
    assert r.status_code == 202, r.text
    assert rig.open(OWNER).status_code == 409                           # a check open: no second browser
    assert _check(rig, OWNER, acct["id"]).status_code == 409
    assert rig.open(OTHER).status_code == 201                           # somebody else is unaffected


def test_p14_a_session_blocks_a_check(rig):
    _all(rig)
    acct = rig.seed(f"user:{OWNER}")
    assert rig.open(OWNER).status_code == 201
    assert _check(rig, OWNER, acct["id"]).status_code == 409


def test_p14_a_check_result_releases_the_claim(rig):
    _all(rig)
    acct = rig.seed(f"user:{OWNER}")
    cid = _check(rig, OWNER, acct["id"]).json()["check_id"]
    token = rig.last_env()["token"]
    r = rig.client.post(f"/internal/bot-accounts/checks/{cid}", headers={"Authorization": f"Bearer {token}"},
                        json={"logged_in": True, "detail": "ok"})
    assert r.status_code == 200, r.text
    assert asyncio.run(rig.redis.get(f"botsession:user:{OWNER}")) is None
    assert rig.open(OWNER).status_code == 201                           # no 180 s wait


def test_p14_the_reaper_releases_an_expired_checks_claim(rig):
    _all(rig)
    acct = rig.seed(f"user:{OWNER}")
    cid = _check(rig, OWNER, acct["id"]).json()["check_id"]
    now = rig.ba.clock()
    rig.ba.clock = lambda: now + 10_000
    assert asyncio.run(rig.ba.reap()) == 1
    assert f"bc-{cid}" in rig.runtime.deleted
    # The claim's own Redis TTL has not run out (fakeredis keeps real time): only the reaper freed it.
    assert asyncio.run(rig.redis.get(f"botsession:user:{OWNER}")) is None
    rig.ba.clock = lambda: now
    assert rig.open(OWNER).status_code == 201


def test_p14_a_stale_claim_whose_check_is_gone_is_taken_over(rig):
    _all(rig)
    asyncio.run(rig.redis.set(f"botsession:user:{OWNER}", "e" * 32, ex=600))   # a claim with no record
    assert rig.open(OWNER).status_code == 201


def test_p14_a_failed_check_spawn_releases_the_claim(rig):
    _all(rig)
    acct = rig.seed(f"user:{OWNER}")
    rig.runtime._fail = True
    assert _check(rig, OWNER, acct["id"]).status_code == 502
    rig.runtime._fail = False
    assert rig.open(OWNER).status_code == 201


def test_p14_the_deployment_ceiling_is_a_429_before_any_workload(rig):
    _all(rig, max_browsers=10)
    users = list(range(1000, 1011))
    for uid in users[:10]:
        assert rig.open(uid).status_code == 201
    before = len(rig.runtime.specs)
    r = rig.open(users[10])
    assert r.status_code == 429 and BROWSERS_FULL in r.text
    assert len(rig.runtime.specs) == before
    acct = rig.seed(f"user:{users[10]}")
    assert _check(rig, users[10], acct["id"]).status_code == 429        # a check counts the same
    assert asyncio.run(rig.redis.get(f"botsession:user:{users[10]}")) is None   # the refused claim is released
    assert asyncio.run(rig.redis.zcard("botsessions:open")) == 10
    # One closes: the next fits.
    open_ids = asyncio.run(rig.redis.zrange("botsessions:open", 0, -1))
    rid = open_ids[0].split(":", 1)[1]
    owner = json.loads(asyncio.run(rig.redis.get(f"botsession:{rid}")))["user_id"]
    assert rig.client.delete(f"/bot-accounts/sessions/{rid}", headers=rig.as_(owner)).status_code == 200
    assert rig.open(users[10]).status_code == 201


def test_p14_the_ceiling_holds_across_replicas_sharing_redis(rig):
    """Two meeting-api replicas over one Redis: the cap is the deployment's, not each process's."""
    _all(rig, max_browsers=2)
    other = BotAccounts(broker=rig.broker, runtime=rig.runtime, store=RedisSessionStore(rig.redis),
                        admin=rig.ba.admin, upstream=rig.upstream, settings=rig.ba.settings,
                        policy=lambda: rig.policy)
    assert rig.open(1001).status_code == 201
    asyncio.run(other.open_session(1002, {"platform": "google"}))
    from meeting_api.bot_accounts import BotAccountRefused
    with pytest.raises(BotAccountRefused) as e:
        asyncio.run(other.open_session(1003, {"platform": "google"}))
    assert e.value.status == 429
    assert rig.open(1004).status_code == 429


def test_p14_the_seventh_open_in_an_hour_is_a_429(rig):
    _all(rig, opens_per_hour=6)
    acct = rig.seed(f"user:{OWNER}")
    for i in range(6):
        if i % 2:
            r = _check(rig, OWNER, acct["id"])
            rid = r.json()["check_id"]
            rig.client.post(f"/internal/bot-accounts/checks/{rid}",
                            headers={"Authorization": f"Bearer {rig.last_env()['token']}"},
                            json={"logged_in": True, "detail": ""})
        else:
            r = rig.open(OWNER)
            assert rig.client.delete(f"/bot-accounts/sessions/{r.json()['session_id']}",
                                     headers=rig.as_(OWNER)).status_code == 200
        assert r.status_code in (201, 202), (i, r.text)
    before = len(rig.runtime.specs)
    r = rig.open(OWNER)
    assert r.status_code == 429 and "in the last hour" in r.text
    assert _check(rig, OWNER, acct["id"]).status_code == 429
    assert len(rig.runtime.specs) == before
    assert asyncio.run(rig.redis.zcard("botsessions:open")) == 0       # a refused open holds no ceiling place
    assert rig.open(OTHER).status_code == 201                           # per person


def test_p14_the_hourly_window_slides():
    from meeting_api.bot_accounts import InMemorySessionStore

    async def go(store):
        assert all([await store.rate_hit(1, 1000.0 + i, window_s=3600, limit=2) for i in range(2)])
        assert not await store.rate_hit(1, 1100.0, window_s=3600, limit=2)
        assert await store.rate_hit(1, 1000.0 + 3601, window_s=3600, limit=2)
        assert await store.admit("session:a", 1.0, cap=1) and not await store.admit("session:b", 1.0, cap=1)
        await store.unindex("session:a")
        assert await store.admit("session:b", 1.0, cap=1)
    asyncio.run(go(InMemorySessionStore()))
    asyncio.run(go(RedisSessionStore(fakeredis.aioredis.FakeRedis(decode_responses=True))))


def test_p14_settings_read_the_bounds_from_env():
    s = Settings.from_env({})
    assert (s.max_browsers, s.opens_per_hour, s.max_accounts_per_owner, s.max_company_accounts) == (10, 6, 3, 20)
    s = Settings.from_env({"VEXA_BOT_ACCOUNTS_MAX_BROWSERS": "4", "VEXA_BOT_ACCOUNTS_OPENS_PER_HOUR": "2",
                           "VEXA_BOT_ACCOUNTS_MAX_PER_OWNER": "1", "VEXA_BOT_ACCOUNTS_MAX_COMPANY": "7"})
    assert (s.max_browsers, s.opens_per_hour, s.max_accounts_per_owner, s.max_company_accounts) == (4, 2, 1, 7)


def test_p14_at_most_three_saved_accounts_per_owner_and_platform(rig):
    _all(rig)
    for _ in range(3):
        rig.seed(f"user:{OWNER}", platform="google")
    rig.seed(f"user:{OWNER}", platform="zoom")
    rig.seed(f"user:{OTHER}", platform="google")
    before = len(rig.runtime.specs)
    r = rig.open(OWNER)                                                 # a new google account: refused early
    assert r.status_code == 409 and "at most 3" in r.text
    assert len(rig.runtime.specs) == before
    assert rig.open(OWNER, platform="zoom").status_code == 201          # another platform has room
    rig.client.delete(f"/bot-accounts/sessions/{rig.last_env()['session_id']}", headers=rig.as_(OWNER))
    existing = next(a for a in rig.broker.accounts.values() if a["owner"] == f"user:{OWNER}" and a["platform"] == "google")
    assert rig.open(OWNER, account_id=existing["id"]).status_code == 201   # signing one in again is not a new one


def test_p14_the_cap_is_checked_again_at_save(rig):
    _all(rig)
    rig.seed(f"user:{OWNER}", platform="google")
    rig.seed(f"user:{OWNER}", platform="google")
    sid = rig.open(OWNER).json()["session_id"]                          # two saved: room for one
    token = rig.last_env()["token"]
    rig.seed(f"user:{OWNER}", platform="google")                        # a third lands meanwhile
    r = rig.save(sid, token)
    assert r.status_code == 409 and "at most 3" in r.text
    assert sum(1 for a in rig.broker.accounts.values() if a["owner"] == f"user:{OWNER}") == 3


def test_p14_an_explicit_bot_account_respects_the_feature_switch(rig, monkeypatch):
    acct = rig.seed(f"user:{OWNER}", platform="google")
    monkeypatch.setenv("VEXA_FEATURES", "")
    r = rig.client.post("/bots", headers=rig.as_(OWNER),
                        json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij",
                              "bot_account": acct["id"]})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["reason"] == "feature_disabled"
    assert not any("VEXA_BOT_CONFIG" in s["env"] for s in rig.runtime.specs)
    monkeypatch.setenv("VEXA_FEATURES", "browser_bots")
    r = rig.client.post("/bots", headers=rig.as_(OWNER),
                        json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij",
                              "bot_account": acct["id"]})
    assert r.status_code == 201, r.text


# ── P14 review 4493930b6: F1 default path gated, F2 company cap, F3 save lock, F4 cap check after the claim ──
async def test_p14_f1_a_default_bot_is_a_guest_when_browser_bots_is_off(rig, monkeypatch):
    rig.seed(f"user:{OWNER}", platform="zoom")
    rig.seed("company", platform="zoom")
    assert (await rig.ba.resolve_for_spawn(OWNER, "zoom", None, room=None)) is not None
    assert (await rig.ba.resolve_for_spawn(PERSON_ADMIN, "zoom", None, room=None)) is not None
    monkeypatch.setenv("VEXA_FEATURES", "")
    calls = len(rig.broker.calls)
    assert await rig.ba.resolve_for_spawn(OWNER, "zoom", None, room=None) is None             # own account
    assert await rig.ba.resolve_for_spawn(PERSON_ADMIN, "zoom", "default", room=None) is None  # company account
    assert await rig.ba.resolve_for_spawn(PERSON_ADMIN, "google_meet", "default",
                                          room="needs_sign_in") is None                       # auto-join / respawn
    assert len(rig.broker.calls) == calls                                                     # broker not asked


def test_p14_f1_default_post_bots_joins_as_a_guest_when_browser_bots_is_off(rig, monkeypatch):
    rig.seed(f"user:{OWNER}", platform="google")
    monkeypatch.setenv("VEXA_FEATURES", "")
    r = rig.client.post("/bots", headers=rig.as_(OWNER),
                        json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij"})
    assert r.status_code == 201, r.text
    assert "bot_account_id" not in rig.repo._meetings[r.json()["id"]]["data"]


def test_p14_f2_company_accounts_have_their_own_cap(rig):
    _all(rig, max_accounts_per_owner=3, max_company_accounts=5)
    for _ in range(4):
        rig.seed("company", platform="google")
    assert rig.open(PERSON_ADMIN, owner="company").status_code == 201       # 4 < 5: room, though over 3
    rig.client.delete(f"/bot-accounts/sessions/{rig.last_env()['session_id']}", headers=rig.as_(PERSON_ADMIN))
    rig.seed("company", platform="google")
    r = rig.open(PERSON_ADMIN, owner="company")
    assert r.status_code == 409 and "at most 5" in r.text


def test_p14_f2_the_company_default_cap_is_20():
    assert BotAccounts(broker=None, runtime=None)._account_cap("company") == 20
    assert BotAccounts(broker=None, runtime=None)._account_cap(f"user:{OWNER}") == 3


async def test_p14_f3_two_admins_saving_company_accounts_cannot_overshoot(rig, monkeypatch):
    _all(rig, max_company_accounts=3)
    for _ in range(2):
        rig.seed("company", platform="google")
    s1 = await rig.ba.open_session(PERSON_ADMIN, {"platform": "google", "owner": "company"})
    t1 = json.loads(rig.runtime.specs[-1]["env"]["VEXA_BROWSER_SESSION"])["token"]
    s2 = await rig.ba.open_session(ADMIN_ACCOUNT, {"platform": "google", "owner": "company"})
    t2 = json.loads(rig.runtime.specs[-1]["env"]["VEXA_BROWSER_SESSION"])["token"]
    real_put = rig.broker.put

    async def slow_put(*a, **kw):
        await asyncio.sleep(0.1)                  # both saves have counted before either lands, without the lock
        return await real_put(*a, **kw)
    monkeypatch.setattr(rig.broker, "put", slow_put)
    from meeting_api.bot_accounts import BotAccountRefused
    files = list(PROFILE.items())
    out = await asyncio.gather(rig.ba.save_profile(s1["session_id"], t1, files),
                               rig.ba.save_profile(s2["session_id"], t2, files), return_exceptions=True)
    refused = [o for o in out if isinstance(o, BotAccountRefused)]
    assert len(refused) == 1 and refused[0].status == 409 and "at most 3" in str(refused[0].detail)
    assert sum(1 for a in rig.broker.accounts.values() if a["owner"] == "company") == 3
    assert await rig.redis.get("botaccount:savelock:company:google") is None          # released


async def test_p14_f3_the_save_lock_is_per_owner_and_platform_and_token_bound():
    from meeting_api.bot_accounts import InMemorySessionStore
    for store in (InMemorySessionStore(), RedisSessionStore(fakeredis.aioredis.FakeRedis(decode_responses=True))):
        a = await store.lock("company:google", ttl_s=30)
        assert a and await store.lock("company:google", ttl_s=30) is None
        assert await store.lock("company:zoom", ttl_s=30)
        await store.unlock("company:google", "not-the-token")
        assert await store.lock("company:google", ttl_s=30) is None
        await store.unlock("company:google", a)
        assert await store.lock("company:google", ttl_s=30)


def test_p14_f4_the_cap_check_comes_after_the_claim_and_the_hourly_limit(rig):
    _all(rig, opens_per_hour=2)
    for _ in range(3):
        rig.seed(f"user:{OWNER}", platform="google")
    calls = lambda: sum(1 for c in rig.broker.calls if c[0] == "list")
    assert rig.open(OWNER).status_code == 409 and rig.open(OWNER).status_code == 409   # each spends an open
    assert asyncio.run(rig.redis.get(f"botsession:user:{OWNER}")) is None              # the claim is released
    assert asyncio.run(rig.redis.zcard("botsessions:open")) == 0                       # and the ceiling place
    before = calls()
    r = rig.open(OWNER)
    assert r.status_code == 429                                                        # the limit, before the broker
    assert calls() == before
    assert len(rig.runtime.specs) == 0
