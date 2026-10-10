"""An upstream that is down is not a refusal the person can act on.

A provider (Google) or a custom service that cannot be reached, or that answers with something
unusable, is an `UpstreamFault` with a `kind`: `unreachable` and `rate_limited` answer 503,
`bad_answer` answers 502, and each is logged as a `broker_fault` line naming its source and kind.
A true refusal — authorization rejected, a permission not granted, bad arguments — stays a 409 and
is logged as a refusal, never as a fault. No response body, token or secret reaches either line.
"""
import http.client
import json
import socket
import time
from unittest.mock import MagicMock, patch
from urllib.parse import urlencode

import httpx
import pytest

from credential_broker import providers, secret_service, service_oauth
from credential_broker.faults import UpstreamFault
from conftest import OWNER

SPEC = {"endpoint": "https://api.example.com/v2/data",
        "oauth": {"authorization_url": "https://login.example.com/authorize", "token_url": "https://api.example.com/token",
                  "scopes": ["read"], "token_auth": "client_secret_post"}, "fields": []}


def lines(capsys, event):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if f'"event":"{event}"' in line]


def raising(exc):
    def handler(req):
        raise exc
    return handler


# ── the adapters classify ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("handler,kind,status", [
    (raising(httpx.ConnectError("down")), "unreachable", 503),
    (raising(httpx.ReadTimeout("slow")), "unreachable", 503),
    (lambda req: httpx.Response(429, text="PRIVATE"), "rate_limited", 503),
    (lambda req: httpx.Response(500, text="PRIVATE"), "bad_answer", 502),
    (lambda req: httpx.Response(503, text="PRIVATE"), "bad_answer", 502),
    (lambda req: httpx.Response(200, text="PRIVATE not json"), "bad_answer", 502),
])
def test_account_read_faults_are_typed(handler, kind, status):
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(UpstreamFault) as e:
            providers.read_account("google_email", {"access_token": "private"}, "gmail.search", http=c)
    assert (e.value.source, e.value.kind, e.value.status) == ("provider", kind, status)
    assert "PRIVATE" not in str(e.value) and "reconnect" not in str(e.value).lower()


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_account_read_refusals_stay_refusals(code):
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(code, text="PRIVATE"))) as c:
        with pytest.raises(providers.ProviderError):
            providers.read_account("google_email", {"access_token": "private"}, "gmail.search", http=c)


@pytest.mark.parametrize("handler,kind", [
    (raising(httpx.ConnectError("down")), "unreachable"),
    (lambda req: httpx.Response(500, text="PRIVATE"), "bad_answer"),
    (lambda req: httpx.Response(429, text="PRIVATE"), "rate_limited"),
    (lambda req: httpx.Response(200, json={"token_type": "bearer"}), "bad_answer"),
])
def test_token_exchange_faults_are_typed(handler, kind):
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(UpstreamFault) as e:
            providers.tokens("google_email", {"client_id": "i", "client_secret": "s"}, refresh="r", http=c)
    assert (e.value.source, e.value.kind) == ("provider", kind)


def test_a_rejected_grant_is_still_a_reconnect():
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(400, json={"error": "invalid_grant"}))) as c:
        with pytest.raises(providers.ProviderError) as e:
            providers.tokens("google_email", {"client_id": "i", "client_secret": "s"}, refresh="r", http=c)
    assert "reconnect" in str(e.value)


@pytest.mark.parametrize("handler,kind", [
    (raising(httpx.ConnectError("down")), "unreachable"),
    (lambda req: httpx.Response(500, text="PRIVATE"), "bad_answer"),
    (lambda req: httpx.Response(200, json={}), "bad_answer"),
])
def test_draft_faults_are_typed_and_say_the_outcome_is_unknown(handler, kind):
    value = {"access_token": "token", "scope": providers.DRAFT_SCOPE}
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(UpstreamFault) as e:
            providers.create_gmail_draft(value, "test@example.test", "s", "b", http=c)
    assert e.value.kind == kind and "unknown" in str(e.value)


def test_custom_service_transport_faults_are_typed():
    conn = MagicMock()
    conn.request.side_effect = ConnectionRefusedError("refused")
    config = secret_service.configure("SECRET", "https://api.example.com/v1", "Authorization", "bearer", "GET")
    with patch.object(secret_service, "public_addresses", return_value=["93.184.216.34"]), \
            patch.object(secret_service, "PinnedHTTPS", return_value=conn):
        with pytest.raises(UpstreamFault) as e:
            secret_service.execute(config, {})
        assert (e.value.source, e.value.kind, e.value.status) == ("service", "unreachable", 503)
        conn.request.side_effect = None
        conn.getresponse.side_effect = http.client.BadStatusLine("garbage")
        with pytest.raises(UpstreamFault) as e:
            secret_service.execute(config, {})
        assert (e.value.kind, e.value.status) == ("bad_answer", 502)
    assert "SECRET" not in str(e.value)


def test_name_resolution_outage_is_a_fault_and_a_missing_host_is_a_refusal():
    with patch.object(secret_service.socket, "getaddrinfo", side_effect=socket.gaierror(socket.EAI_AGAIN, "again")):
        with pytest.raises(UpstreamFault) as e:
            secret_service.public_addresses("api.example.com")
    assert e.value.kind == "unreachable"
    with patch.object(secret_service.socket, "getaddrinfo", side_effect=socket.gaierror(socket.EAI_NONAME, "none")):
        with pytest.raises(secret_service.ServiceError):
            secret_service.public_addresses("api.example.invalid")


