"""M7 (security review 2026-10-17): `/ws?api_key=…` never leaves the key in a log line."""
from __future__ import annotations

import logging

from gateway import log_scrub


def test_a_query_credential_is_redacted_and_the_rest_kept():
    out = log_scrub.scrub('GET /ws?api_key=vxa_secret123&meeting=7 HTTP/1.1')
    assert "vxa_secret123" not in out
    assert "api_key=[redacted]" in out and "meeting=7" in out


def test_the_uvicorn_access_and_websocket_lines_are_rewritten_before_formatting(caplog):
    log_scrub.install()
    log_scrub.install()   # idempotent
    access = logging.getLogger("uvicorn.access")
    assert sum(isinstance(f, log_scrub.QueryCredentialFilter) for f in access.filters) == 1
    with caplog.at_level(logging.INFO):
        access.info('%s - "%s %s HTTP/%s" %d', "1.2.3.4:5", "GET", "/ws?api_key=vxa_k1", "1.1", 101)
        logging.getLogger("uvicorn.error").info('%s - "WebSocket %s" [accepted]', "1.2.3.4:5",
                                                "/vnc/x?token=vxa_k2")
    text = caplog.text
    assert "vxa_k1" not in text and "vxa_k2" not in text
    assert "[redacted]" in text


def test_the_app_installs_it():
    import gateway.app  # noqa: F401
    src = (gateway.app.__file__)
    assert "log_scrub.install()" in open(src).read()
