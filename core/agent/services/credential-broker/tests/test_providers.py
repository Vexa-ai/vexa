"""Google OAuth adapters and the consent routes: PKCE, scopes, session-bound single-use state, and
no credential in any response. Offline: every provider call is a MockTransport or a patch."""
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from credential_broker import providers
from credential_broker.faults import UpstreamFault
from conftest import GOOGLE


def authorize(signed, cid, session="browser-session"):
    r = signed("human", "POST", f"/api/connections/{cid}/authorize", session=session)
    assert r.status_code == 200, r.text
    return parse_qs(urlsplit(r.json()["authorize_url"]).query)


@pytest.mark.parametrize("provider", ["google_email", "google_calendar"])
def test_oauth_start_uses_pkce_fixed_scopes_and_no_secret(signed, connection, broker, provider):
    cid = connection(provider)
    q = authorize(signed, cid)
    assert q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == ["https://app.example.test/api/auth/callback/google"]
    assert q["scope"] == [" ".join(providers.CATALOG[provider]["scopes"])]
    assert "client_secret" not in q and "code_verifier" not in q
    assert q["code_challenge"] == [broker.pkce(q["state"][0])[1]]


def test_missing_application_refuses_to_start(signed, connection, broker):
    object.__setattr__(broker.settings, "google_client_id", "")
    cid = connection("google_email")
    r = signed("human", "POST", f"/api/connections/{cid}/authorize")
    assert r.status_code == 409 and "authorize_url" not in r.text
    assert r.json()["detail"] == "Provider application is not configured on this deployment"


def test_missing_redirect_is_a_typed_503(signed, connection, broker, capsys):
    object.__setattr__(broker.settings, "product_redirect", "")
    cid = connection("google_email")
    assert signed("human", "POST", f"/api/connections/{cid}/authorize").status_code == 503
    assert '"kind":"oauth_redirect_unset"' in capsys.readouterr().out


def test_the_google_application_comes_from_configuration_only(signed, connection, broker, store):
    """VEXA_CONNECTIONS_GOOGLE_CLIENT_ID/SECRET are the one declared source. A store record a
    development harness once wrote is not a second, undeclared one: it is never read, whether or
    not the deployment configured an application."""
    store.put("operator-google", {"client_id": "harness-client", "client_secret": "harness-secret"})
    q = authorize(signed, connection("google_calendar"))
    assert q["client_id"] == [GOOGLE["client_id"]]
    object.__setattr__(broker.settings, "google_client_id", "")
    r = signed("human", "POST", f"/api/connections/{connection('google_email')}/authorize")
    assert r.status_code == 409
    assert r.json()["detail"] == "Provider application is not configured on this deployment"
    assert not [c for c in store.calls if c[0] == "get" and c[1] == "operator-google"]


def test_callback_stores_tokens_and_returns_status_only(signed, connection, store):
    cid = connection("google_email")
    q = authorize(signed, cid)
    path = "/api/auth/callback/google?" + urlencode({"state": q["state"][0], "code": "fixture-code"})
    token = {"access_token": "fixture-user-secret", "refresh_token": "fixture-refresh-secret",
             "expires_at": 99999999999, "scope": providers.READ_SCOPE}
    with patch.object(providers, "tokens", return_value=token) as exchange, \
            patch.object(providers, "account_email", return_value="person@example.test"):
        r = signed("human", "GET", path)
    assert r.json() == {"connection_id": cid, "status": "connected"}
    assert exchange.call_args.kwargs["redirect"] == "https://app.example.test/api/auth/callback/google"
    assert store.rows[cid][-1] == {"value": token}
    listed = signed("human", "GET", "/api/connections").text
    assert "fixture-user-secret" not in listed and "person@example.test" in listed


def test_denied_consent_never_connects(signed, connection, store):
    cid = connection("google_calendar")
    q = authorize(signed, cid)
    r = signed("human", "GET", "/api/auth/callback/google?" + urlencode({"state": q["state"][0], "error": "access_denied"}))
    assert r.json() == {"connection_id": cid, "status": "refused"}
    assert not store.puts(cid)
    assert signed("human", "GET", "/api/connections").json()["connections"][0]["status"] == "awaiting_user"


def test_failed_exchange_is_refused_not_raised(signed, connection, capsys):
    cid = connection("google_calendar")
    q = authorize(signed, cid)
    with patch.object(providers, "tokens", side_effect=providers.ProviderError("Authorization failed; reconnect this account")):
        r = signed("human", "GET", "/api/auth/callback/google?" + urlencode({"state": q["state"][0], "code": "c"}))
    assert r.json()["status"] == "refused"
    assert '"kind":"exchange_refused"' in capsys.readouterr().out


def test_tokens_sanitized_and_scopes_checked():
    # A grant without the requested scopes is the person's to fix (a refusal); a 200 without a
    # usable token is the provider answering badly (a fault). Neither echoes the body.
    for data, raised in [({"access_token": "secret", "token_type": "Bearer", "scope": "wrong", "expires_in": 3600},
                          providers.ProviderError),
                         ({"error": "secret-echo"}, UpstreamFault)]:
        http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=data)))
        with pytest.raises(raised) as err:
            providers.tokens("google_email", GOOGLE, code="fixture-code", http=http)
        assert "secret" not in str(err.value)


def test_refresh_rotation():
    seen = []

    def wire(r):
        seen.append(parse_qs(r.content.decode()))
        return httpx.Response(200, json={"access_token": "new-access", "refresh_token": "new-refresh",
                                         "token_type": "Bearer", "expires_in": 3600})
    value = providers.tokens("google_calendar", GOOGLE, refresh="old-refresh",
                             http=httpx.Client(transport=httpx.MockTransport(wire)))
    assert value["refresh_token"] == "new-refresh"
    assert seen[0]["grant_type"] == ["refresh_token"] and seen[0]["refresh_token"] == ["old-refresh"]


def test_disconnect_blocks_use(signed, connection, ready):
    cid = connection("custom_secret")
    ready(cid)
    assert signed("human", "POST", f"/api/connections/{cid}/disconnect").json()["status"] == "disconnected"
    assert signed("agent", "POST", f"/api/connections/{cid}/call", {}).status_code == 409


def test_oauth_provider_cannot_take_a_pasted_secret(signed, connection):
    cid = connection("google_calendar")
    assert signed("human", "POST", f"/api/connections/{cid}/custom-secret", {"value": "raw-secret"}).status_code == 409


def test_invalid_secret_error_does_not_echo(signed, connection):
    cid = connection("custom_secret")
    r = signed("human", "POST", f"/api/connections/{cid}/custom-secret",
               {"value": "private-secret\nCookie: x", "endpoint": "https://api.example.test/v1"})
    assert r.status_code == 400 and "private-secret" not in r.text