@pytest.mark.parametrize("setup,kind", [
    (lambda conn: setattr(conn.request, "side_effect", TimeoutError("slow")), "unreachable"),
    (lambda conn: setattr(conn.getresponse.return_value, "status", 502), "bad_answer"),
])
def test_custom_oauth_exchange_faults_are_typed(setup, kind):
    conn = MagicMock()
    setup(conn)
    with patch.object(secret_service, "public_addresses", return_value=["93.184.216.34"]), \
            patch.object(secret_service, "PinnedHTTPS", return_value=conn):
        with pytest.raises(UpstreamFault) as e:
            service_oauth.exchange(SPEC, {"client_id": "id", "client_secret": "private"}, refresh="old")
    assert (e.value.source, e.value.kind) == ("service", kind)


def test_custom_oauth_rejection_stays_a_refusal():
    conn = MagicMock()
    conn.getresponse.return_value.status = 400
    with patch.object(secret_service, "public_addresses", return_value=["93.184.216.34"]), \
            patch.object(secret_service, "PinnedHTTPS", return_value=conn):
        with pytest.raises(secret_service.ServiceError):
            service_oauth.exchange(SPEC, {"client_id": "id", "client_secret": "private"}, refresh="old")


# ── the routes answer and log them distinctly ─────────────────────────────────────────────────
@pytest.fixture
def gmail(connection, ready, store):
    cid = connection("google_email")
    store.put(cid, {"owner": OWNER, "value": {"access_token": "SECRET", "expires_at": time.time() + 3600, "scope": providers.DRAFT_SCOPE}})
    ready(cid)
    return cid


@pytest.mark.parametrize("kind,status", [("unreachable", 503), ("rate_limited", 503), ("bad_answer", 502)])
def test_a_provider_fault_on_a_read_is_a_typed_5xx(signed, gmail, broker, capsys, kind, status):
    capsys.readouterr()
    with patch.object(providers, "read_account", side_effect=UpstreamFault("provider", kind, "Account read is unavailable; retry later")):
        r = signed("agent", "POST", f"/api/connections/{gmail}/read", {"action": "gmail.search"})
    assert r.status_code == status and r.json() == {"detail": "Account read is unavailable; retry later"}
    out = capsys.readouterr().out
    faults = [json.loads(line) for line in out.splitlines() if '"event":"broker_fault"' in line]
    assert faults[-1]["fields"] == {"source": "provider", "kind": kind, "route": "/api/connections/{cid}/read"}
    assert '"event":"upstream_refused"' not in out and "SECRET" not in out
    audit = broker.sql("SELECT outcome FROM audit WHERE connection=? AND action='gmail.search'", (gmail,), rows=True)
    assert audit[-1]["outcome"] == "failed"


def test_a_provider_refusal_is_a_409_logged_as_a_refusal(signed, gmail, capsys):
    capsys.readouterr()
    with patch.object(providers, "read_account", side_effect=providers.ProviderError("Authorization rejected; reconnect this account")):
        r = signed("agent", "POST", f"/api/connections/{gmail}/read", {"action": "gmail.search"})
    assert r.status_code == 409
    out = capsys.readouterr().out
    refused = [json.loads(line) for line in out.splitlines() if '"event":"upstream_refused"' in line]
    assert refused[-1]["fields"] == {"source": "provider", "kind": "refused", "route": "/api/connections/{cid}/read"}
    assert '"event":"broker_fault"' not in out


def test_a_draft_fault_leaves_the_outcome_unknown(signed, gmail, broker):
    payload = {"request_id": "fixture-draft-0001", "recipient": "test@example.test", "subject": "s", "body": "b"}
    with patch.object(providers, "create_gmail_draft",
                      side_effect=UpstreamFault("provider", "unreachable", "Draft outcome unknown; check Gmail Drafts before retrying")):
        r = signed("agent", "POST", f"/api/connections/{gmail}/draft", payload)
    assert r.status_code == 503
    audit = broker.sql("SELECT outcome FROM audit WHERE action='gmail.draft'", rows=True)
    assert audit[-1]["outcome"] == "unknown"
    again = signed("agent", "POST", f"/api/connections/{gmail}/draft", payload)
    assert again.status_code == 409 and "unknown" in again.json()["detail"]


def test_a_custom_service_fault_is_a_typed_5xx(signed, connection, capsys):
    cid = connection("custom_secret", "Fixture")
    signed("human", "POST", f"/api/connections/{cid}/custom-secret", {"value": "SECRET", "endpoint": "https://fixture.test/v1"})
    capsys.readouterr()
    with patch.object(secret_service, "execute", side_effect=UpstreamFault("service", "bad_answer", "Service answered with an unreadable response")):
        r = signed("agent", "POST", f"/api/connections/{cid}/call", {})
    assert r.status_code == 502
    out = capsys.readouterr().out
    assert '"source":"service","kind":"bad_answer"' in out and "SECRET" not in out


def test_a_consent_exchange_fault_is_logged_by_kind(signed, connection, capsys):
    from urllib.parse import parse_qs, urlsplit
    cid = connection("google_calendar")
    q = parse_qs(urlsplit(signed("human", "POST", f"/api/connections/{cid}/authorize").json()["authorize_url"]).query)
    capsys.readouterr()
    with patch.object(providers, "tokens", side_effect=UpstreamFault("provider", "unreachable", "Provider authorization is unavailable; try again later")):
        r = signed("human", "GET", "/api/auth/callback/google?" + urlencode({"state": q["state"][0], "code": "c"}))
    assert r.json() == {"connection_id": cid, "status": "refused"}
    out = capsys.readouterr().out
    assert '"source":"provider","kind":"unreachable"' in out
