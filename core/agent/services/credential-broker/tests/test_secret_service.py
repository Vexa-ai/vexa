"""Custom-service execution: public HTTPS only, DNS pinned, no redirects, credential redacted."""
import json
import socket
from unittest.mock import MagicMock, patch

import pytest

from credential_broker import secret_service


@pytest.mark.parametrize("ips", [("127.0.0.1",), ("169.254.169.254",), ("224.0.0.1",), ("8.8.8.8", "10.0.0.1"), ("::1",),
                                 # an IPv6 address carrying an internal IPv4 one reaches it
                                 ("::ffff:127.0.0.1",), ("::ffff:169.254.169.254",), ("::10.0.0.1",),
                                 ("64:ff9b::a9fe:a9fe",), ("2002:a9fe:a9fe::1",), ("2001:0:4136:e378:8000:63bf:3fff:fdd2",),
                                 ("100.100.100.200",), ("fd00:ec2::254",)])
def test_private_mixed_multicast_and_linklocal_refused(ips):
    with patch.object(socket, "getaddrinfo", return_value=[(0, 0, 0, "", (ip, 443)) for ip in ips]):
        with pytest.raises(secret_service.ServiceError):
            secret_service.public_addresses("fixture.test")


def test_endpoint_and_header_injection_refused():
    for endpoint in ["http://fixture.test/v1", "https://user:pass@fixture.test/v1", "https://fixture.test/v1?token=x", "https://fixture.test/../admin"]:
        with pytest.raises(secret_service.ServiceError):
            secret_service.configure("key", endpoint, "Authorization", "bearer", "GET")
    with pytest.raises(secret_service.ServiceError):
        secret_service.configure("key\nCookie:x", "https://fixture.test/v1", "Authorization", "bearer", "GET")


def test_fixed_endpoint_pinned_ip_redirect_and_secret_redaction():
    config = secret_service.configure("private-token", "https://fixture.test/v1", "Authorization", "bearer", "GET")
    connection = MagicMock()
    response = connection.getresponse.return_value
    response.status = 200
    response.read.return_value = b'{"echo":"private-token"}'
    with patch.object(secret_service, "public_addresses", return_value=["8.8.8.8"]), \
            patch.object(secret_service, "PinnedHTTPS", return_value=connection) as constructor:
        result = secret_service.execute(config, {"q": "fixture"})
        constructor.assert_called_once_with("fixture.test", "8.8.8.8")
        assert "private-token" not in json.dumps(result)
        assert connection.request.call_args.args[:2] == ("GET", "/v1?q=fixture")
        # A User-Agent rides every request (GitHub refuses one without), and cannot displace the credential.
        assert connection.request.call_args.kwargs["headers"]["User-Agent"] == "vexa-credential-broker"
        assert connection.request.call_args.kwargs["headers"]["Authorization"] == "Bearer private-token"
        response.status = 302
        with pytest.raises(secret_service.ServiceError):
            secret_service.execute(config, {})
        with pytest.raises(secret_service.ServiceError):
            secret_service.execute(config, {}, body={"write": True})


def test_only_human_stores_agent_uses_own_reference(signed, connection, store):
    cid = connection("custom_secret", "Fixture")
    path = f"/api/connections/{cid}/custom-secret"
    payload = {"value": "SECRET", "endpoint": "https://fixture.test/v1"}
    assert signed("agent", "POST", path, payload).status_code == 403
    assert not store.puts(cid)
    r = signed("human", "POST", path, payload)
    assert r.status_code == 200 and "SECRET" not in r.text
    with patch.object(secret_service, "execute", return_value={"http_status": 200, "content": {"ok": True}, "untrusted_content": True}) as execute:
        assert signed("agent", "POST", f"/api/connections/{cid}/call", {}, actor="other").status_code == 404
        execute.assert_not_called()
        r = signed("agent", "POST", f"/api/connections/{cid}/call", {})
        assert r.status_code == 200 and r.json()["content"] == {"ok": True}
        assert execute.call_args.args[0]["value"] == "SECRET"
    assert "SECRET" not in signed("agent", "GET", "/api/connections").text


def test_service_failure_is_typed_and_sanitized(signed, connection, store, capsys):
    cid = connection("custom_secret", "Fixture")
    signed("human", "POST", f"/api/connections/{cid}/custom-secret", {"value": "SECRET", "endpoint": "https://fixture.test/v1"})
    with patch.object(secret_service, "public_addresses", side_effect=secret_service.ServiceError("Service must resolve only to public addresses")):
        r = signed("agent", "POST", f"/api/connections/{cid}/call", {})
    assert r.status_code == 409 and "SECRET" not in r.text
    out = capsys.readouterr().out
    assert '"source":"service","kind":"refused"' in out and "SECRET" not in out
