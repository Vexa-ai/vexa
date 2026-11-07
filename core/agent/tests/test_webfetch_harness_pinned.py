"""WebFetch in the openai-agent harness connects to the address the guard checked (R6-8).

`web_tools.fetch_refusal` checks the URL the model chose; the connection must then land on an
address that passed the same check — re-resolved and re-checked at connect time and dialled by
address, so a name that answers differently a second time (a rebind) reaches nothing internal. The
harness hands `web_fetch` its own client, so that client's transport is the one that decides.

No network: the transport that dials is a recorder (`httpx.HTTPTransport`, both where `web_tools`
builds one and where an httpx client builds its default), and DNS is played by the test.
"""
from __future__ import annotations

import json

import httpx
import pytest

from llm import ssrf as worker_ssrf
from llm import web_tools
from llm.openai_agent import OpenAIAgentHarness, _Sandbox, run_builtin

PUBLIC = "93.184.216.34"


class _Recorder(httpx.BaseTransport):
    def __init__(self):
        self.dialled: list[httpx.Request] = []

    def handle_request(self, request):
        self.dialled.append(request)
        return httpx.Response(200, headers={"content-type": "text/html"},
                              content=b"<html><title>ok</title><body>ok</body></html>")


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **k: rec)
    monkeypatch.setattr(httpx._client, "HTTPTransport", lambda *a, **k: rec)   # a client's default
    monkeypatch.delenv("VEXA_SEARCH_URL", raising=False)
    return rec


def _dns(monkeypatch, answers: list[list[str]]):
    """Each lookup returns the next answer — the URL check first, the connect second."""
    seq = iter(answers)
    monkeypatch.setattr(worker_ssrf, "resolve_host", lambda host: next(seq))


def _fetch(tmp_path):
    harness = OpenAIAgentHarness(base_url="http://model.invalid/v1", model="m")
    return run_builtin("WebFetch", {"url": "https://www.example.com/page"},
                       _Sandbox([], tmp_path), web=harness._web)


def test_the_harness_dials_the_checked_address(tmp_path, monkeypatch, recorder):
    _dns(monkeypatch, [[PUBLIC], [PUBLIC]])
    ok, out = _fetch(tmp_path)
    assert ok, out
    assert [r.url.host for r in recorder.dialled] == [PUBLIC]
    assert recorder.dialled[0].extensions["sni_hostname"] == "www.example.com"
    assert json.loads(out)["title"] == "ok"


@pytest.mark.parametrize("internal", ["169.254.169.254", "10.0.0.7", "127.0.0.1",
                                      "::ffff:169.254.169.254"])
def test_a_name_that_rebinds_after_the_check_reaches_nothing(tmp_path, monkeypatch, recorder, internal):
    _dns(monkeypatch, [[PUBLIC], [internal]])
    ok, out = _fetch(tmp_path)
    assert not ok and recorder.dialled == [], "the harness dialled after a rebind"
