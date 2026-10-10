"""The rig's sign-in doors: `/login`, start_onboarding/confirm_login, auth_link/auth_claim.

* `/login` signs in only the address its code was mailed to, which step 2 binds; a step-3 form
  naming another address is refused.
* Every door sits behind `VEXA_RIG_OAUTH_ENABLED`, off unless set to 1.
* Codes are capped per address, cumulatively: asking again never resets a count.
* Mailing is capped per source as well as globally, so one caller cannot spend everyone's budget.
* Everything a page reflects is escaped.

Offline: the ASGI app is driven directly, admin-api is the recording stand-in and mail is captured.
"""
from __future__ import annotations

import asyncio
import json
import re
import urllib.parse

import pytest

import vexa_control_mcp as rig
import vexa_oauth
from conftest import as_user, tool

ADMITTED = (200, {"admitted": True, "why": "existing-user"})


def page(method, path, *, query="", form=None, client=("8.8.8.8", 4000), headers=()):
    payload = urllib.parse.urlencode(form).encode() if form is not None else b""
    sent: list = []

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(m):
        sent.append(m)

    scope = {"type": "http", "method": method, "path": path, "query_string": query.encode(),
             "headers": [(b"host", b"localhost:18310"), *headers], "scheme": "http",
             "client": client}
    asyncio.run(rig.app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body.decode()


@pytest.fixture
def mail(monkeypatch):
    sent: list = []
    monkeypatch.setattr(rig, "_send_code", lambda email, code: sent.append((email, code)) or None)
    monkeypatch.setenv(vexa_oauth.ENABLED_ENV, "1")
    for store in (rig.EMAIL_CODES_STORE, rig.LOGINS_STORE, rig.TOKENS_STORE):
        rig.rig_secrets.write(store, {})
    rig._CODE_SENDS.clear()
    rig._SOURCE_SENDS.clear()
    rig.CALL_SOURCE.set("")
    return sent


def _http(monkeypatch, admission=ADMITTED):
    http = as_user(monkeypatch, "7", routes={
        "/internal/signin-admission": admission,
        "/admin/users/email/": (200, {"id": 42}),
        "/admin/users": (200, {"id": 42})})
    rig.CURRENT.set(None)
    return http


def _h(body: str) -> str:
    return re.search(r'name=h value="([^"]*)"', body).group(1)


def _minted() -> dict:
    return {v["email"]: v for v in rig.rig_secrets.read(rig.TOKENS_STORE).values()}


def _wrong(code: str) -> str:
    return "000000" if code != "000000" else "111111"


# ── the code proves the address it was mailed to, and only that one ──────────────────────────────

def test_step_three_cannot_name_another_address(monkeypatch, mail):
    http = _http(monkeypatch)
    _, body = page("POST", "/login", form={"email": "me@example.com"})
    h, code = _h(body), mail[-1][1]
    assert [e for e, _ in mail] == ["me@example.com"]

    status, body = page("POST", "/login", form={"h": h, "code": code,
                                                "email": "founder@example.com"})
    assert "different address" in body
    assert _minted() == {}
    assert not [u for u in http.urls() if "founder" in u], "the other address was looked up"
    assert not rig._logins()[h].get("token")


def test_step_three_signs_in_the_address_step_two_bound(monkeypatch, mail):
    _http(monkeypatch)
    _, body = page("POST", "/login", form={"email": "Me@Example.com"})
    h, code = _h(body), mail[-1][1]
    assert 'name=email' not in body.split("6-digit code is on its way")[1], \
        "the code form must not carry an address for step 3 to read back"

    _, body = page("POST", "/login", form={"h": h, "code": code})
    assert "You're in" in body
    assert set(_minted()) == {"me@example.com"}
    assert rig._logins()[h]["email"] == "me@example.com"


def test_an_agent_link_binds_the_address_too(monkeypatch, mail):
    """auth_link's handle: step 2 binds the address on that record; a step 3 naming another fails
    and the handle's claim stays pending."""
    _http(monkeypatch)
    h = json.loads(tool("auth_link")())["handle"]
    page("POST", "/login", form={"h": h, "email": "me@example.com"})
    code = mail[-1][1]
    page("POST", "/login", form={"h": h, "code": code, "email": "founder@example.com"})
    assert json.loads(tool("auth_claim")(h)).get("pending")

    _, body = page("POST", "/login", form={"h": h, "code": code})
    assert "Approved" in body
    assert json.loads(tool("auth_claim")(h))["signed_in"] == "me@example.com"


def test_a_code_without_its_sign_in_is_refused(monkeypatch, mail):
    _http(monkeypatch)
    page("POST", "/login", form={"email": "me@example.com"})
    _, body = page("POST", "/login", form={"code": mail[-1][1], "email": "me@example.com"})
    assert "expired" in body and _minted() == {}


def test_a_code_mailed_by_another_door_still_proves_only_its_own_address(monkeypatch, mail):
    _http(monkeypatch)
    tool("start_onboarding")("me@example.com")
    code = mail[-1][1]
    _, body = page("POST", "/login", form={"email": "founder@example.com"})
    h = _h(body)
    _, body = page("POST", "/login", form={"h": h, "code": code})
    assert "You're in" not in body and _minted() == {}


# ── a code goes to exactly one address ───────────────────────────────────────────────────────────

#: Strings that are not a single mailbox. Each one was accepted by the old shape check, which
#: refused only whitespace, control characters and a leading or trailing "@".
NOT_ONE_ADDRESS = ["me@example.com,other@example.com", "me@example.com;other@example.com",
                   "<me@example.com>,<other@example.com>", "me@example.com,", '"me"@example.com',
                   "me@@example.com", "me@example", "me@example.com>"]


@pytest.mark.parametrize("email", NOT_ONE_ADDRESS)
def test_the_shape_check_accepts_one_address_only(email):
    assert not rig._plausible_email(email)


@pytest.mark.parametrize("email", ["me@example.com", "Me.Name+tag@mail.example.co.uk",
                                   "o'neil@example.com"])
def test_an_ordinary_address_is_still_one(email):
    assert rig._plausible_email(email)


@pytest.mark.parametrize("email", NOT_ONE_ADDRESS[:3])
def test_no_door_mails_a_code_to_a_list_of_addresses(monkeypatch, mail, email):
    _http(monkeypatch)
    _, body = page("POST", "/login", form={"email": email})
    assert "not an email address" in body
    assert "error" in json.loads(tool("start_onboarding")(email))
    assert mail == [], "a sign-in code was mailed to a list of recipients"


# ── the switch: every door is off unless turned on ───────────────────────────────────────────────

@pytest.mark.parametrize("value", [None, "0", "", "off"])
def test_every_sign_in_door_is_off_unless_switched_on(monkeypatch, mail, value):
    http = _http(monkeypatch)
    if value is None:
        monkeypatch.delenv(vexa_oauth.ENABLED_ENV, raising=False)
    else:
        monkeypatch.setenv(vexa_oauth.ENABLED_ENV, value)

    for method, path, form in (("GET", "/", None), ("GET", "/login", None),
                               ("POST", "/login", {"email": "me@example.com"}),
                               ("POST", "/login", {"h": "x", "code": "123456"}),
                               ("GET", "/login/claim", None), ("GET", "/start", None)):
        status, body = page(method, path, form=form)
        assert status == 404, (method, path, status)
        assert "<form" not in body

    for name, args in (("start_onboarding", ("me@example.com",)),
                       ("confirm_login", ("me@example.com", "123456")),
                       ("auth_link", ()), ("auth_claim", ("x",))):
        out = json.loads(tool(name)(*args))
        assert out["error"] == "sign-in through this server is switched off", name
    assert mail == [] and _minted() == {}
    assert not [c for c in http.calls if "/admin/users" in c["url"]]


def test_a_finished_login_does_not_promote_while_the_doors_are_shut(monkeypatch, mail):
    _http(monkeypatch)
    rig._login_update("h-1", {"exp": 9e12, "token": "vxa_mcp_x", "uid": "42",
                              "email": "me@example.com"}, create=True)
    monkeypatch.delenv(vexa_oauth.ENABLED_ENV, raising=False)
    status, body = page("POST", "/mcp", query="c=h-1")
    assert status == 401 and "invalid_setup_code" in body
    assert "h-1" not in rig._tokens()


# ── per-address caps are cumulative ──────────────────────────────────────────────────────────────

def test_asking_again_neither_remints_nor_resets_the_tries(monkeypatch, mail):
    _http(monkeypatch)
    _, body = page("POST", "/login", form={"email": "me@example.com"})
    h, code = _h(body), mail[-1][1]
    for _ in range(3):
        page("POST", "/login", form={"h": h, "code": _wrong(code)})
    page("POST", "/login", form={"h": h, "email": "me@example.com"})       # step 2 again
    tool("start_onboarding")("me@example.com")                             # another door
    assert len(mail) == 1, "a live code was reminted"

    out = json.loads(tool("confirm_login")("me@example.com", _wrong(code)))
    assert out["attempts_left"] == 1
    out = json.loads(tool("confirm_login")("me@example.com", _wrong(code)))
    assert out["attempts_left"] == 0
    _, body = page("POST", "/login", form={"h": h, "code": code})
    assert "You're in" not in body and _minted() == {}


def test_wrong_tries_are_capped_per_address_across_codes(monkeypatch, mail):
    _http(monkeypatch)
    monkeypatch.setattr(rig, "CODE_ADDRESS_FAIL_CAP", 7)
    tool("start_onboarding")("me@example.com")
    for _ in range(rig.CODE_TRIES):
        tool("confirm_login")("me@example.com", _wrong(mail[-1][1]))
    tool("start_onboarding")("me@example.com")
    assert len(mail) == 2
    left = [json.loads(tool("confirm_login")("me@example.com", _wrong(mail[-1][1])))
            ["attempts_left"] for _ in range(2)]
    assert left == [1, 0], "the second code got a fresh count"
    assert "too many" in json.loads(tool("start_onboarding")("me@example.com"))["error"]
    assert len(mail) == 2


def test_codes_mailed_are_capped_per_address(monkeypatch, mail):
    _http(monkeypatch)
    monkeypatch.setattr(rig, "CODE_ADDRESS_ISSUE_CAP", 2)
    for _ in range(2):
        tool("start_onboarding")("me@example.com")
        assert json.loads(tool("confirm_login")("me@example.com", mail[-1][1]))["token"]
    out = json.loads(tool("start_onboarding")("me@example.com"))
    assert "too many" in out["error"] and len(mail) == 2
    assert rig._issue_email_code("other@example.com") == {"sent": True}


def test_the_login_page_spends_the_mail_budget(monkeypatch, mail):
    _http(monkeypatch)
    page("POST", "/login", form={"email": "me@example.com"})
    assert len(rig._CODE_SENDS) == 1
    monkeypatch.setattr(rig, "CODE_BUDGET", 1)
    _, body = page("POST", "/login", form={"email": "you@example.com"})
    assert "Too many" in body and len(mail) == 1


def test_a_failed_mailing_does_not_leave_a_code_answering_already_sent(monkeypatch, mail):
    _http(monkeypatch)
    monkeypatch.setattr(rig, "_send_code", lambda email, code: "SMTPError: down")
    assert rig._issue_email_code("me@example.com")["refused"] == "mail"
    monkeypatch.setattr(rig, "_send_code", lambda email, code: mail.append((email, code)) or None)
    assert rig._issue_email_code("me@example.com") == {"sent": True}


def test_only_ascii_digits_are_a_code(monkeypatch, mail):
    _http(monkeypatch)
    tool("start_onboarding")("me@example.com")
    arabic = "".join(chr(0x660 + int(d)) for d in mail[-1][1])
    out = json.loads(tool("confirm_login")("me@example.com", arabic))
    assert out["error"] == "wrong code"


# ── per source as well as global ────────────────────────────────────────────────────────────────

def test_one_source_cannot_spend_everyone_s_budget(monkeypatch, mail):
    _http(monkeypatch)
    monkeypatch.setattr(rig, "CODE_SOURCE_BUDGET", 2)
    for i in range(2):
        page("POST", "/login", form={"email": f"a{i}@example.com"}, client=("9.9.9.9", 1))
    _, body = page("POST", "/login", form={"email": "a2@example.com"}, client=("9.9.9.9", 1))
    assert "Too many" in body and len(mail) == 2
    _, body = page("POST", "/login", form={"email": "b@example.com"}, client=("1.0.0.1", 1))
    assert "on its way" in body and len(mail) == 3


def test_the_source_is_the_peer_or_the_named_header_from_a_proxy(monkeypatch):
    fwd = [(b"cf-connecting-ip", b"8.8.4.4"), (b"x-forwarded-for", b"9.9.9.10, 149.112.112.112")]
    public = {"client": ("1.1.1.1", 1), "headers": fwd}
    proxied = {"client": ("127.0.0.1", 1), "headers": fwd}
    monkeypatch.setattr(rig, "CLIENT_ADDRESS_HEADER", "")
    assert rig._client_source(public) == "1.1.1.1"
    assert rig._client_source(proxied) == "", "a proxy we were not told how to read is no source"
    monkeypatch.setattr(rig, "CLIENT_ADDRESS_HEADER", "cf-connecting-ip")
    assert rig._client_source(proxied) == "8.8.4.4"
    assert rig._client_source(public) == "1.1.1.1", "a header from the internet is not trusted"
    monkeypatch.setattr(rig, "CLIENT_ADDRESS_HEADER", "x-forwarded-for")
    assert rig._client_source(proxied) == "149.112.112.112"
    assert rig._client_source({"client": ("10.0.0.2", 1),
                               "headers": [(b"x-forwarded-for", b"not-an-ip")]}) == ""


# ── what a page reflects is escaped ──────────────────────────────────────────────────────────────

def test_the_login_page_escapes_what_it_reflects(monkeypatch, mail):
    _http(monkeypatch)
    _, body = page("GET", "/login", query='h="><script>alert(1)</script>')
    assert "<script>" not in body and "&lt;script&gt;" in body

    hostile = '"><img/src=x/onerror=alert(1)>@example.com'
    _, body = page("POST", "/login", form={"email": hostile})
    assert "<img" not in body                  # not one address: refused, never reflected
    # One address can still carry characters HTML must escape, and the code page reflects it.
    reflected = "o'neil&co@example.com"
    _, body = page("POST", "/login", form={"email": reflected})
    assert reflected not in body and "&amp;co@example.com" in body

    monkeypatch.setattr(rig, "_send_code", lambda email, code: "<script>boom</script>")
    _, body = page("POST", "/login", form={"email": "else@example.com"})
    assert "Could not send" in body and "boom" not in body


def test_page_titles_are_escaped():
    assert b"<script>" not in rig._login_page("<p>x</p>", "<script>a</script>.md")


# ── the mail double holds everyone's mail: a caller sees only their own ─────────────────────────

def _msg(mid, to):
    return {"ID": mid, "From": {"Address": "vexa@vexa.ai"}, "To": [{"Address": a} for a in to],
            "Subject": f"subject {mid}", "Text": f"body {mid}"}


SINK_MSGS = [_msg("m1", ["someone@example.com"]), _msg("m2", ["Me@Example.com"]),
             _msg("m3", ["x@example.com", "me@example.com"]), _msg("m4", ["me@example.com"])]


def _sink_http(monkeypatch, email="me@example.com", read=None):
    http = as_user(monkeypatch, "7", routes={
        "/api/v1/messages": (200, {"total": len(SINK_MSGS), "messages": SINK_MSGS}),
        "/api/v1/message/": (200, read or SINK_MSGS[0])})
    rig.rig_secrets.write(rig.TOKENS_STORE, {"vxa_mcp_t": {"uid": "7", "email": email}} if email
                          else {})
    return http


def test_the_inbox_shows_only_mail_addressed_to_the_caller(monkeypatch):
    _sink_http(monkeypatch)
    out = json.loads(tool("mail_inbox")())
    assert [m["id"] for m in out["messages"]] == ["m2", "m3", "m4"] and out["total"] == 3
    assert [m["id"] for m in json.loads(tool("mail_inbox")(limit=1))["messages"]] == ["m2"]


def test_an_unknown_address_sees_nothing_and_reads_nothing(monkeypatch):
    http = _sink_http(monkeypatch, email="")
    assert json.loads(tool("mail_inbox")()) == {"total": 0, "messages": []}
    assert not http.urls("/api/v1/messages"), "the sink was read for nobody"
    assert json.loads(tool("mail_read")("m4"))["status"] == 404


def test_a_message_to_someone_else_does_not_exist(monkeypatch):
    _sink_http(monkeypatch, read=SINK_MSGS[0])
    out = json.loads(tool("mail_read")("m1"))
    assert out == {"status": 404, "body": "no such message"}


def test_a_message_to_the_caller_reads_and_the_id_stays_one_segment(monkeypatch):
    http = _sink_http(monkeypatch, read=SINK_MSGS[2])
    assert json.loads(tool("mail_read")("m3"))["text"] == "body m3"
    tool("mail_read")("../messages")
    assert http.urls("/api/v1/message/")[-1].endswith("/api/v1/message/..%2Fmessages")


@pytest.mark.parametrize("verb,args", [("mail_inbox", ()), ("mail_read", ("m4",))])
@pytest.mark.parametrize("regime", ["human", "autonomous"])
def test_a_worker_never_reads_its_person_s_mail(monkeypatch, verb, args, regime):
    """Even mail addressed to the person it acts for: that mail holds their sign-in codes."""
    http = _sink_http(monkeypatch, read=SINK_MSGS[3])
    rig.CALL_SCOPE.set({"regime": regime, "workspaces": "*"})
    try:
        out = json.loads(tool(verb)(*args))
    finally:
        rig.CALL_SCOPE.set(None)
    assert out.get("refused") == "delegated", out
    assert not http.urls("/api/v1/"), "the double was read for a worker"


def test_a_worker_token_on_the_call_is_refused_too(monkeypatch):
    http = _sink_http(monkeypatch, read=SINK_MSGS[3])
    rig.CALL_TOKEN.set("vxd_header.payload.signature")
    try:
        out = json.loads(tool("mail_read")("m4"))
    finally:
        rig.CALL_TOKEN.set(None)
    assert out.get("refused") == "delegated", out
    assert not http.urls("/api/v1/")
