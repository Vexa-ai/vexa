"""Every fetch agent-api or the worker makes to a URL somebody else chose goes through one guard
(``shared/ssrf.py`` / ``llm/ssrf.py``, the same bytes), which reads an IPv6 address for the IPv4
address it carries, and every such fetch dials the address it checked.

The table is every notation of a loopback, private or metadata address the guard has to see through;
each consumer — WebFetch, the asset fetch, the repository host check, the model-endpoint gate — must
refuse every row."""
from __future__ import annotations

import httpx
import pytest

from control_plane import model_endpoint, repo_ref
from llm import ssrf as worker_ssrf
from llm import web_tools
from shared import asset_source as assets
from shared import ssrf

INTERNAL = [
    "[::ffff:127.0.0.1]", "[::ffff:a9fe:a9fe]", "[::ffff:10.0.0.1]", "[::127.0.0.1]", "[::a9fe:a9fe]",
    "[::ffff:0:a9fe:a9fe]", "[2002:7f00:1::1]", "[2002:a9fe:a9fe::]", "[2001:0:4136:e378:8000:63bf:3fff:fdd2]",
    "[64:ff9b::7f00:1]", "[64:ff9b::a9fe:a9fe]", "[64:ff9b:1::a00:1]", "[2a00:1450::5efe:a9fe:a9fe]",
    "[fd00:ec2::254]", "[fe80::1]", "[::]", "[::1]",
    "2130706433", "0x7f.0.0.1", "0177.0.0.1", "127.1", "100.100.100.200", "198.18.0.1", "0.0.0.0",
    "a.localhost", "metadata.google.internal",
]


def _public(host):
    return ["93.184.216.34"]


@pytest.mark.parametrize("host", INTERNAL)
def test_webfetch_refuses_every_notation(host):
    assert web_tools.fetch_refusal(f"http://{host}/x", resolve=_public) is not None


@pytest.mark.parametrize("host", INTERNAL)
def test_the_asset_fetch_refuses_every_notation(host):
    assert assets.fetch_refusal(f"http://{host}/a.png", resolve=_public) is not None


@pytest.mark.parametrize("host", INTERNAL)
def test_the_repository_host_check_refuses_every_notation(host):
    assert repo_ref._host_is_internal(host)


@pytest.mark.parametrize("resolved", ["::ffff:127.0.0.1", "64:ff9b::a9fe:a9fe", "2002:a9fe:a9fe::1", "::10.0.0.1"])
def test_a_name_resolving_into_any_notation_is_refused(resolved):
    resolve = lambda h: ["93.184.216.34", resolved]  # noqa: E731
    assert web_tools.fetch_refusal("https://www.example.com/", resolve=resolve) is not None
    assert assets.fetch_refusal("https://www.example.com/a.png", resolve=resolve) is not None


def test_both_images_carry_the_same_guard():
    assert ssrf.__file__ != worker_ssrf.__file__
    with open(ssrf.__file__, "rb") as a, open(worker_ssrf.__file__, "rb") as b:
        assert a.read() == b.read()


def test_a_public_page_is_still_fetchable():
    assert web_tools.fetch_refusal("https://www.aswf.io/", resolve=_public) is None
    assert assets.fetch_refusal("https://upload.wikimedia.org/a.png", resolve=_public) is None
    assert model_endpoint.refuse_reason("https://openrouter.ai/api/v1", env={}) is None


# ── the connection is made to the checked address ────────────────────────────────────────────────

class _Recorder(httpx.BaseTransport):
    def __init__(self):
        self.dialled = []

    def handle_request(self, request):
        self.dialled.append(request)
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG")


def test_a_record_flipped_after_the_check_is_never_dialled(monkeypatch):
    """`fetch_refusal` saw a public address; at connect time the name answers with an internal one.
    The pinned transport re-checks and refuses — the request never reaches a socket."""
    rec = _Recorder()
    monkeypatch.setattr(ssrf, "resolve_host", lambda h: ["::ffff:169.254.169.254"])
    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **k: rec)
    with pytest.raises(assets.AssetFetchError) as exc:
        assets.fetch_asset("https://img.example.com/a.png", resolve=_public)
    assert exc.value.kind == "refused" and rec.dialled == []


def test_webfetch_dials_the_checked_address(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(worker_ssrf, "resolve_host", lambda h: ["93.184.216.34"])
    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **k: rec)
    ok, out = web_tools.web_fetch("https://www.example.com/page", resolve=_public)
    assert ok, out
    assert [str(r.url.host) for r in rec.dialled] == ["93.184.216.34"]
    assert rec.dialled[0].extensions["sni_hostname"] == "www.example.com"


def test_webfetch_refuses_a_rebind_at_connect(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(worker_ssrf, "resolve_host", lambda h: ["64:ff9b::a9fe:a9fe"])
    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **k: rec)
    ok, out = web_tools.web_fetch("https://www.example.com/page", resolve=_public)
    assert not ok and rec.dialled == []
