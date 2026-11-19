"""The Models "Test" button probes a person's endpoint only where the endpoint gate means.

The gate admits a host by name: exactly, or through a wildcard. The probe then:
- never follows a redirect (it would carry the person's key and our request to another host);
- reaches a host admitted only by a wildcard through the outbound URL guard, so that name must
  resolve to public addresses.

Both cases use real local HTTP servers; the names resolve to them through a patched resolver.
"""
from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from control_plane import config_test as ct


class _Server:
    def __init__(self, status=200, location=""):
        self.hits = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def _answer(self):
                outer.hits.append((self.command, self.path, dict(self.headers)))
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            do_GET = do_POST = _answer

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def names(monkeypatch):
    """Every *.example.test name resolves to this host's loopback."""
    real = socket.getaddrinfo

    def getaddrinfo(host, port, *a, **kw):
        if str(host).endswith(".example.test"):
            return real("127.0.0.1", port, *a, **kw)
        return real(host, port, *a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


def _cfg(url):
    return {"mode": "custom", "base_url": url, "api_key": "person-key", "model": "m1"}


def test_a_wildcard_admitted_name_that_resolves_to_a_private_address_is_not_reached(names, monkeypatch):
    target = _Server()
    try:
        monkeypatch.setenv("VEXA_MODEL_BASE_URL_ALLOW", "*.example.test")
        out = ct.run_models_test(_cfg(f"http://llm.example.test:{target.port}"), env={})
        assert target.hits == [] and not out["ok"]
    finally:
        target.close()


def test_a_redirect_is_not_followed(names, monkeypatch):
    elsewhere = _Server()
    redirector = _Server(status=302, location=f"http://elsewhere.example.test:{elsewhere.port}/collect")
    try:
        monkeypatch.setenv("VEXA_MODEL_BASE_URL_ALLOW", "llm.example.test")
        out = ct.run_models_test(_cfg(f"http://llm.example.test:{redirector.port}"), env={})
        assert redirector.hits                      # the named host is reached, as named
        assert elsewhere.hits == [] and not out["ok"]
    finally:
        elsewhere.close()
        redirector.close()


def test_an_exactly_named_host_is_reached(names, monkeypatch):
    target = _Server()
    try:
        monkeypatch.setenv("VEXA_MODEL_BASE_URL_ALLOW", "llm.example.test")
        out = ct.run_models_test(_cfg(f"http://llm.example.test:{target.port}"), env={})
        assert out["ok"] and target.hits[0][0] == "POST"
    finally:
        target.close()
